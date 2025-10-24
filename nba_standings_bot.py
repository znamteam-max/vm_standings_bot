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
    "GSW": "Голден Стэйт
