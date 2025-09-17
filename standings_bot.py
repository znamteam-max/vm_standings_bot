import os, math, datetime as dt
from zoneinfo import ZoneInfo
from telegram import Bot
from nba_api.stats.endpoints import leaguestandingsv3

TELEGRAM_TOKEN = os.getenv("TG_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")

TEAM_RU = {
    "ATL":"Атланта","BOS":"Бостон","BKN":"Бруклин","CHA":"Шарлотт","CHI":"Чикаго",
    "CLE":"Кливленд","DAL":"Даллас","DEN":"Денвер","DET":"Детройт","GSW":"Голден Стэйт",
    "HOU":"Хьюстон","IND":"Индиана","LAC":"Клипперс","LAL":"Лейкерс","MEM":"Мемфис",
    "MIA":"Майами","MIL":"Милуоки","MIN":"Миннесота","NOP":"Нью-Орлеан","NYK":"Нью-Йорк",
    "OKC":"Оклахома-Сити","ORL":"Орландо","PHI":"Филадельфия","PHX":"Финикс",
    "POR":"Портленд","SAC":"Сакраменто","SAS":"Сан-Антонио","TOR":"Торонто","UTA":"Юта","WAS":"Вашингтон"
}

def msk_today():
    return dt.datetime.now(ZoneInfo("Europe/Moscow"))

def format_pct(x):
    try:
        return f"{float(x):.3f}".lstrip("0")
    except:
        return str(x)

def gb(leader_w, leader_l, w, l):
    return ((leader_w - w) + (l - leader_l)) / 2.0

def fetch_standings():
    df = leaguestandingsv3.LeagueStandingsV3().standings.get_data_frame()
    df = df.rename(columns={
        "TeamTricode":"tri","TeamName":"name","Conference":"conf","ConferenceRank":"rank_conf",
        "W":"w","L":"l","W_PCT":"pct","L10":"l10","STRK":"streak"
    })[["tri","name","conf","rank_conf","w","l","pct","l10","streak"]]
    df["rank_conf"] = df["rank_conf"].astype(int)
    df["w"] = df["w"].astype(int)
    df["l"] = df["l"].astype(int)
    return df

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
    now = msk_today()
    head = f"НБА • Таблица на {now.day} {months[now.month-1]}"
    east = df[df["conf"].str.upper().str.startswith("E")]
    west = df[df["conf"].str.upper().str.startswith("W")]
    return f"{head}\n{build_block(east,'Восток')}\n\n{build_block(west,'Запад')}"

def main():
    bot = Bot(TELEGRAM_TOKEN)
    df = fetch_standings()
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=build_message(df), disable_web_page_preview=True)

if __name__ == "__main__":
    main()
