import os, time, datetime as dt, sys, traceback
from zoneinfo import ZoneInfo
from telegram import Bot, error as tg_error
from nba_api.stats.endpoints import leaguestandingsv3
from requests.exceptions import HTTPError, ReadTimeout, ConnectionError as ReqConnError

TEAM_RU = {
    "ATL":"Атланта","BOS":"Бостон","BKN":"Бруклин","CHA":"Шарлотт","CHI":"Чикаго",
    "CLE":"Кливленд","DAL":"Даллас","DEN":"Денвер","DET":"Детройт","GSW":"Голден Стэйт",
    "HOU":"Хьюстон","IND":"Индиана","LAC":"Клипперс","LAL":"Лейкерс","MEM":"Мемфис",
    "MIA":"Майами","MIL":"Милуоки","MIN":"Миннесота","NOP":"Нью-Орлеан","NYK":"Нью-Йорк",
    "OKC":"Оклахома-Сити","ORL":"Орландо","PHI":"Филадельфия","PHX":"Финикс",
    "POR":"Портленд","SAC":"Сакраменто","SAS":"Сан-Антонио","TOR":"Торонто","UTA":"Юта","WAS":"Вашингтон"
}

def msk_now(): return dt.datetime.now(ZoneInfo("Europe/Moscow"))

def format_pct(x):
    try: return f"{float(x):.3f}".lstrip("0")
    except: return str(x)

def gb(leader_w, leader_l, w, l):
    return ((leader_w - w) + (l - leader_l)) / 2.0

def fetch_standings_with_retry(retries=4, pause=3):
    last_err = None
    for i in range(1, retries+1):
        try:
            df = leaguestandingsv3.LeagueStandingsV3().standings.get_data_frame()
            df = df.rename(columns={
                "TeamTricode":"tri","TeamName":"name","Conference":"conf","ConferenceRank":"rank_conf",
                "W":"w","L":"l","W_PCT":"pct","L10":"l10","STRK":"streak"
            })[["tri","name","conf","rank_conf","w","l","pct","l10","streak"]]
            df["rank_conf"] = df["rank_conf"].astype(int)
            df["w"] = df["w"].astype(int)
            df["l"] = df["l"].astype(int)
            print(f"✅ Standings fetched on attempt {i}")
            return df
        except (HTTPError, ReadTimeout, ReqConnError) as e:
            last_err = e
            print(f"⚠️  NBA fetch failed (attempt {i}/{retries}): {e}")
            time.sleep(pause)
        except Exception as e:
            last_err = e
            print("❌ Unexpected error while fetching standings:")
            traceback.print_exc()
            break
    raise SystemExit(f"Failed to fetch standings after {retries} attempts: {last_err}")

def build_block(df_conf, title):
    df_conf = df_conf.sort_values("rank_conf")
    lw, ll = int(df_conf.iloc[0]["w"]), int(df_conf.iloc[0]["l"])
    lines = [title]
    for _, r in df_conf.iterrows():
        name_ru = TEAM_RU.get(r["tri"], r["name"])
        w, l = int(r["w"]), int(r["l"])
        pct = format_pct(r["pct"])
        gb_str = f"{gb(lw, ll, w, l):.1f}"
        streak = r["streak"] if r["streak"] else "-"
        l10 = r["l10"] if r["l10"] else "-"
        lines.append(f"{int(r['rank_conf'])}) {name_ru} {w}–{l} ({pct}) GB {gb_str} — {streak} — L10: {l10}")
    return "\n".join(lines)

def build_message(df):
    months = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    now = msk_now()
    head = f"НБА • Таблица на {now.day} {months[now.month-1]}"
    east = df[df["conf"].str.upper().str.startswith("E")]
    west = df[df["conf"].str.upper().str.startswith("W")]
    return f"{head}\n{build_block(east,'Восток')}\n\n{build_block(west,'Запад')}"

def main():
    token = os.getenv("TG_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    dry = os.getenv("DRY_RUN","0") == "1"

    if not token: raise SystemExit("❌ TG_TOKEN is empty. Добавь секрет TG_TOKEN в репозиторий.")
    if not chat_id: raise SystemExit("❌ TG_CHAT_ID is empty. Добавь секрет TG_CHAT_ID (формат -100...).")
    if not str(chat_id).strip().startswith("-100"):
        raise SystemExit("❌ TG_CHAT_ID должен начинаться с -100 (id канала). Проверь значение.")

    print("🔧 Fetching standings...")
    df = fetch_standings_with_retry()

    msg = build_message(df)
    print("📤 Message preview:\n" + ("\n".join(msg.splitlines()[:6]) + "\n..."))  # первые строки, чтобы видеть формат

    if dry:
        print("DRY_RUN=1 → публикация пропущена.")
        return

    bot = Bot(token)
    try:
        bot.send_message(chat_id=chat_id, text=msg, disable_web_page_preview=True)
        print("✅ Sent to Telegram.")
    except tg_error.Unauthorized as e:
        raise SystemExit("❌ Telegram Unauthorized: неверный TG_TOKEN (проверь токен у BotFather).")
    except tg_error.BadRequest as e:
        raise SystemExit("❌ Telegram BadRequest: " + str(e) + " — проверь TG_CHAT_ID и что бот админ канала.")
    except Exception as e:
        print("❌ Unexpected Telegram error:")
        traceback.print_exc()
        raise

if __name__ == "__main__":
    main()
