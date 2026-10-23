"""Настройки бота. Все секреты берутся из переменных окружения."""
import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Режим публичного бота для покупателей (@OtzyvSK_bot): без доступа по списку,
# пошаговая проверка документов и заключение. Включается PUBLIC_MODE=1.
PUBLIC_MODE = os.environ.get("PUBLIC_MODE", "").strip() in ("1", "true", "yes")

# Контакт для кнопки «Связаться» в публичном боте (например, https://wa.me/996...).
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "").strip()

# Telegram ID администраторов через запятую: 123456789,987654321
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Токен @OSK_Tech_bot для публичного бота: отчёты о проверках агентов и сбои
# приходят администраторам туда, а не в чат публичного бота.
NOTIFY_BOT_TOKEN = os.environ.get("NOTIFY_BOT_TOKEN", "").strip()

# Веб-приложение Google Apps Script (см. apps_script/Code.gs)
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")
APPS_SCRIPT_SECRET = os.environ.get("APPS_SCRIPT_SECRET", "")

# Проверка фото документов ИИ (публичный бот). Без ключа — только чтение текста (OCR).
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5").strip()

# Папка для базы SQLite. На хостинге должна быть постоянным диском (volume).
DATA_DIR = os.environ.get("DATA_DIR", "data")
DB_PATH = os.path.join(DATA_DIR, "osk_bot.sqlite3")

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Bishkek"))

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")
