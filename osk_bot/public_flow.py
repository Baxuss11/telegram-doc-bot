"""Публичный бот для покупателей (@OtzyvSK_bot).

Открытый доступ: любой человек фотографирует документы застройщика по шагам,
бот считает процент заполненности и выдаёт понятное заключение. Фото и
результат сохраняются к администратору (та же папка по компании/объекту и
отдельный лист «Покупатели»), чтобы наполнялась база и можно было сравнивать.
"""
import asyncio
import difflib
import html
import json
import logging
import os
import re
import time
from collections import defaultdict

from telegram import (
    Bot, BotCommand, BotCommandScopeChat, InlineKeyboardButton as Btn, InlineKeyboardMarkup,
    InputMediaPhoto, Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from . import buyer, config, db, gdrive, verify

logger = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024
REFRESH_DELAY = 1.5
EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "buyer_examples")
MAX_EXAMPLES = 10  # столько фото Telegram отправляет одним альбомом

_refresh: dict[int, asyncio.Task] = {}
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)  # фото одного человека — по очереди
_ref_cache: dict[str, bytes] = {}                              # образцы: file_id → байты
_names_cache: dict[tuple, tuple[float, list[str]]] = {}        # папки Диска для подсказок
NAMES_TTL = 600
_uploading: set[int] = set()                                   # фото, которые сейчас грузятся на Диск
AI_DAILY_LIMIT = 60                                            # проверок ИИ на человека в сутки
_ai_used: dict[int, tuple[str, int]] = {}                      # uid → (дата, сколько)
_ai_alert_at = 0.0                                             # когда последний раз писали админам о сбое ИИ


def e(text) -> str:
    return html.escape(str(text))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:120]


def _safe(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")[:60] or "doc"


# ----------------------------- экраны -----------------------------

GREETING = (
    "👋 Здравствуйте! Это бесплатная проверка документов новостройки от ОСК.\n\n"
    f"📸 Пройдите {len(buyer.REQUIRED)} шагов: на каждом я покажу образец и подскажу, какие "
    "страницы снять. Бот сам проверяет, что на фото нужный документ.\n\n"
    "🎁 <b>Что вы получите:</b>\n"
    "• понятное заключение — что в порядке, а чего не хватает и чем это грозит;\n"
    f"• соберёте <b>все {len(buyer.REQUIRED)} документов</b> — <b>скидка {buyer.DISCOUNT_PCT}%</b> на "
    f"юридическое сопровождение: мы {buyer.OFFER_SERVICES}. "
    f"Вы оплачиваете только {buyer.CLIENT_PAYS_PCT}%.\n\n"
    "Начнём?"
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
    n_photos = db.photo_counts(iid).get(doc.key, 0)
    need = pages_needed(doc)
    n_ex = len(examples(doc.key))
    lines = [
        f"🏗 {e(insp['company'])} · {e(insp['zhk'])}",
        f"Документ {idx + 1} из {len(buyer.DOCS)}",
        "",
        f"<b>{idx + 1}. {e(doc.title)}</b>",
        f"<i>{e(doc.instruction)}</i>",
        "",
        f"📷 Принято страниц: <b>{min(n_photos, need)} из {need}</b>"
        + ("  ✅ документ собран" if n_photos >= need else ""),
        "",
        (f"Пришлите {need} фото — те же страницы, что на образце выше. " if n_ex else
         f"Пришлите {need} фото разных страниц. ")
        + "Если документа нет — нажмите «Нет / не дали».",
    ]
    # У собранного документа «Нет / не дали» не показываем — чтобы случайно не обнулить.
    rows = [[] if n_photos >= need else [Btn("🚫 Нет / не дали", callback_data=f"p_skip:{iid}")]]
    if n_photos:
        rows[0].insert(0, Btn("🗑 Удалить фото", callback_data=f"p_del:{iid}"))
    nav = []
    if idx > 0:
        nav.append(Btn("⬅️ Назад", callback_data=f"p_go:{iid}:{idx - 1}"))
    if idx < len(buyer.DOCS) - 1:
        nav.append(Btn("Далее ➡️", callback_data=f"p_next:{iid}"))
    rows.append(nav)
    # Заключение — только на последнем документе, чтобы не нажали посреди проверки.
    if idx == len(buyer.DOCS) - 1:
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


def pages_needed(doc: buyer.BuyerDoc) -> int:
    """Сколько фото нужно по документу: столько, сколько образцов, иначе по умолчанию."""
    return len(examples(doc.key)) or buyer.DEFAULT_PAGES.get(doc.key, 1)


def progress(iid: int) -> dict[str, tuple[int, int]]:
    counts = db.photo_counts(iid)
    return {d.key: (min(counts.get(d.key, 0), pages_needed(d)), pages_needed(d)) for d in buyer.DOCS}


def _sync_done(iid: int, doc: buyer.BuyerDoc) -> bool:
    """Отмечает документ собранным, только когда принято нужное число страниц."""
    done = db.photo_counts(iid).get(doc.key, 0) >= pages_needed(doc)
    if done != (doc.key in db.checked_criteria(iid)):
        db.toggle_criterion(iid, doc.key)
    return done


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
             "перед инструкцией и должен будет прислать столько же страниц — бот сверит "
             "каждую с образцом. Замажьте имена, номера и подписи.", ""]
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
        logger.info("/examples от %s — не в ADMIN_IDS %s", uid, sorted(config.ADMIN_IDS))
        await update.message.reply_text(
            f"Команда только для администратора.\nВаш Telegram ID: <code>{uid}</code>",
            parse_mode=ParseMode.HTML)
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
    # Только неизменное: имя покупатель может уточнить в конце, а папка уже создана.
    return [insp["company"], insp["zhk"], f"Покупатель {insp['folder_label']} (ID {insp['inspector_id']})"]


async def _download(bot: Bot, file_id: str) -> bytes:
    f = await bot.get_file(file_id)
    return bytes(await f.download_as_bytearray())


async def upload_photo(bot: Bot, photo_id: int) -> None:
    photo = db.get_photo(photo_id)
    if not photo or photo["drive_file_id"] or photo_id in _uploading:
        return
    _uploading.add(photo_id)
    try:
        await _upload_photo(bot, photo)
    finally:
        _uploading.discard(photo_id)


async def _upload_photo(bot: Bot, photo) -> None:
    photo_id = photo["id"]
    insp = db.get_inspection(photo["inspection_id"])
    try:
        content = await _download(bot, photo["tg_file_id"])
        doc = buyer.DOCS_BY_KEY.get(photo["item_key"])
        folder = doc.title if doc else photo["item_key"]
        page = f"стр{photo['page']}_" if photo["page"] else ""
        name = f"{page}фото_{photo['id']}.jpg" if photo["kind"] == "photo" else \
            f"{photo['id']}_{photo['file_name'] or 'file'}"
        res = await gdrive.upload(_base_path(insp) + [folder], name,
                                  photo["mime"] or "image/jpeg", content)
    except (gdrive.DriveError, TelegramError) as ex:
        logger.warning("Фото покупателя %s не загружено: %s", photo_id, ex)
        return
    if db.get_photo(photo_id):
        db.set_photo_drive_id(photo_id, res["fileId"])


async def save_to_google(bot: Bot, insp) -> str | None:
    """Фото (догрузка) + строка в лист «Покупатели». Возвращает ошибку или None."""
    iid = insp["id"]
    provided = db.checked_criteria(iid)
    pct = buyer.percent(provided)
    pages = progress(iid)
    done = [d.title for d in buyer.REQUIRED if d.key in provided]
    missing = [f"{d.title} ({pages[d.key][0]}/{pages[d.key][1]} стр.)" if pages[d.key][0] else d.title
               for d in buyer.REQUIRED if d.key not in provided]
    photos = db.photos(iid)
    by_method = {m: sum(1 for p in photos if p["verify"] == m) for m in ("ai", "ocr", "none")}
    agent = db.get_user(insp["inspector_id"])
    for p in photos:  # догружаем фото, которые не ушли на Диск сразу
        if not p["drive_file_id"]:
            await upload_photo(bot, p["id"])
    try:
        folder = await gdrive.folder(_base_path(insp))
        db.set_inspection(iid, folder_url=folder["folderUrl"])
        # Новые колонки — только в конец: в листе уже есть строки со старым порядком.
        headers = ["№", "Дата", "Компания", "ЖК", "Имя", "Телефон", "Собрано",
                   "Из", "%", "Предоставлено", "Не хватает", "Папка с фото",
                   "Telegram ID", "Имя в Telegram", "Username", "Фото принято",
                   "Проверено ИИ", "Проверено OCR", "Без проверки"]
        row = [iid, (insp["started_at"] or "")[:10], insp["company"], insp["zhk"],
               insp["inspector_name"] or "", insp["contact_phone"] or "",
               len(done), len(buyer.REQUIRED), pct,
               ", ".join(done), ", ".join(missing), folder["folderUrl"],
               insp["inspector_id"], (agent["tg_name"] if agent else "") or "",
               f"@{agent['username']}" if agent and agent["username"] else "",
               len(photos), by_method["ai"], by_method["ocr"], by_method["none"]]
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
        "📘 Пройдите по документам: на каждом шаге есть образец и подсказка, какие страницы "
        "снять. Бот проверяет, что на фото нужный документ, и засчитывает его, когда собраны "
        f"все страницы. Соберёте все {len(buyer.REQUIRED)} — получите скидку {buyer.DISCOUNT_PCT}% "
        "на юридическое сопровождение. Нажмите /start, чтобы начать.")


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

    if mode in ("pub_company", "pub_zhk"):
        if len(text) < 2:
            await update.message.reply_text("Напишите название полностью.")
            return
        if mode == "pub_zhk" and not user["draft_company"]:
            await ask_company(context.bot, uid)
            return
        path = [] if mode == "pub_company" else [user["draft_company"]]
        # Поиск по Диску идёт в фоне, чтобы не задерживать других пользователей.
        context.application.create_task(_choose_name(context.bot, uid, mode, path, text))
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


# --------------------- компания и ЖК: без дублей папок ---------------------
# Если такая компания/ЖК уже есть в папке «Тех РЕЙТИНГ» (их заводят и сотрудники
# в @OSK_Tech_bot), берём её точное название, чтобы фото легли в ту же папку.

_name_options: dict[int, tuple[str, list[str]]] = {}


async def _known_names(path: list[str]) -> list[str]:
    key = tuple(gdrive.norm_name(p) for p in path)
    cached = _names_cache.get(key)
    if cached and time.monotonic() - cached[0] < NAMES_TTL:
        return cached[1]
    names = await gdrive.children(path)
    _names_cache[key] = (time.monotonic(), names)
    return names


async def _match_name(path: list[str], typed: str) -> tuple[str | None, list[str]]:
    """(то же название в базе, похожие названия)."""
    if not gdrive.enabled():
        return None, []
    try:
        names = await _known_names(path)
    except gdrive.DriveError as ex:
        logger.info("Подсказки названий недоступны: %s", ex)
        return None, []
    key = gdrive.norm_name(typed)
    by_norm = {gdrive.norm_name(n): n for n in names if gdrive.norm_name(n)}
    if key in by_norm:
        return by_norm[key], []
    similar = difflib.get_close_matches(key, list(by_norm), n=3, cutoff=0.75)
    if len(key) >= 4:
        similar += [k for k in by_norm if (key in k or k in key) and len(k) >= 4 and k not in similar]
    return None, [by_norm[k] for k in similar[:3]]


async def _choose_name(bot: Bot, uid: int, mode: str, path: list[str], typed: str) -> None:
    try:
        exact, similar = await _match_name(path, typed)
        if exact:
            await _accept_name(bot, uid, mode, exact, found=True)
        elif similar:
            _name_options[uid] = (mode, [typed] + similar)
            rows = [[Btn(f"✅ {n}", callback_data=f"p_nm:{i}")] for i, n in enumerate(similar, 1)]
            rows.append([Btn(f"➕ Нет, новое: {typed}", callback_data="p_nm:0")])
            await bot.send_message(uid, "🔎 В базе ОСК есть похожие названия. Это одно из них?",
                                   reply_markup=InlineKeyboardMarkup(rows))
        else:
            await _accept_name(bot, uid, mode, typed)
    except TelegramError as ex:
        logger.warning("Выбор названия для %s: %s", uid, ex)


async def _accept_name(bot: Bot, uid: int, mode: str, name: str, found: bool = False) -> None:
    user = db.get_user(uid)
    if user["mode"] != mode:
        return  # пользователь уже ушёл с этого шага
    note = "  📂 <i>есть в базе ОСК</i>" if found else ""
    if mode == "pub_company":
        db.set_user(uid, mode="pub_zhk", draft_company=name)
        await bot.send_message(uid, f"🏢 {e(name)}{note}\n\n🏘 Напишите название <b>ЖК / объекта</b>:",
                               parse_mode=ParseMode.HTML)
        return
    company = user["draft_company"]
    if not company:
        await ask_company(bot, uid)
        return
    iid = db.create_inspection(uid, user["tg_name"], "buyer", company, name)
    db.set_user(uid, mode="pub_docs", draft_company=None)
    await bot.send_message(uid, f"🏘 {e(name)}{note}\n\nНачинаем! Фотографируйте документы по порядку. 👇",
                           parse_mode=ParseMode.HTML)
    await send_card(bot, uid, db.get_inspection(iid), with_example=True)


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
        f = msg.photo[-1]
        file_id, uniq, kind, mime, name = f.file_id, f.file_unique_id, "photo", "image/jpeg", None
    else:
        doc = msg.document
        if doc.file_size and doc.file_size > MAX_DOWNLOAD:
            await msg.reply_text("⚠️ Файл больше 20 МБ. Сфотографируйте документ камерой.")
            return
        kind = "pdf" if doc.mime_type == "application/pdf" else "image"
        file_id, uniq, mime, name = doc.file_id, doc.file_unique_id, doc.mime_type, doc.file_name
    cur = buyer.DOCS[insp["current_item"]]  # шаг на момент отправки, даже если человек уже нажал «Далее»
    status = await msg.reply_text("🔎 Проверяю фото…")
    context.application.create_task(_process_file(
        context.application, uid, insp["id"], cur, status,
        dict(file_id=file_id, uniq=uniq, kind=kind, mime=mime or "image/jpeg", name=name)))


async def _to_admins(text: str) -> None:
    """Сообщение администраторам через @OSK_Tech_bot (NOTIFY_BOT_TOKEN), не в публичный бот."""
    if not config.NOTIFY_BOT_TOKEN:
        logger.warning("NOTIFY_BOT_TOKEN не задан — админам не отправлено: %s", text[:200])
        return
    try:
        async with Bot(config.NOTIFY_BOT_TOKEN) as staff_bot:
            for admin_id in config.ADMIN_IDS:
                try:
                    await staff_bot.send_message(admin_id, text, parse_mode=ParseMode.HTML,
                                                 disable_web_page_preview=True)
                except TelegramError as ex:
                    logger.info("Админ %s не уведомлён: %s", admin_id, ex)
    except TelegramError as ex:
        logger.warning("Бот уведомлений недоступен: %s", ex)


async def _alert_ai_down(bot: Bot, reason: str) -> None:
    """Раз в час сообщает администраторам, что ИИ не работает (фото пока принимаются без проверки)."""
    global _ai_alert_at
    if time.monotonic() - _ai_alert_at < 3600 and _ai_alert_at:
        return
    _ai_alert_at = time.monotonic()
    await _to_admins("⚠️ <b>ЮР заключение ОСК:</b> проверка фото ИИ не работает — фото принимаются "
                     f"без проверки.\nПричина: {e(reason)[:500]}")


def _ai_count(uid: int) -> int:
    day, n = _ai_used.get(uid, ("", 0))
    return n if day == db.now().date().isoformat() else 0


def _ai_spent(uid: int) -> None:
    _ai_used[uid] = (db.now().date().isoformat(), _ai_count(uid) + 1)


async def _references(bot: Bot, doc: buyer.BuyerDoc) -> list[bytes]:
    refs = []
    for fid in examples(doc.key):
        if fid not in _ref_cache:
            _ref_cache[fid] = await asyncio.to_thread(verify.prepare_jpeg, await _download(bot, fid))
        refs.append(_ref_cache[fid])
    return refs


async def _process_file(app: Application, uid: int, iid: int, doc: buyer.BuyerDoc,
                        status, f: dict) -> None:
    """Проверяет фото и засчитывает его, только если это нужный документ и новая страница."""
    async def say(text: str) -> None:
        try:
            await status.edit_text(text, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass

    async with _locks[uid]:
        try:
            need = pages_needed(doc)
            if db.photo_seen(iid, f["uniq"]):
                await say("♻️ Это фото уже было. Пришлите другую страницу.")
                return
            if db.photo_counts(iid).get(doc.key, 0) >= need:
                await say(f"✅ «{e(doc.title)}» уже собран ({need} из {need}). Жмите «Далее ➡️».")
                return
            content = await _download(app.bot, f["file_id"])
            refs = await _references(app.bot, doc)
            # Лимит ИИ в сутки — чтобы поток мусорных фото не тратил деньги; дальше только OCR.
            v = await verify.check(doc, content, f["mime"], refs,
                                   allow_ai=_ai_count(uid) < AI_DAILY_LIMIT)
            if v.method == "ai":
                _ai_spent(uid)
            elif v.method == "none" and v.reason:
                await _alert_ai_down(app.bot, v.reason)
            if v.ok and v.page and v.page in db.covered_pages(iid, doc.key):
                v = verify.Verdict(False, reason=f"Эта страница уже есть (как на образце №{v.page}). "
                                                 "Нужна другая страница — сверьтесь с образцом.")
            if not v.ok:
                await say(f"❌ Не засчитано: {e(v.reason or 'это не похоже на нужный документ.')}\n"
                          f"Нужно: <b>{e(doc.title)}</b>.")
                return
            insp = db.active_inspection(uid)
            if not insp or insp["id"] != iid:
                return  # проверку уже закрыли
            photo_id = db.add_photo(iid, doc.key, f["file_id"], f["kind"], f["mime"], f["name"],
                                    verify=v.method, page=v.page or None, uniq=f["uniq"])
            db.set_item_state(iid, doc.key, visited=1, absent=0)
            got = min(db.photo_counts(iid).get(doc.key, 0), need)
            if _sync_done(iid, doc):
                await say(f"✅ Принято: {got} из {need}. 🎉 «{e(doc.title)}» собран — жмите «Далее ➡️».")
            else:
                await say(f"✅ Принято: {got} из {need}. Пришлите следующую страницу.")
            if gdrive.enabled():
                app.create_task(upload_photo(app.bot, photo_id))
            schedule_refresh(app, uid)
        except Exception:
            logger.exception("Фото покупателя %s не обработано", uid)
            await say("⚠️ Не получилось обработать фото. Пришлите его ещё раз.")


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
    if action == "p_nm":
        await query.answer()
        mode, options = _name_options.pop(uid, ("", []))
        if rest.isdigit() and int(rest) < len(options):
            try:
                await query.edit_message_reply_markup(None)
            except TelegramError:
                pass
            await _accept_name(context.bot, uid, mode, options[int(rest)], found=int(rest) > 0)
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
        _sync_done(iid, cur)
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
        if idx < len(buyer.DOCS) - 1:
            await query.answer("Сначала пройдите все документы", show_alert=True)
            return
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
    insp = db.get_inspection(iid)
    await _edit(query, buyer.conclusion(provided, progress(iid), insp["company"], insp["zhk"]), None)
    if pct >= buyer.OFFER_THRESHOLD:
        db.set_user(uid, mode="pub_contact", draft_company=str(iid))
        await context.bot.send_message(
            uid, "✍️ Напишите <b>имя и телефон</b> одним сообщением — мы позвоним и оформим "
                 f"сопровождение со скидкой {buyer.DISCOUNT_PCT}%.", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[Btn("Не сейчас", callback_data=f"p_contact_skip:{iid}")]]))
    else:
        db.set_user(uid, mode="idle")
        status = await context.bot.send_message(uid, "⏳ Сохраняю…")
        context.application.create_task(_finish_contact(context.application, uid, iid, status))


async def _finish_contact(app: Application, uid: int, iid: int, status_message) -> None:
    try:
        insp = db.get_inspection(iid)
        error = await save_to_google(app.bot, insp) if gdrive.enabled() else None
        if error:
            logger.warning("Покупатель %s: Google не сохранил: %s", iid, error)
        await _notify_admins(app.bot, db.get_inspection(iid), error)
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


async def _notify_admins(bot: Bot, insp, error: str | None) -> None:
    """Коротко сообщает администраторам о каждой завершённой проверке агента."""
    provided = db.checked_criteria(insp["id"])
    agent = db.get_user(insp["inspector_id"])
    who = e(insp["inspector_name"] or (agent["tg_name"] if agent else ""))
    if agent and agent["username"]:
        who += f" (@{e(agent['username'])})"
    lines = [f"🆕 <b>ЮР заключение ОСК — проверка агента №{insp['id']}</b>",
             f"👤 {who}, ID <code>{insp['inspector_id']}</code>"
             + (f", 📞 {e(insp['contact_phone'])}" if insp["contact_phone"] else ""),
             f"🏗 {e(insp['company'])} · {e(insp['zhk'])}",
             f"📊 {len(provided)} из {len(buyer.REQUIRED)} ({buyer.percent(provided)}%)"]
    if insp["folder_url"]:
        lines.append(f"📂 <a href=\"{e(insp['folder_url'])}\">Папка с фото</a>")
    sheet_url = db.get_setting("spreadsheet_url")
    if sheet_url and not error:
        lines.append(f"📊 <a href=\"{e(sheet_url)}\">Таблица рейтинга</a> — лист «Покупатели»")
    if error:
        lines.append(f"⚠️ В таблицу не сохранилось: {e(error)}")
    await _to_admins("\n".join(lines))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Ошибка в публичном боте", exc_info=context.error)


async def post_init(app: Application) -> None:
    commands = [
        BotCommand("start", "🏠 Начать проверку документов"),
        BotCommand("help", "📘 Как это работает"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("Публичный бот: проверка фото — OCR %s, ИИ %s",
                "есть" if verify.ocr_enabled() else "НЕТ (apt install tesseract-ocr tesseract-ocr-rus)",
                f"есть ({config.AI_MODEL})" if verify.ai_enabled() else "нет (ANTHROPIC_API_KEY не задан)")
    logger.info("Публичный бот: администраторы образцов (/examples): %s",
                ", ".join(map(str, sorted(config.ADMIN_IDS))) or "не заданы (ADMIN_IDS пуст)")
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
