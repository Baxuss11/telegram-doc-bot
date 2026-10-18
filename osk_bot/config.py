"""Настройки бота. Все секреты берутся из переменных окружения."""
import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Telegram ID администраторов через запятую: 123456789,987654321
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Веб-приложение Google Apps Script (см. apps_script/Code.gs)
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")
APPS_SCRIPT_SECRET = os.environ.get("APPS_SCRIPT_SECRET", "")

# Папка для базы SQLite. На хостинге должна быть постоянным диском (volume).
DATA_DIR = os.environ.get("DATA_DIR", "data")
DB_PATH = os.path.join(DATA_DIR, "osk_bot.sqlite3")

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Bishkek"))

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")
