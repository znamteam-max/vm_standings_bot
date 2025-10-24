import os
import sys
import json
import math
import datetime as dt
from zoneinfo import ZoneInfo
from html import escape
from typing import Dict, List, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ====== Telegram ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

TZ = ZoneInfo("Europe/Helsinki")  # твой часовой пояс
USER_AGENT = "NBA-Standings-Bot/1.0 (+https://espn.com, +https://basketball-reference.com)"

# ====== HTTP с ретраями ======
def make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=6, connect=6, read=6, backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"], raise_on_status=False
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({"User-Agent": USER_AGENT})
    return s

SESSION = make_session()

# ====== Утилиты ======
def season_end_year(today: dt.date) -> int:
    """
    NBA сезон обозначается годом завершения.
    Если месяц >= 8 (август) — считаем, что сезон нового года (октябрь старт),
    иначе — прошлогодний сезон.
    """
    return today.year + 1 if today.month >= 8 else today.year

def norm_team_key(name: str) -> str:
    """Приводим имя к ключу для сопоставления между сайтами."""
    return "".join(ch for ch in name.lower() if ch.isalnum())

def pct(w: int, l: int) -> float:
    g = w + l
    return (w / g) if g > 0 else 0.0

def arrow(delta_places: Optional[int]) -> str:
    """
    Возвращает стрелку тренда для места:
      >0  -> 🟢▲+N
      <0  -> 🔴▼N
      ==0 -> ⚪︎=
      None -> ⚪︎=
    """
    if delta_places is None:
        return "⚪︎="
    if delta_places > 0:
        return f"🟢▲+{delta_places}"
    if delta_places < 0:
        return f"🔴▼{abs(delta_places)}"
    return "⚪︎="

# ====== Парсим ESPN (текущие standings) ======
def fetch_espn_standings_html() -> Dict[str, List[Dict]]:
    """
    Парсит https://www.espn.com/nba/standings (конференции).
    Возвращает словарь:
      {
        "east": [ { "team": "...", "abbr": "...", "w": int, "l": int, "pct": float }, ... ],
        "west": [ ... ]
      }
    """
    url = "https://www.espn.com/nba/standings"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Найдём заголовки "Eastern Conference" и "Western Conference"
    # и ближайшие после них таблицы
    def parse_conference(title_text: str) -> List[Dict]:
        header = soup.find(lambda tag: tag.name in ("h2", "h3") and title_text in tag.get_text(strip=True))
        if not header:
            # fallback: поиск по тексту
            header = soup.find(string=lambda t: t and title_text in t)
            header = header.parent if header else None
        if not header:
            return []
        table = header.find_next("table")
        if not table:
            return []

        # определим индексы столбцов W, L, PCT по заголовку
        thead = table.find("thead")
        tbody = table.find("tbody")
        if not thead or not tbody:
            return []

        ths = [th.get_text(strip=True).upper() for th in thead.find_all("th")]
        # иногда ESPN дублирует "TEAM" лево/право; нам нужны W, L, PCT
        try:
            w_idx = ths.index("W")
            l_idx = ths.index("L")
        except ValueError:
            # иногда шапка может быть составной; попробуем альтернативы
            w_idx = next((i for i, t in enumerate(ths) if t.startswith("W")), None)
            l_idx = next((i for i, t in enumerate(ths) if t.startswith("L")), None)
        try:
            pct_idx = ths.index("PCT")
        except ValueError:
            pct_idx = next((i for i, t in enumerate(ths) if "PCT" in t), None)

        rows_out = []
        for tr in tbody.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < max( (w_idx or 0), (l_idx or 0), (pct_idx or 0) ) + 1:
                continue

            # название команды — берём текст первой ячейки, в ней есть <a> с названием
            team_cell = tds[0]
            team_a = team_cell.find("a")
            team_name = team_a.get_text(strip=True) if team_a else team_cell.get_text(strip=True)
            abbr_span = team_cell.find("abbr")
            team_abbr = abbr_span.get_text(strip=True) if abbr_span else ""

            def safe_int(x):
                try:
                    return int(str(x).strip())
                except Exception:
                    return 0

            w = safe_int(tds[w_idx].get_text()) if w_idx is not None else 0
            l = safe_int(tds[l_idx].get_text()) if l_idx is not None else 0

            if pct_idx is not None:
                try:
                    pct_val = float(tds[pct_idx].get_text().strip())
                except Exception:
                    pct_val = pct(w, l)
            else:
                pct_val = pct(w, l)

            rows_out.append({
                "team": team_name,
                "abbr": team_abbr,
                "w": w,
                "l": l,
                "pct": pct_val
            })

        # Ранг по порядку строк
        # Отсортируем по pct, затем по победам (на случай плохого порядка)
        rows_out.sort(key=lambda x: (-x["pct"], -x["w"]))
        return rows_out

    east = parse_conference("Eastern Conference")
    west = parse_conference("Western Conference")
    return {"east": east, "west": west}

# ====== Вчерашние места (Basketball-Reference) ======
def fetch_bbr_positions_yesterday(today: dt.date) -> Dict[str, Dict[str, int]]:
    """
    Возвращает словарь с позициями команд на вчера:
      { "east": { team_key: rank, ... }, "west": { ... } }
    Используем страницу-калькулятор по дате:
      https://www.basketball-reference.com/friv/standings.fcgi?month=MM&day=DD&year=YYYY
    Если страница/парсинг недоступны — вернём пустые словари.
    """
    yday = today - dt.timedelta(days=1)
    url = f"https://www.basketball-reference.com/friv/standings.fcgi?month={yday.month}&day={yday.day}&year={yday.year}"
    try:
        r = SESSION.get(url, timeout=30)
        if r.status_code != 200 or not r.text:
            return {"east": {}, "west": {}}
        soup = BeautifulSoup(r.text, "html.parser")

        # Ищем блоки с подзаголовками "Eastern Conference" / "Western Conference" и ближайшие таблицы
        def extract_positions(title_text: str) -> Dict[str, int]:
            header = soup.find(lambda tag: tag.name in ("h2", "h3") and title_text in tag.get_text(strip=True))
            if not header:
                header = soup.find(string=lambda t: t and title_text in t)
                header = header.parent if header else None
            if not header:
                return {}

            table = header.find_next("table")
            if not table:
                return {}

            body = table.find("tbody") or table
            positions = {}
            rank = 1
            for tr in body.find_all("tr"):
                # пропускаем подзаголовочные/тотал строки
                if tr.get("class") and any(c in ("thead", "stat_total") for c in tr.get("class", [])):
                    continue
                tcell = tr.find("a")
                if not tcell:
                    continue
                team_name = tcell.get_text(strip=True)
                positions[norm_team_key(team_name)] = rank
                rank += 1
            return positions

        east_pos = extract_positions("Eastern Conference")
        west_pos = extract_positions("Western Conference")
        return {"east": east_pos, "west": west_pos}
    except Exception:
        return {"east": {}, "west": {}}

# ====== Формирование таблиц с трендом ======
def attach_trend(current_rows: List[Dict], yesterday_positions: Dict[str, int]) -> List[Dict]:
    """
    current_rows: список словарей {team, abbr, w, l, pct}
    yesterday_positions: словарь team_key -> rank_yd
    Возвращает тот же список, добавляя:
      - "rank" (сегодня)
      - "delta_places" (вчерашний ранг - сегодняшний)
    """
    # ранжируем по текущему порядку
    ranked = sorted(current_rows, key=lambda x: (-x["pct"], -x["w"]))
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
        key = norm_team_key(row["team"])
        y_rank = yesterday_positions.get(key)
        if y_rank is None:
            row["delta_places"] = None
        else:
            # если вчера был 5, сегодня 3 — delta_places = +2 (поднялись)
            row["delta_places"] = y_rank - i
    return ranked

def fmt_table(title: str, rows: List[Dict]) -> str:
    """
    Формат строки с таблицей для Telegram.
    Пример строки:
      1  🟢▲+2  BOS  5–1  (83.3%)
    """
    out = [f"<b>{escape(title)}</b>"]
    for r in rows:
        w, l = r["w"], r["l"]
        pct_val = r["pct"]
        arrow_str = arrow(r.get("delta_places"))
        abbr = r["abbr"] if r.get("abbr") else r["team"]
        pct_str = f"{pct_val:.3f}"
        out.append(f"{r['rank']:>2} {arrow_str:>4}  {escape(abbr)}  {w}–{l}  ({pct_str})")
    return "\n".join(out)

# ====== Сообщение и отправка ======
def build_message() -> str:
    today = dt.datetime.now(tz=TZ).date()
    cur = fetch_espn_standings_html()
    prev = fetch_bbr_positions_yesterday(today)

    east = attach_trend(cur["east"], prev["east"])
    west = attach_trend(cur["west"], prev["west"])

    head = f"<b>НБА · Таблица по конференциям</b> — {today.strftime('%d %b %Y')}"
    info = "ℹ️ Источники: ESPN (текущая таблица), Basketball-Reference (позиции на вчера)."
    return "\n\n".join([head, fmt_table("Восточная конференция", east),
                        "", fmt_table("Западная конференция", west),
                        "", info])

def send_telegram(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        print("No TELEGRAM_BOT_TOKEN/CHAT_ID in env", file=sys.stderr)
        return
    r = SESSION.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=25
    )
    r.raise_for_status()

# ====== main ======
if __name__ == "__main__":
    try:
        msg = build_message()
        send_telegram(msg)
        print("OK")
    except Exception as e:
        print("ERROR:", repr(e), file=sys.stderr)
        sys.exit(1)
