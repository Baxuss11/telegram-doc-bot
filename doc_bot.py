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
logger = logging.getLogger("doc_bot")


def main() -> None:
    if not config.BOT_TOKEN:
        sys.exit("Не задана переменная окружения BOT_TOKEN")
    db.init()
    # PUBLIC_MODE=1 — публичный бот для покупателей (@OtzyvSK_bot);
    # иначе — внутренний бот для сотрудников (@OSK_Tech_bot).
    mod = public_flow if config.PUBLIC_MODE else handlers
    mode = ("Публичный бот для покупателей" if config.PUBLIC_MODE
            else "Бот рейтинга ОСК для сотрудников")

    async def post_init(app: Application) -> None:
        # Какой бот и в каком режиме запущен — первое, что смотреть в логе,
        # если бот отвечает «не тем» сценарием (перепутан токен или режим).
        me = await app.bot.get_me()
        logger.info("%s запущен: @%s (id %s)", mode, me.username, me.id)
        if not config.PUBLIC_MODE and not config.ADMIN_IDS:
            logger.warning(
                "Режим сотрудников без ADMIN_IDS: каждому будет ответ «Администратор "
                "бота ещё не настроен». Если это публичный бот — в настройках нет PUBLIC_MODE=1.")
        await mod.post_init(app)

    application = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    mod.register(application)
    application.run_polling()


if __name__ == '__main__':
    main()
