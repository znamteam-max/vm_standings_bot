import os
import sys
import json
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
from html import escape
from typing import Dict, List, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ====== Telegram ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Время и формат даты в заголовке
TZ = ZoneInfo("Europe/Helsinki")
DATE_FMT = "%d %b %Y"

USER_AGENT = (
    "NBA-Standings-Bot/3.0 "
    "(+https://site.web.api.espn.com/apis/v2/; +https://site.api.espn.com/apis/v2/)"
)

# ====== Русские названия команд по аббревиатурам ESPN ======
RU_BY_ABBR: Dict[str, str] = {
    "ATL": "Атланта Хокс",
    "BOS": "Бостон Селтикс",
    "BKN": "Бруклин Нетс",
    "CHA": "Шарлотт Хорнетс",
    "CHI": "Чикаго Буллз",
    "CLE": "Кливленд Кавальерс",
    "DAL": "Даллас Маверикс",
    "DEN": "Денвер Наггетс",
    "DET": "Детройт Пистонс",
    "GSW": "Голден Стэйт Уорриорз",
    "HOU": "Хьюстон Рокетс",
    "IND": "Индиана Пэйсерс",
    "LAC": "Лос-Анджелес Клипперс",
    "LAL": "Лос-Анджелес Лейкерс",
    "MEM": "Мемфис Гриззлис",
    "MIA": "Майами Хит",
    "MIL": "Милуоки Бакс",
    "MIN": "Миннесота Тимбервулвз",
    "NOP": "Нью-Орлеан Пеликанс",
    "NYK": "Нью-Йорк Никс",
    "OKC": "Оклахома-Сити Тандер",
    "ORL": "Орландо Мэджик",
    "PHI": "Филадельфия 76ерс",
    "PHX": "Финикс Санз",
    "POR": "Портленд Трэйл Блэйзерс",
    "SAC": "Сакраменто Кингз",
    "SAS": "Сан-Антонио Спёрс",
    "TOR": "Торонто Рэпторс",
    "UTA": "Юта Джаз",
    "WAS": "Вашингтон Уизардс",
}

# На случай «коротких» аббревиатур в ответе ESPN
VARIANT_TO_ESPN_ABBR = {
    "NO": "NOP",
    "NY": "NYK",
    "GS": "GSW",
    "SA": "SAS",
}

# ====== Путь к файлу с «вчерашними» позициями ======
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
PREV_FILE = DATA_DIR / "nba_prev_positions.json"

# ====== HTTP с ретраями ======
def make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=6, connect=6, read=6,
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({"User-Agent": USER_AGENT})
    return s

SESSION = make_session()

# ====== Утилиты ======
def normalize_abbr(abbr: str) -> str:
    a = (abbr or "").upper()
    return VARIANT_TO_ESPN_ABBR.get(a, a)

def arrow(delta_places: Optional[int]) -> str:
    if delta_places is None:
        return "⚪︎="
    if delta_places > 0:
        return f"🟢▲+{delta_places}"
    if delta_places < 0:
        return f"🔴▼{abs(delta_places)}"
    return "⚪︎="

def pct_percent_str(pct_val: float) -> str:
    return f"{pct_val * 100:.1f}%"

def load_prev_positions() -> Dict[str, Dict[str, int]]:
    """
    Загружаем вчерашние позиции из файла.
    Формат:
      {"date":"YYYY-MM-DD","east":{"BOS":1,...},"west":{"DEN":1,...}}
    """
    if not PREV_FILE.exists():
        return {"date": "", "east": {}, "west": {}}
    try:
        with PREV_FILE.open("r", encoding="utf-8") as f:
            j = json.load(f)
        east = j.get("east") or {}
        west = j.get("west") or {}
        return {"date": j.get("date") or "", "east": east, "west": west}
    except Exception:
        return {"date": "", "east": {}, "west": {}}

def save_current_as_prev(today: dt.date, east_rows: List[Dict], west_rows: List[Dict]) -> None:
    """
    Сохраняем сегодняшние позиции как «вчерашние» на следующий запуск.
    """
    east_map = { r["abbr"]: r["rank"] for r in east_rows }
    west_map = { r["abbr"]: r["rank"] for r in west_rows }
    payload = {
        "date": today.isoformat(),
        "east": east_map,
        "west": west_map,
    }
    with PREV_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

# ====== ESPN JSON: текущие standings ======
def _get_json(url: str, params: dict | None = None) -> dict:
    try:
        r = SESSION.get(url, params=params or {}, timeout=30)
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}

def _gather_standings_nodes(node: Any, out: List[dict]) -> None:
    """Рекурсивно собираем узлы, где лежат standings.entries."""
    if isinstance(node, dict):
        st = node.get("standings")
        if isinstance(st, dict):
            entries = st.get("entries") or []
            if isinstance(entries, list) and entries:
                out.append(node)
        for v in node.values():
            _gather_standings_nodes(v, out)
    elif isinstance(node, list):
        for v in node:
            _gather_standings_nodes(v, out)

def _stats_to_map(stats_list: List[dict]) -> Dict[str, Any]:
    m: Dict[str, Any] = {}
    for s in stats_list or []:
        name = s.get("name") or s.get("abbreviation") or s.get("shortDisplayName")
        if not name:
            continue
        m[name] = s.get("value", s.get("displayValue"))
    return m

def _entries_to_rows(entries: List[dict]) -> List[Dict]:
    rows: List[Dict] = []
    for e in entries:
        team = e.get("team") or {}
        display = team.get("displayName") or team.get("name") or ""
        abbr = normalize_abbr(team.get("abbreviation") or team.get("shortDisplayName") or display)
        stats = _stats_to_map(e.get("stats") or [])
        w = int(stats.get("wins") or 0)
        l = int(stats.get("losses") or 0)
        try:
            wp = float(stats.get("winPercent")) if stats.get("winPercent") is not None else (w / (w + l) if (w + l) else 0.0)
        except Exception:
            wp = (w / (w + l) if (w + l) else 0.0)
        rows.append({"team": display, "abbr": abbr, "w": w, "l": l, "pct": float(wp)})
    rows.sort(key=lambda x: (-x["pct"], -x["w"], x["team"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows

def fetch_espn_standings_json() -> Dict[str, List[Dict]]:
    candidates = [
        "https://site.web.api.espn.com/apis/v2/sports/basketball/nba/standings?region=us&lang=en&contentorigin=espn",
        "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings?region=us&lang=en",
    ]
    data = {}
    for u in candidates:
        data = _get_json(u)
        if data:
            break
    if not data:
        return {"east": [], "west": []}

    nodes: List[dict] = []
    _gather_standings_nodes(data, nodes)

    east_rows: List[Dict] = []
    west_rows: List[Dict] = []

    def push_by_name(name: str, entries: List[dict]):
        nonlocal east_rows, west_rows
        lname = (name or "").lower()
        rows = _entries_to_rows(entries)
        if "east" in lname:
            east_rows = rows
        elif "west" in lname:
            west_rows = rows

    for n in nodes:
        name = n.get("name") or n.get("shortName") or n.get("abbreviation") or ""
        st = n.get("standings") or {}
        entries = st.get("entries") or []
        if entries:
            push_by_name(name, entries)

    if (not east_rows or not west_rows) and "children" in data:
        for ch in data.get("children", []):
            name = ch.get("name") or ""
            st = ch.get("standings") or {}
            entries = st.get("entries") or []
            if entries:
                push_by_name(name, entries)
            for ch2 in ch.get("children", []) or []:
                name2 = ch2.get("name") or ""
                st2 = ch2.get("standings") or {}
                entries2 = st2.get("entries") or []
                if entries2:
                    push_by_name(name2, entries2)

    if not east_rows or not west_rows:
        all_entries: List[dict] = []
        for n in nodes:
            all_entries.extend((n.get("standings") or {}).get("entries") or [])
        rows_all = _entries_to_rows(all_entries)
        if len(rows_all) >= 30:
            east_rows = rows_all[:15]
            west_rows = rows_all[15:30]

    return {"east": east_rows, "west": west_rows}

# ====== Тренд и форматирование ======
def attach_trend(current_rows: List[Dict], yesterday_positions: Dict[str, int]) -> List[Dict]:
    ranked = sorted(current_rows, key=lambda x: (-x["pct"], -x["w"], x["team"]))
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
        abbr = row["abbr"]
        y_rank = yesterday_positions.get(abbr)
        row["delta_places"] = None if y_rank is None else (y_rank - i)
    return ranked

def fmt_table(title: str, rows: List[Dict]) -> str:
    out = [f"<b>{escape(title)}</b>"]
    for r in rows:
        w, l = r["w"], r["l"]
        pct_str = pct_percent_str(r["pct"])  # ##.#%
        abbr = r["abbr"]
        name_ru = RU_BY_ABBR.get(abbr, r["team"])

        # пометка плей-ин
        playin = " <i>— плей-ин</i>" if 7 <= r["rank"] <= 10 else ""

        out.append(
            f"{r['rank']:>2} {arrow(r.get('delta_places')):>4}  {escape(name_ru)}  {w}–{l}  ({pct_str}){playin}"
        )
    return "\n".join(out)

# ====== Сообщение и отправка ======
def build_message() -> str:
    today = dt.datetime.now(tz=TZ).date()

    # 1) Текущие таблицы
    cur = fetch_espn_standings_json()
    east_now, west_now = cur.get("east", []), cur.get("west", [])

    # 2) Загружаем вчерашние позиции из файла
    prev = load_prev_positions()
    east = attach_trend(east_now, prev.get("east", {}))
    west = attach_trend(west_now, prev.get("west", {}))

    # 3) Заголовок и блоки
    head = f"<b>НБА · Таблица по конференциям</b> — {today.strftime(DATE_FMT)}"
    info = "ℹ️ Источник текущих данных: ESPN JSON. Сравнение по позициям — с предыдущего поста (локальный файл)."

    # 4) Сохраняем сегодняшние позиции для следующего запуска
    save_current_as_prev(today, east, west)

    return "\n\n".join([
        head,
        fmt_table("Восточная конференция", east),
        "",
        fmt_table("Западная конференция", west),
        "",
        info
    ])

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
