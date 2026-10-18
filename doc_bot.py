"""Бот технического рейтинга ОСК (@OSK_Tech_bot). Точка входа."""
import logging
import sys

from telegram.ext import Application

from osk_bot import config, db, handlers

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# httpx пишет в лог каждый запрос к Telegram вместе с токеном в адресе
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    if not config.BOT_TOKEN:
        sys.exit("Не задана переменная окружения BOT_TOKEN")
    db.init()
    application = Application.builder().token(config.BOT_TOKEN).post_init(handlers.post_init).build()
    handlers.register(application)
    print("Бот технического рейтинга ОСК запущен...")
    application.run_polling()


if __name__ == '__main__':
    main()
