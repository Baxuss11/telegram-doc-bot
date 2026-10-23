"""Бот технического рейтинга ОСК (@OSK_Tech_bot). Точка входа."""
import logging
import sys

from telegram.ext import Application

from osk_bot import config, db, handlers, public_flow

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
    # PUBLIC_MODE=1 — публичный бот для покупателей (@OtzyvSK_bot);
    # иначе — внутренний бот для сотрудников (@OSK_Tech_bot).
    mod = public_flow if config.PUBLIC_MODE else handlers
    application = Application.builder().token(config.BOT_TOKEN).post_init(mod.post_init).build()
    mod.register(application)
    print(("Публичный бот для покупателей" if config.PUBLIC_MODE
           else "Бот рейтинга ОСК для сотрудников") + " запущен...")
    application.run_polling()


if __name__ == '__main__':
    main()
