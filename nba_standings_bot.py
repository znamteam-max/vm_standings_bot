import os
import sys
import json
import datetime as dt
from zoneinfo import ZoneInfo
from html import escape
from typing import Dict, List, Tuple, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ====== Telegram ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

TZ = ZoneInfo("Europe/Helsinki")  # твой пояс

USER_AGENT = (
    "NBA-Standings-Bot/2.0 "
    "(+https://www.espn.com, +https://site.web.api.espn.com/apis/v2/; +https://www.basketball-reference.com)"
)

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
def norm_team_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())

def pct(w: int, l: int) -> float:
    g = w + l
    return (w / g) if g > 0 else 0.0

def arrow(delta_places: Optional[int]) -> str:
    if delta_places is None:
        return "⚪︎="
    if delta_places > 0:
        return f"🟢▲+{delta_places}"
    if delta_places < 0:
        return f"🔴▼{abs(delta_places)}"
    return "⚪︎="

def _get_json(url: str, params: dict | None = None) -> dict:
    try:
        r = SESSION.get(url, params=params or {}, timeout=30)
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}

# ====== ESPN JSON: текущие standings ======
def _gather_standings_nodes(node: Any, out: List[dict]) -> None:
    """Рекурсивно находим все узлы, где есть standings.entries."""
    if isinstance(node, dict):
        if "standings" in node and isinstance(node["standings"], dict):
            st = node["standings"]
            entries = st.get("entries") or st.get("groups") or []
            # некоторые ревизии кладут entries прямо на уровень выше
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
    """Преобразует ESPN entries -> [{team, abbr, w, l, pct}]"""
    rows: List[Dict] = []
    for e in entries:
        team = e.get("team") or {}
        display = team.get("displayName") or team.get("name") or ""
        abbr = team.get("abbreviation") or team.get("shortDisplayName") or display
        stats = _stats_to_map(e.get("stats") or [])
        # Основные поля:
        w = int(stats.get("wins") or 0)
        l = int(stats.get("losses") or 0)
        wp = stats.get("winPercent")
        try:
            wp = float(wp) if wp is not None else pct(w, l)
        except Exception:
            wp = pct(w, l)
        rows.append({"team": display, "abbr": abbr, "w": w, "l": l, "pct": float(wp)})
    # Сортируем по % побед, затем по победам
    rows.sort(key=lambda x: (-x["pct"], -x["w"], x["team"]))
    # Пронумеруем рангом
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows

def fetch_espn_standings_json() -> Dict[str, List[Dict]]:
    """
    Получает конференции с ESPN JSON (без парсинга HTML).
    Возвращает {"east":[...], "west":[...]}.
    """
    # Основной и запасной эндпоинты (у ESPN бывают разные поддомены)
    candidates = [
        # site.web.api — чаще всего
        "https://site.web.api.espn.com/apis/v2/sports/basketball/nba/standings?region=us&lang=en&contentorigin=espn",
        # site.api — запасной
        "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings?region=us&lang=en",
    ]

    data = {}
    for u in candidates:
        data = _get_json(u)
        if data:
            break
    if not data:
        return {"east": [], "west": []}

    # Собираем все узлы с standings.entries
    nodes: List[dict] = []
    _gather_standings_nodes(data, nodes)

    east_rows: List[Dict] = []
    west_rows: List[Dict] = []

    # Хелпер: положить entries в нужную корзину
    def push_by_name(name: str, entries: List[dict]):
        nonlocal east_rows, west_rows
        lname = (name or "").lower()
        rows = _entries_to_rows(entries)
        if "east" in lname:
            east_rows = rows
        elif "west" in lname:
            west_rows = rows

    # 1) Пытаемся найти явные блоки Eastern/Western
    for n in nodes:
        name = n.get("name") or n.get("shortName") or n.get("abbreviation") or ""
        st = n.get("standings") or {}
        entries = st.get("entries") or []
        if entries and isinstance(entries, list):
            push_by_name(name, entries)

    # 2) Если всё ещё пусто, попробуем пройти по "children"
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

    # 3) Фоллбэк: если в json пришёл один общий список, попробуем разбить по conference,
    #    если у team -> groups/parentGroup есть имя Eastern/Western.
    if (not east_rows or not west_rows):
        all_entries: List[dict] = []
        for n in nodes:
            st = n.get("standings") or {}
            entries = st.get("entries") or []
            all_entries.extend(entries)
        if all_entries:
            east_tmp: List[dict] = []
            west_tmp: List[dict] = []
            for e in all_entries:
                team = e.get("team") or {}
                conf_name = ""
                # варианты вложенности
                grp = team.get("groups") or team.get("group")
                if isinstance(grp, dict):
                    conf_name = grp.get("name") or grp.get("shortName") or ""
                elif isinstance(grp, list) and grp:
                    g0 = grp[0] or {}
                    conf_name = g0.get("name") or g0.get("shortName") or ""
                lname = (conf_name or "").lower()
                if "east" in lname:
                    east_tmp.append(e)
                elif "west" in lname:
                    west_tmp.append(e)
            if east_tmp and not east_rows:
                east_rows = _entries_to_rows(east_tmp)
            if west_tmp and not west_rows:
                west_rows = _entries_to_rows(west_tmp)

    # 4) Жёсткий фоллбэк: если совсем не нашли разделение — упорядочим общий список
    #    и делим пополам (15/15). Лучше так, чем нули.
    if not east_rows or not west_rows:
        all_entries: List[dict] = []
        for n in nodes:
            st = n.get("standings") or {}
            entries = st.get("entries") or []
            all_entries.extend(entries)
        rows_all = _entries_to_rows(all_entries)
        if len(rows_all) >= 30:
            east_rows = rows_all[:15]
            west_rows = rows_all[15:30]

    return {"east": east_rows, "west": west_rows}

# ====== Вчерашние места (Basketball-Reference) ======
def fetch_bbr_positions_yesterday(today: dt.date) -> Dict[str, Dict[str, int]]:
    """
    Возвращает словарь позиций на вчера:
      { "east": { team_key: rank, ... }, "west": { ... } }
    https://www.basketball-reference.com/friv/standings.fcgi?month=MM&day=DD&year=YYYY
    """
    yday = today - dt.timedelta(days=1)
    url = f"https://www.basketball-reference.com/friv/standings.fcgi?month={yday.month}&day={yday.day}&year={yday.year}"
    try:
        r = SESSION.get(url, timeout=30)
        if r.status_code != 200 or not r.text:
            return {"east": {}, "west": {}}
        soup = BeautifulSoup(r.text, "html.parser")

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
            positions: Dict[str, int] = {}
            rank = 1
            for tr in body.find_all("tr"):
                if tr.get("class") and any(c in ("thead", "stat_total") for c in tr.get("class", [])):
                    continue
                a = tr.find("a")
                if not a:
                    continue
                team_name = a.get_text(strip=True)
                positions[norm_team_key(team_name)] = rank
                rank += 1
            return positions

        east_pos = extract_positions("Eastern Conference")
        west_pos = extract_positions("Western Conference")
        return {"east": east_pos, "west": west_pos}
    except Exception:
        return {"east": {}, "west": {}}

# ====== Тренд и вывод ======
def attach_trend(current_rows: List[Dict], yesterday_positions: Dict[str, int]) -> List[Dict]:
    ranked = sorted(current_rows, key=lambda x: (-x["pct"], -x["w"], x["team"]))
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
        key = norm_team_key(row["team"])
        y_rank = yesterday_positions.get(key)
        row["delta_places"] = None if y_rank is None else (y_rank - i)
    return ranked

def fmt_table(title: str, rows: List[Dict]) -> str:
    out = [f"<b>{escape(title)}</b>"]
    for r in rows:
        w, l = r["w"], r["l"]
        pct_str = f"{r['pct']:.3f}"
        abbr = r["abbr"] if r.get("abbr") else r["team"]
        out.append(f"{r['rank']:>2} {arrow(r.get('delta_places')):>4}  {escape(abbr)}  {w}–{l}  ({pct_str})")
    return "\n".join(out)

# ====== Сообщение и отправка ======
def build_message() -> str:
    today = dt.datetime.now(tz=TZ).date()

    cur = fetch_espn_standings_json()
    east_now, west_now = cur.get("east", []), cur.get("west", [])

    prev = fetch_bbr_positions_yesterday(today)
    east = attach_trend(east_now, prev.get("east", {}))
    west = attach_trend(west_now, prev.get("west", {}))

    head = f"<b>НБА · Таблица по конференциям</b> — {today.strftime('%d %b %Y')}"
    info = "ℹ️ Источники: ESPN JSON (текущая таблица), Basketball-Reference (позиции на вчера)."
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
