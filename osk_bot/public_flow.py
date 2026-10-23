"""Публичный бот для покупателей (@OtzyvSK_bot).

Открытый доступ: любой человек фотографирует документы застройщика по шагам,
бот считает процент заполненности и выдаёт понятное заключение. Фото и
результат сохраняются к администратору (та же папка по компании/объекту и
отдельный лист «Покупатели»), чтобы наполнялась база и можно было сравнивать.
"""
import asyncio
import html
import json
import logging
import os
import re

from telegram import (
    Bot, BotCommand, BotCommandScopeChat, InlineKeyboardButton as Btn, InlineKeyboardMarkup,
    InputMediaPhoto, Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from . import buyer, config, db, gdrive

logger = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024
REFRESH_DELAY = 1.5
EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "buyer_examples")
MAX_EXAMPLES = 10  # столько фото Telegram отправляет одним альбомом

_refresh: dict[int, asyncio.Task] = {}


def e(text) -> str:
    return html.escape(str(text))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:120]


def _safe(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")[:60] or "doc"


# ----------------------------- экраны -----------------------------

GREETING = (
    "👋 Здравствуйте! Это бесплатная проверка документов новостройки от ОСК.\n\n"
    "Покажу по шагам, какие документы просить у застройщика и как их сфотографировать. "
    "В конце вы получите понятное заключение: что в порядке, а чего не хватает и чем это грозит.\n\n"
    "Фотографируйте то, что у вас есть. Начнём?"
)


def menu(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    active = db.active_inspection(uid)
    if active and active["kind"] == "buyer":
        rows.append([Btn("▶️ Продолжить проверку", callback_data="p_cont")])
    rows.append([Btn("🏠 Новая проверка", callback_data="p_new")])
    return GREETING, InlineKeyboardMarkup(rows)


def doc_card(insp) -> tuple[str, InlineKeyboardMarkup]:
    iid = insp["id"]
    idx = insp["current_item"]
    doc = buyer.DOCS[idx]
    provided = db.checked_criteria(iid)
    n_photos = db.photo_counts(iid).get(doc.key, 0)
    lines = [
        f"🏗 {e(insp['company'])} · {e(insp['zhk'])}",
        f"Документ {idx + 1} из {len(buyer.DOCS)}",
        "",
        f"<b>{idx + 1}. {e(doc.title)}</b>",
        f"<i>{e(doc.instruction)}</i>",
        "",
        f"📷 Ваших фото: {n_photos}" + ("  ✅ принято" if doc.key in provided else ""),
        "",
        "Пришлите фото этого документа. Если его нет — нажмите «Нет / не дали».",
    ]
    rows = [[Btn("🚫 Нет / не дали", callback_data=f"p_skip:{iid}")]]
    if n_photos:
        rows[0].insert(0, Btn("🗑 Удалить фото", callback_data=f"p_del:{iid}"))
    nav = []
    if idx > 0:
        nav.append(Btn("⬅️ Назад", callback_data=f"p_go:{iid}:{idx - 1}"))
    if idx < len(buyer.DOCS) - 1:
        nav.append(Btn("Далее ➡️", callback_data=f"p_next:{iid}"))
    rows.append(nav)
    rows.append([Btn("🏁 Получить заключение", callback_data=f"p_fin:{iid}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _edit(query, text, markup) -> None:
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup,
                                      disable_web_page_preview=True)
    except BadRequest as ex:
        if "not modified" not in str(ex).lower():
            raise


async def send_card(bot: Bot, uid: int, insp=None, with_example: bool = False) -> None:
    insp = insp or db.active_inspection(uid)
    if not insp or insp["kind"] != "buyer":
        return
    user = db.get_user(uid)
    if user["card_message_id"]:
        try:
            await bot.edit_message_reply_markup(uid, user["card_message_id"], reply_markup=None)
        except TelegramError:
            pass
    if with_example:
        await send_example(bot, uid, buyer.DOCS[insp["current_item"]])
    text, markup = doc_card(insp)
    msg = await bot.send_message(uid, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    db.set_user(uid, card_message_id=msg.message_id)


# ----------------------------- образцы фото -----------------------------
# Образцы загружает администратор прямо в бота (/examples) — хранятся как
# file_id Telegram в настройках базы, без файлов в репозитории. Если образцов
# нет — берётся файл из buyer_examples/ (если положен), иначе только текст.

def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def examples(doc_key: str) -> list[str]:
    try:
        return json.loads(db.get_setting(f"example:{doc_key}") or "[]")
    except ValueError:
        return []


def set_examples(doc_key: str, file_ids: list[str]) -> None:
    db.set_setting(f"example:{doc_key}", json.dumps(file_ids[:MAX_EXAMPLES]))


async def send_example(bot: Bot, uid: int, doc: buyer.BuyerDoc) -> None:
    caption = f"Пример: {doc.title}"
    try:
        ids = examples(doc.key)
        if len(ids) == 1:
            await bot.send_photo(uid, ids[0], caption=caption)
        elif ids:
            await bot.send_media_group(uid, [InputMediaPhoto(fid, caption=caption if i == 0 else None)
                                             for i, fid in enumerate(ids)])
        elif doc.example:
            path = os.path.join(EXAMPLES_DIR, f"{doc.example}.jpg")
            if os.path.exists(path):
                with open(path, "rb") as f:
                    await bot.send_photo(uid, f, caption=caption)
    except TelegramError as ex:
        logger.warning("Пример %s не отправлен: %s", doc.key, ex)


def examples_screen() -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🖼 <b>Образцы фото для покупателей</b>",
             "Выберите документ и пришлите фото-образцы (до 10). Покупатель увидит их "
             "перед инструкцией. Замажьте имена, номера и подписи.", ""]
    rows = []
    for i, doc in enumerate(buyer.DOCS):
        n = len(examples(doc.key))
        lines.append(f"{i + 1}. {e(doc.title)} — " + (f"{n} фото" if n else "нет"))
        rows.append([Btn(f"{i + 1}. {doc.title}" + (f" ({n})" if n else ""), callback_data=f"x_doc:{i}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def example_doc_screen(idx: int) -> tuple[str, InlineKeyboardMarkup]:
    doc = buyer.DOCS[idx]
    n = len(examples(doc.key))
    text = (f"🖼 <b>{idx + 1}. {e(doc.title)}</b>\n\nОбразцов: {n} из {MAX_EXAMPLES}.\n"
            "Пришлите фото сюда (обычным фото, не файлом) — каждое добавится к образцам.")
    rows = []
    if n:
        rows.append([Btn("👁 Показать", callback_data=f"x_show:{idx}"),
                     Btn("🗑 Очистить", callback_data=f"x_clr:{idx}")])
    rows.append([Btn("✅ Готово", callback_data="x_list")])
    return text, InlineKeyboardMarkup(rows)


async def cmd_examples(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update)["tg_id"]
    if not is_admin(uid):
        return
    db.set_user(uid, mode="idle")
    text, markup = examples_screen()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_example_file(update: Update, idx: int) -> None:
    msg = update.message
    if not msg.photo:
        await msg.reply_text("Пришлите образец обычным фото (со сжатием), не файлом.")
        return
    doc = buyer.DOCS[idx]
    ids = examples(doc.key)
    if len(ids) >= MAX_EXAMPLES:
        await msg.reply_text(f"Уже {MAX_EXAMPLES} образцов — это максимум. Очистите и загрузите заново.")
        return
    set_examples(doc.key, ids + [msg.photo[-1].file_id])
    text, markup = example_doc_screen(idx)
    await msg.reply_text("✅ Добавлено.\n\n" + text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_example_callback(query, context, uid: int, action: str, rest: str) -> None:
    await query.answer()
    idx = int(rest) if rest.isdigit() and int(rest) < len(buyer.DOCS) else None
    if action == "x_list" or idx is None:
        db.set_user(uid, mode="idle")
        text, markup = examples_screen()
    elif action == "x_show":
        await send_example(context.bot, uid, buyer.DOCS[idx])
        text, markup = example_doc_screen(idx)
        await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    else:
        if action == "x_clr":
            set_examples(buyer.DOCS[idx].key, [])
        db.set_user(uid, mode=f"pub_ex:{idx}")
        text, markup = example_doc_screen(idx)
    await _edit(query, text, markup)


def schedule_refresh(app: Application, uid: int) -> None:
    old = _refresh.get(uid)
    if old and not old.done():
        old.cancel()

    async def run():
        await asyncio.sleep(REFRESH_DELAY)
        if _refresh.get(uid) is task:
            del _refresh[uid]
        await send_card(app.bot, uid)

    task = app.create_task(run())
    _refresh[uid] = task


# ----------------------------- сохранение -----------------------------

def _base_path(insp) -> list[str]:
    return [insp["company"], insp["zhk"], f"Покупатель {insp['folder_label']}"]


async def _download(bot: Bot, file_id: str) -> bytes:
    f = await bot.get_file(file_id)
    return bytes(await f.download_as_bytearray())


async def upload_photo(bot: Bot, photo_id: int) -> None:
    photo = db.get_photo(photo_id)
    if not photo or photo["drive_file_id"]:
        return
    insp = db.get_inspection(photo["inspection_id"])
    try:
        content = await _download(bot, photo["tg_file_id"])
        doc = buyer.DOCS_BY_KEY.get(photo["item_key"])
        folder = doc.title if doc else photo["item_key"]
        name = f"фото_{photo['id']}.jpg" if photo["kind"] == "photo" else \
            f"{photo['id']}_{photo['file_name'] or 'file'}"
        res = await gdrive.upload(_base_path(insp) + [folder], name,
                                  photo["mime"] or "image/jpeg", content)
    except (gdrive.DriveError, TelegramError) as ex:
        logger.warning("Фото покупателя %s не загружено: %s", photo_id, ex)
        return
    if db.get_photo(photo_id):
        db.set_photo_drive_id(photo_id, res["fileId"])


async def save_to_google(insp) -> str | None:
    """Фото (догрузка) + строка в лист «Покупатели». Возвращает ошибку или None."""
    iid = insp["id"]
    provided = db.checked_criteria(iid)
    pct = buyer.percent(provided)
    done = [d.title for d in buyer.REQUIRED if d.key in provided]
    missing = [d.title for d in buyer.REQUIRED if d.key not in provided]
    try:
        folder = await gdrive.folder(_base_path(insp))
        db.set_inspection(iid, folder_url=folder["folderUrl"])
        headers = ["№", "Дата", "Компания", "ЖК", "Имя", "Телефон", "Собрано",
                   "Из", "%", "Предоставлено", "Не хватает", "Папка с фото"]
        row = [iid, (insp["started_at"] or "")[:10], insp["company"], insp["zhk"],
               insp["inspector_name"] or "", insp["contact_phone"] or "",
               len(done), len(buyer.REQUIRED), pct,
               ", ".join(done), ", ".join(missing), folder["folderUrl"]]
        row = [("'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v) for v in row]
        res = await gdrive.append_rows("Покупатели", headers, [row])
    except gdrive.DriveError as ex:
        logger.warning("Проверка покупателя %s не сохранена: %s", iid, ex)
        return str(ex)
    db.set_setting("spreadsheet_url", res["spreadsheetUrl"])
    db.set_inspection(iid, synced=1)
    return None


# ----------------------------- обработчики -----------------------------

def _touch(update: Update):
    u = update.effective_user
    return db.touch_user(u.id, u.full_name, u.username)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _touch(update)
    text, markup = menu(update.effective_user.id)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📘 Пройдите по документам, фотографируя каждый. В конце получите заключение. "
        "Нажмите /start, чтобы начать.")


async def ask_company(bot: Bot, uid: int, query=None) -> None:
    db.set_user(uid, mode="pub_company")
    text = "🏢 Напишите название <b>строительной компании</b> (застройщика):"
    if query:
        await _edit(query, text, None)
    else:
        await bot.send_message(uid, text, parse_mode=ParseMode.HTML)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _touch(update)
    uid = user["tg_id"]
    mode = user["mode"]
    text = _clean(update.message.text)

    if mode == "pub_company":
        if len(text) < 2:
            await update.message.reply_text("Напишите название компании.")
            return
        db.set_user(uid, mode="pub_zhk", draft_company=text)
        await update.message.reply_text(
            f"🏢 {e(text)}\n\n🏘 Напишите название <b>ЖК / объекта</b>:", parse_mode=ParseMode.HTML)
    elif mode == "pub_zhk":
        company = user["draft_company"]
        if not company:
            await ask_company(context.bot, uid)
            return
        iid = db.create_inspection(uid, user["tg_name"], "buyer", company, text)
        db.set_user(uid, mode="pub_docs", draft_company=None)
        await update.message.reply_text("Начинаем! Фотографируйте документы по порядку. 👇")
        await send_card(context.bot, uid, db.get_inspection(iid), with_example=True)
    elif mode == "pub_contact" and (user["draft_company"] or "").isdigit():
        # Текст = имя и телефон; id проверки сохранён в draft_company
        iid = int(user["draft_company"])
        name, phone = _split_contact(update.message.text)
        db.set_inspection(iid, inspector_name=name or user["tg_name"], contact_phone=phone)
        db.set_user(uid, mode="idle", draft_company=None)
        status = await update.message.reply_text("⏳ Сохраняю заявку…")
        context.application.create_task(_finish_contact(context.application, uid, iid, status))
    else:
        text2, markup = menu(uid)
        await update.message.reply_text(text2, parse_mode=ParseMode.HTML, reply_markup=markup)


def _split_contact(raw: str) -> tuple[str, str]:
    phone = ""
    m = re.search(r"(\+?\d[\d\s\-()]{7,}\d)", raw)
    if m:
        phone = re.sub(r"[^\d+]", "", m.group(1))
        raw = raw.replace(m.group(1), " ")
    return _clean(raw), phone


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _touch(update)
    uid = user["tg_id"]
    if user["mode"].startswith("pub_ex:") and is_admin(uid):
        await on_example_file(update, int(user["mode"].partition(":")[2]))
        return
    insp = db.active_inspection(uid)
    if user["mode"] != "pub_docs" or not insp or insp["kind"] != "buyer":
        await update.message.reply_text("Чтобы начать проверку, нажмите /start")
        return
    msg = update.message
    if msg.photo:
        file_id, kind, mime, name = msg.photo[-1].file_id, "photo", "image/jpeg", None
    else:
        doc = msg.document
        if doc.file_size and doc.file_size > MAX_DOWNLOAD:
            await msg.reply_text("⚠️ Файл больше 20 МБ. Сфотографируйте документ камерой.")
            return
        kind = "pdf" if doc.mime_type == "application/pdf" else "image"
        file_id, mime, name = doc.file_id, doc.mime_type, doc.file_name
    cur = buyer.DOCS[insp["current_item"]]
    photo_id = db.add_photo(insp["id"], cur.key, file_id, kind, mime, name)
    if cur.key not in db.checked_criteria(insp["id"]):
        db.toggle_criterion(insp["id"], cur.key)  # отметить документ как предоставленный
    db.set_item_state(insp["id"], cur.key, visited=1, absent=0)
    if gdrive.enabled():
        context.application.create_task(upload_photo(context.bot, photo_id))
    schedule_refresh(context.application, uid)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = _touch(update)
    uid = user["tg_id"]
    action, _, rest = (query.data or "").partition(":")

    if action.startswith("x_"):
        if is_admin(uid):
            await on_example_callback(query, context, uid, action, rest)
        else:
            await query.answer()
        return
    if action == "p_new":
        await query.answer()
        active = db.active_inspection(uid)
        if active and active["kind"] == "buyer":
            db.set_inspection(active["id"], status="cancelled")
        await ask_company(context.bot, uid, query)
        return
    if action == "p_cont":
        await query.answer()
        insp = db.active_inspection(uid)
        if not insp or insp["kind"] != "buyer":
            await ask_company(context.bot, uid, query)
            return
        db.set_user(uid, mode="pub_docs")
        await _edit(query, "Продолжаем 👇", None)
        await send_card(context.bot, uid, insp, with_example=True)
        return
    if action == "p_contact_skip":
        await query.answer()
        insp = db.get_inspection(int(rest)) if rest.isdigit() else None
        if insp:
            db.set_user(uid, mode="idle")
            status = await context.bot.send_message(uid, "⏳ Сохраняю…")
            context.application.create_task(_finish_contact(context.application, uid, insp["id"], status))
        return

    iid_s, _, arg = rest.partition(":")
    insp = db.active_inspection(uid)
    if not insp or not iid_s.isdigit() or insp["id"] != int(iid_s):
        await query.answer("Эта проверка уже закрыта", show_alert=True)
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass
        return
    iid = insp["id"]
    idx = insp["current_item"]
    cur = buyer.DOCS[idx]
    db.set_user(uid, mode="pub_docs")

    if action == "p_skip":
        db.clear_criteria(iid, [cur.key])
        db.set_item_state(iid, cur.key, visited=1, absent=1)
        if idx < len(buyer.DOCS) - 1:
            db.set_inspection(iid, current_item=idx + 1)
            await query.answer("Отмечено: не предоставлен")
            await send_card(context.bot, uid, db.get_inspection(iid), with_example=True)
        else:
            await query.answer()
            await _show_result(context, uid, iid, query)
        return
    if action == "p_del":
        removed = db.delete_last_photo(iid, cur.key)
        if removed and not db.photo_counts(iid).get(cur.key):
            db.clear_criteria(iid, [cur.key])
        if removed and removed["drive_file_id"]:
            context.application.create_task(_trash(removed["drive_file_id"]))
        await query.answer("Фото удалено" if removed else "Фото нет")
    elif action == "p_next":
        db.set_item_state(iid, cur.key, visited=1)
        db.set_inspection(iid, current_item=min(idx + 1, len(buyer.DOCS) - 1))
        await query.answer()
        await send_card(context.bot, uid, db.get_inspection(iid), with_example=True)
        return
    elif action == "p_go":
        await query.answer()
        if arg.isdigit() and 0 <= int(arg) < len(buyer.DOCS):
            db.set_inspection(iid, current_item=int(arg))
        await send_card(context.bot, uid, db.get_inspection(iid), with_example=True)
        return
    elif action == "p_fin":
        await query.answer()
        await _show_result(context, uid, iid, query)
        return
    else:
        await query.answer()
        return
    text, markup = doc_card(db.get_inspection(iid))
    await _edit(query, text, markup)


async def _trash(fid: str) -> None:
    try:
        await gdrive.trash(fid)
    except gdrive.DriveError:
        pass


async def _show_result(context, uid: int, iid: int, query) -> None:
    db.set_inspection(iid, status="finished", finished_at=db.now().isoformat())
    db.set_user(uid, card_message_id=None)
    provided = db.checked_criteria(iid)
    pct = buyer.percent(provided)
    await _edit(query, buyer.conclusion(provided), None)
    if pct >= buyer.OFFER_THRESHOLD:
        db.set_user(uid, mode="pub_contact", draft_company=str(iid))
        await context.bot.send_message(
            uid, "✍️ Напишите <b>имя и телефон</b> одним сообщением — и мы свяжемся по сопровождению "
                 "со скидкой 50%.", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[Btn("Не сейчас", callback_data=f"p_contact_skip:{iid}")]]))
    else:
        db.set_user(uid, mode="idle")
        status = await context.bot.send_message(uid, "⏳ Сохраняю…")
        context.application.create_task(_finish_contact(context.application, uid, iid, status))


async def _finish_contact(app: Application, uid: int, iid: int, status_message) -> None:
    try:
        insp = db.get_inspection(iid)
        error = await save_to_google(insp) if gdrive.enabled() else None
        if error:
            logger.warning("Покупатель %s: Google не сохранил: %s", iid, error)
        rows = [[Btn("🔄 Новая проверка", callback_data="p_new")]]
        if config.SUPPORT_CONTACT:
            rows.insert(0, [Btn("💬 Связаться с нами", url=config.SUPPORT_CONTACT)])
        await app.bot.send_message(
            uid, "✅ Готово! Спасибо, что проверили документы.\n"
                 "Если оставили контакт — мы свяжемся с вами.",
            reply_markup=InlineKeyboardMarkup(rows))
    except Exception:
        logger.exception("Не удалось завершить проверку покупателя %s", iid)
        await app.bot.send_message(uid, "⚠️ Что-то пошло не так, но ваши данные сохранены.")
    finally:
        try:
            await status_message.delete()
        except TelegramError:
            pass


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Ошибка в публичном боте", exc_info=context.error)


async def post_init(app: Application) -> None:
    commands = [
        BotCommand("start", "🏠 Начать проверку документов"),
        BotCommand("help", "📘 Как это работает"),
    ]
    await app.bot.set_my_commands(commands)
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands(commands + [BotCommand("examples", "🖼 Образцы фото")],
                                          scope=BotCommandScopeChat(admin_id))
        except TelegramError as ex:  # админ ещё не писал боту
            logger.info("Команды для администратора %s не установлены: %s", admin_id, ex)
    if gdrive.enabled():
        try:
            info = await gdrive.ping()
            logger.info("Публичный бот: Google Диск подключён (%s)", info.get("root"))
        except gdrive.DriveError as ex:
            logger.error("Публичный бот: Google Диск не отвечает: %s", ex)


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=private))
    app.add_handler(CommandHandler("help", cmd_help, filters=private))
    app.add_handler(CommandHandler("examples", cmd_examples, filters=private))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        private & (filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF), on_file))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
