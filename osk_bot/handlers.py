"""Диалог бота: доступ сотрудников, пошаговая проверка, итоговый отчёт."""
import asyncio
import html
import logging
import re

from telegram import (
    Bot,
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import config, db, gdrive, report
from .checklist import MODULES, item_score, module_for, module_total

logger = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024  # Bot API не отдаёт ботам файлы больше 20 МБ
CARD_REFRESH_DELAY = 1.5  # секунды тишины после последнего фото (альбомы приходят пачкой)

_insp_locks: dict[int, asyncio.Lock] = {}
_refresh_tasks: dict[int, asyncio.Task] = {}
_busy: set[int] = set()  # проверки, по которым сейчас формируется отчёт


def e(text) -> str:
    return html.escape(str(text))


def _lock(inspection_id: int) -> asyncio.Lock:
    return _insp_locks.setdefault(inspection_id, asyncio.Lock())


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(r) for r in rows if r])


def _clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:120]


def _safe_filename(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")[:60] or "doc"


# ============================ Доступ ============================

def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def _touch(update: Update):
    u = update.effective_user
    return db.touch_user(u.id, u.full_name, u.username)


def has_access(user) -> bool:
    return is_admin(user["tg_id"]) or user["status"] == "allowed"


async def send_no_access(bot: Bot, user) -> None:
    uid = user["tg_id"]
    if not config.ADMIN_IDS:
        await bot.send_message(
            uid, f"Администратор бота ещё не настроен.\nВаш Telegram ID: <code>{uid}</code>",
            parse_mode=ParseMode.HTML)
        return
    if user["status"] == "pending":
        await bot.send_message(uid, "⏳ Запрос отправлен администратору. Как только он его одобрит, я напишу.")
    elif user["status"] == "blocked":
        await bot.send_message(uid, "⛔ Доступ к боту закрыт. Обратитесь к администратору.")
    else:
        await bot.send_message(
            uid, "🔒 Бот только для сотрудников ОСК.\nОтправьте запрос — администратор откроет доступ.",
            reply_markup=_kb([Btn("🔑 Запросить доступ", callback_data="acc:req")]))


async def request_access(bot: Bot, user) -> None:
    db.set_user(user["tg_id"], status="pending")
    who = e(user["tg_name"]) + (f" (@{e(user['username'])})" if user["username"] else "")
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, f"🔑 Запрос доступа к боту\n{who}\nID: <code>{user['tg_id']}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=_kb([Btn("✅ Разрешить", callback_data=f"acc:ok:{user['tg_id']}"),
                                  Btn("❌ Отклонить", callback_data=f"acc:no:{user['tg_id']}")]))
        except TelegramError as ex:
            logger.warning("Не удалось уведомить администратора %s: %s", admin_id, ex)


def staff_screen() -> tuple[str, InlineKeyboardMarkup]:
    allowed = db.users_with_status("allowed")
    pending = db.users_with_status("pending")
    blocked = db.users_with_status("blocked")
    lines = ["👥 <b>Сотрудники с доступом</b>"]
    rows = []
    if not allowed:
        lines.append("пока никого")
    for u in allowed:
        name = u["display_name"] or u["tg_name"]
        lines.append(f"• {e(name)}")
        rows.append([Btn(f"❌ Убрать: {name}", callback_data=f"acc:rm:{u['tg_id']}")])
    if pending:
        lines.append("\n⏳ <b>Ждут одобрения</b>")
        for u in pending:
            lines.append(f"• {e(u['tg_name'])}")
            rows.append([Btn(f"✅ Разрешить: {u['tg_name']}", callback_data=f"acc:ok:{u['tg_id']}")])
    if blocked:
        lines.append("\n⛔ <b>Без доступа</b>")
        for u in blocked:
            name = u["display_name"] or u["tg_name"]
            lines.append(f"• {e(name)}")
            rows.append([Btn(f"↩️ Вернуть: {name}", callback_data=f"acc:ok:{u['tg_id']}")])
    rows.append([Btn("🏠 Меню", callback_data="menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ============================ Меню ============================

def menu_screen(user) -> tuple[str, InlineKeyboardMarkup]:
    uid = user["tg_id"]
    active = db.active_inspection(uid)
    rows = []
    if active:
        mod = module_for(active["kind"])
        rows.append([Btn(f"▶️ Продолжить ({mod.emoji} {active['company']} / {active['zhk']})",
                         callback_data="cont")])
    rows.append([Btn("⚖️ Юридическая проверка", callback_data="new:legal")])
    rows.append([Btn("🔧 Техническая проверка", callback_data="new:tech")])
    rows.append([Btn("📋 Мои проверки", callback_data="mine")])
    if is_admin(uid):
        rows.append([Btn("🗂 Проверки всех менеджеров", callback_data="all")])
        sheet_url = db.get_setting("spreadsheet_url")
        if sheet_url:
            rows.append([Btn("📊 Таблица рейтинга", url=sheet_url)])
        rows.append([Btn("👥 Сотрудники", callback_data="staff")])
    text = (f"👷 {e(user['display_name'] or user['tg_name'])}, это бот рейтинга ОСК.\n\n"
            "Выберите раздел проверки. Бот проведёт по пунктам: фотографируете документ, "
            "отмечаете, что есть, — баллы и отчёт посчитает сам.\n\n"
            "⚖️ Юридическая — документы, земля, договор (итог в %).\n"
            "🔧 Техническая — журналы, лаборатория, графики (итог в баллах).")
    return text, InlineKeyboardMarkup(rows)


def _insp_label(insp, with_name: bool = False) -> str:
    mod = module_for(insp["kind"])
    score = module_total(mod, db.checked_criteria(insp["id"]))
    val = f"{mod.percent(score)}%" if mod.scoring == "percent" else f"{score}/{mod.max_points}"
    date = (insp["finished_at"] or insp["started_at"] or "")[:10]
    who = f"{insp['inspector_name']}: " if with_name else ""
    return f"{who}{mod.emoji} {insp['company']}/{insp['zhk']} · {val} · {date}"


def reports_screen(uid: int, scope: str) -> tuple[str, InlineKeyboardMarkup]:
    """scope='mine' — свои проверки; scope='all' — все (только админ)."""
    all_mgr = scope == "all"
    insps = db.finished_inspections(None if all_mgr else uid)
    title = "🗂 Проверки всех менеджеров" if all_mgr else "📋 Мои завершённые проверки"
    lines = [f"<b>{title}</b>"]
    rows = []
    if not insps:
        lines.append("\nПока нет завершённых проверок.")
    for insp in insps:
        label = _insp_label(insp, with_name=all_mgr)
        rows.append([Btn(label[:60], callback_data=f"rep:{insp['id']}")])
    if insps:
        lines.append("\nНажмите на проверку — пришлю PDF-отчёт в чат.")
    rows.append([Btn("🏠 Меню", callback_data="menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def send_report_copy(app: Application, uid: int, iid: int, status_message) -> None:
    """Пересобирает PDF завершённой проверки и присылает его в чат."""
    bot = app.bot
    try:
        insp = db.get_inspection(iid)
        pdf, _ = await build_report(bot, insp)
        name = f"Проверка_{iid}_{_safe_filename(insp['company'])}_{_safe_filename(insp['zhk'])}.pdf"
        try:
            await bot.send_document(uid, pdf, filename=name)
        except TelegramError:
            link = insp["report_url"] or insp["folder_url"]
            msg = "⚠️ Отчёт слишком большой для Telegram."
            if link:
                msg += f"\nОткрыть на Диске: {link}"
            await bot.send_message(uid, msg)
    except Exception:
        logger.exception("Не удалось переслать отчёт проверки %s", iid)
        await bot.send_message(uid, "⚠️ Не удалось собрать отчёт. Попробуйте позже.")
    finally:
        try:
            await status_message.delete()
        except TelegramError:
            pass


async def show_menu(bot: Bot, user, query=None) -> None:
    db.set_user(user["tg_id"], mode="idle")
    text, markup = menu_screen(user)
    if query:
        await _edit(query, text, markup)
    else:
        await bot.send_message(user["tg_id"], text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def ask_company(bot: Bot, uid: int, query=None, kind: str | None = None) -> None:
    fields = {"mode": "await_company"}
    if kind:
        fields["draft_kind"] = kind
    db.set_user(uid, **fields)
    mod = module_for(db.get_user(uid)["draft_kind"] or "tech")
    rows = [[Btn(r["company"], callback_data=f"co:{r['id']}")] for r in db.recent_companies()]
    rows.append([Btn("🏠 Меню", callback_data="menu")])
    text = f"{mod.emoji} <b>{e(mod.title)}</b>\n\n🏢 Напишите название <b>строительной компании</b>"
    if len(rows) > 1:
        text += " или выберите из уже проверенных"
    text += ":"
    if query:
        await _edit(query, text, InlineKeyboardMarkup(rows))
    else:
        await bot.send_message(uid, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def ask_zhk(bot: Bot, uid: int, company: str, query=None) -> None:
    db.set_user(uid, mode="await_zhk", draft_company=company)
    rows = [[Btn(r["zhk"], callback_data=f"zk:{r['id']}")] for r in db.recent_zhk(company)]
    rows.append([Btn("⬅️ Другая компания", callback_data="new")])
    text = f"🏢 {e(company)}\n\n🏘 Напишите название <b>ЖК</b>"
    if len(rows) > 1:
        text += " или выберите из списка"
    text += ":"
    if query:
        await _edit(query, text, InlineKeyboardMarkup(rows))
    else:
        await bot.send_message(uid, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def start_inspection(bot: Bot, user, zhk: str, query=None) -> None:
    uid = user["tg_id"]
    company = user["draft_company"]
    if not company:
        await ask_company(bot, uid, query)
        return
    kind = user["draft_kind"] or "tech"
    inspection_id = db.create_inspection(uid, user["display_name"] or user["tg_name"], kind, company, zhk)
    db.set_user(uid, mode="checklist", draft_company=None)
    logger.info("Проверка %s (%s) начата: %s / %s (%s)", inspection_id, kind, company, zhk, uid)
    insp = db.get_inspection(inspection_id)
    if query:
        await edit_card(query, insp)
    else:
        await send_card(bot, uid, insp)


# ============================ Карточка пункта ============================

def render_card(insp) -> tuple[str, InlineKeyboardMarkup]:
    iid = insp["id"]
    mod = module_for(insp["kind"])
    idx = insp["current_item"]
    item = mod.items[idx]
    checked = db.checked_criteria(iid)
    state = db.item_states(iid).get(item.key)
    n_photos = db.photo_counts(iid).get(item.key, 0)

    lines = [
        f"🏗 <b>{e(insp['company'])}</b> · ЖК {e(insp['zhk'])}",
        f"{mod.emoji} {mod.title} · пункт {idx + 1} из {len(mod.items)}",
        "",
        f"<b>{idx + 1}. {e(item.title)}</b>",
        f"<i>{e(item.hint)}</i>",
        "",
        f"📷 Фото: {n_photos}    ⭐ Баллы: {item_score(item, checked)} из {item.max_points}",
    ]
    if state and state["absent"]:
        lines.append("🚫 Отмечено: отсутствует")
    if state and state["comment"]:
        lines.append(f"💬 {e(state['comment'])}")
    pick = "выберите один вариант" if item.single else "отметьте, что есть"
    lines += ["", f"Пришлите фото (можно несколько), {pick}. "
                  "Текст сообщением сохранится как комментарий («-» удалит его)."]

    rows = []
    for ch in item.choices:
        mark = "🔘" if (item.single and ch.key in checked) else ("✅" if ch.key in checked else "⬜")
        rows.append([Btn(f"{mark} {ch.title} · {ch.points} б.", callback_data=f"t:{iid}:{ch.key}")])
    tools = [Btn("🚫 Отсутствует", callback_data=f"abs:{iid}")]
    if n_photos:
        tools.append(Btn("🗑 Удалить фото", callback_data=f"delph:{iid}"))
    rows.append(tools)
    nav = []
    if idx > 0:
        nav.append(Btn("⬅️ Назад", callback_data=f"go:{iid}:{idx - 1}"))
    nav.append(Btn("📑 Пункты", callback_data=f"list:{iid}"))
    if idx < len(mod.items) - 1:
        nav.append(Btn("Далее ➡️", callback_data=f"next:{iid}"))
    rows.append(nav)
    rows.append([Btn("🏁 Завершить проверку", callback_data=f"fin:{iid}"),
                 Btn("🏠 Меню", callback_data="menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _edit(query, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup,
                                      disable_web_page_preview=True)
    except BadRequest as ex:
        if "not modified" not in str(ex).lower():
            raise


async def edit_card(query, insp) -> None:
    text, markup = render_card(insp)
    await _edit(query, text, markup)
    db.set_user(query.from_user.id, card_message_id=query.message.message_id)


async def send_card(bot: Bot, uid: int, insp=None) -> None:
    """Присылает карточку текущего пункта новым сообщением (под фото пользователя)."""
    insp = insp or db.active_inspection(uid)
    if not insp:
        return
    user = db.get_user(uid)
    if user["card_message_id"]:
        try:  # у старой карточки убираем кнопки, чтобы не путаться
            await bot.edit_message_reply_markup(uid, user["card_message_id"], reply_markup=None)
        except TelegramError:
            pass
    text, markup = render_card(insp)
    msg = await bot.send_message(uid, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    db.set_user(uid, card_message_id=msg.message_id)


def schedule_card_refresh(app: Application, uid: int) -> None:
    """Обновляет карточку один раз после серии фото, а не после каждого."""
    old = _refresh_tasks.get(uid)
    if old and not old.done():
        old.cancel()

    async def refresh():
        await asyncio.sleep(CARD_REFRESH_DELAY)
        if _refresh_tasks.get(uid) is task:
            del _refresh_tasks[uid]
        await send_card(app.bot, uid)

    task = app.create_task(refresh())
    _refresh_tasks[uid] = task


def _score_line(mod, score: int) -> str:
    if mod.scoring == "percent":
        return f"{mod.percent(score)}% ({score} из {mod.max_points}) — {mod.grade(score)}"
    return f"{score} из {mod.max_points} баллов — {mod.grade(score)}"


def list_screen(insp) -> tuple[str, InlineKeyboardMarkup]:
    iid = insp["id"]
    mod = module_for(insp["kind"])
    checked = db.checked_criteria(iid)
    states = db.item_states(iid)
    rows = []
    for n, item in enumerate(mod.items):
        st = states.get(item.key)
        icon = "⬜" if not st or not st["visited"] else ("🚫" if st["absent"] else "✅")
        rows.append([Btn(f"{icon} {n + 1}. {item.title} — {item_score(item, checked)}/{item.max_points}",
                         callback_data=f"go:{iid}:{n}")])
    rows.append([Btn("🏁 Завершить проверку", callback_data=f"fin:{iid}")])
    text = (f"📑 <b>Все пункты</b> · {mod.emoji} {mod.title}\n{e(insp['company'])} / {e(insp['zhk'])}\n"
            f"Набрано: {_score_line(mod, module_total(mod, checked))}\n\n"
            "✅ проверен · 🚫 отсутствует · ⬜ ещё не смотрели\nНажмите на пункт, чтобы перейти к нему.")
    return text, InlineKeyboardMarkup(rows)


def unvisited_items(insp) -> list[int]:
    mod = module_for(insp["kind"])
    states = db.item_states(insp["id"])
    return [n + 1 for n, item in enumerate(mod.items)
            if not (states.get(item.key) and states[item.key]["visited"])]


def finish_screen(insp) -> tuple[str, InlineKeyboardMarkup]:
    iid = insp["id"]
    mod = module_for(insp["kind"])
    score = module_total(mod, db.checked_criteria(iid))
    lines = [f"🏁 <b>Завершение проверки</b>\n{mod.emoji} {mod.title}\n{e(insp['company'])} / {e(insp['zhk'])}\n",
             f"Итог: <b>{_score_line(mod, score)}</b>"]
    missing = unvisited_items(insp)
    if missing:
        lines.append(f"\n⚠️ Не проверены пункты: {', '.join(map(str, missing))}. "
                     "Они пойдут в отчёт с нулём баллов.")
    lines.append("\nПосле завершения изменить проверку будет нельзя.")
    return "\n".join(lines), _kb(
        [Btn("✅ Завершить и сформировать отчёт", callback_data=f"finok:{iid}")],
        [Btn("⬅️ Вернуться к проверке", callback_data=f"go:{iid}:{insp['current_item']}")],
    )


# ============================ Google Диск ============================

def _kind_label(insp) -> str:
    return "Юридическая" if insp["kind"] == "legal" else "Техническая"


def _base_path(insp) -> list[str]:
    return [insp["company"], insp["zhk"], f"{_kind_label(insp)} {insp['folder_label']}"]


def _item_folder(insp, item_key: str) -> str:
    mod = module_for(insp["kind"])
    n = next(i for i, it in enumerate(mod.items, 1) if it.key == item_key)
    return f"{n:02d} {mod.items_by_key[item_key].title}"


def _photo_name(photo) -> tuple[str, str]:
    """Имя файла на Диске и MIME-тип."""
    if photo["kind"] == "photo":
        return f"фото_{photo['id']}.jpg", "image/jpeg"
    name = photo["file_name"] or f"файл_{photo['id']}"
    return f"{photo['id']}_{name}", photo["mime"] or "application/octet-stream"


async def _download(bot: Bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def upload_photo(bot: Bot, photo_id: int, content: bytes | None = None) -> bool:
    """Загружает одно фото на Диск. True — если оно уже там или загрузилось."""
    photo = db.get_photo(photo_id)
    if not photo:
        return True
    insp = db.get_inspection(photo["inspection_id"])
    async with _lock(insp["id"]):
        photo = db.get_photo(photo_id)
        if not photo or photo["drive_file_id"]:
            return True
        try:
            if content is None:
                content = await _download(bot, photo["tg_file_id"])
            name, mime = _photo_name(photo)
            res = await gdrive.upload(_base_path(insp) + [_item_folder(insp, photo["item_key"])],
                                      name, mime, content)
        except (gdrive.DriveError, TelegramError) as ex:
            logger.warning("Фото %s не загружено на Диск: %s", photo_id, ex)
            return False
        if db.get_photo(photo_id):
            db.set_photo_drive_id(photo_id, res["fileId"])
        else:  # пока грузили, фото удалили в боте
            await _trash_quietly(res["fileId"])
        return True


async def _trash_quietly(drive_file_id: str) -> None:
    try:
        await gdrive.trash(drive_file_id)
    except gdrive.DriveError as ex:
        logger.warning("Не удалось удалить файл %s с Диска: %s", drive_file_id, ex)


# ============================ Итоговый отчёт ============================

def _cell(value):
    """Защита от того, что Таблица воспримет текст как формулу."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _date(iso: str | None) -> str:
    return iso[:16].replace("T", " ") if iso else ""


async def build_report(bot: Bot, insp) -> tuple[bytes, dict[int, bytes]]:
    iid = insp["id"]
    files: dict[str, list] = {}
    contents: dict[int, bytes] = {}
    for photo in db.photos(iid):
        try:
            raw = await _download(bot, photo["tg_file_id"])
        except TelegramError as ex:
            logger.warning("Не удалось скачать фото %s: %s", photo["id"], ex)
            continue
        contents[photo["id"]] = raw
        files.setdefault(photo["item_key"], []).append((raw, photo["kind"], photo["file_name"] or ""))
    mod = module_for(insp["kind"])
    checked = db.checked_criteria(iid)
    score = module_total(mod, checked)
    data = report.ReportData(
        inspection_id=iid, kind=insp["kind"], company=insp["company"], zhk=insp["zhk"],
        inspector=insp["inspector_name"] or "", started=_date(insp["started_at"]),
        finished=_date(insp["finished_at"]), score=score, grade=mod.grade(score),
        checked=checked, states={k: dict(v) for k, v in db.item_states(iid).items()},
        files=files, folder_url=insp["folder_url"],
    )
    pdf = await asyncio.to_thread(report.build, data)
    return pdf, contents


def sheet_rows(insp) -> tuple[list[str], list[list], list[str], list[list]]:
    """Строки для листов «Рейтинг» (одна строка на проверку) и «Детали» (по критериям)."""
    iid = insp["id"]
    mod = module_for(insp["kind"])
    checked = db.checked_criteria(iid)
    states = db.item_states(iid)
    counts = db.photo_counts(iid)
    score = module_total(mod, checked)
    date = _date(insp["started_at"])[:10]
    head = [iid, date, insp["company"], insp["zhk"], insp["inspector_name"]]

    no_photo = [str(n) for n, it in enumerate(mod.items, 1)
                if it.max_points and item_score(it, checked) and not counts.get(it.key)]
    summary_headers = (["№ проверки", "Дата", "Компания", "ЖК", "Проверяющий"]
                       + [f"{n}. {it.title}" + (f" (из {it.max_points})" if it.max_points else "")
                          for n, it in enumerate(mod.items, 1)]
                       + ["Итого", "Макс.", "%", "Оценка", "Не проверены",
                          "Баллы без фото (пункты)", "Папка с фото", "PDF-отчёт"])
    per_item = [item_score(it, checked) if it.max_points else
                ("есть" if any(c.key in checked for c in it.choices) else "нет") for it in mod.items]
    summary = [head + per_item
               + [score, mod.max_points, mod.percent(score),
                  mod.grade(score), ", ".join(map(str, unvisited_items(insp))),
                  ", ".join(no_photo), insp["folder_url"] or "", insp["report_url"] or ""]]

    detail_headers = ["№ проверки", "Дата", "Компания", "ЖК", "Проверяющий", "Пункт", "Вариант/критерий",
                      "Отмечено", "Баллы", "Макс.", "Фото, шт.", "Отсутствует", "Комментарий"]
    details = []
    for n, it in enumerate(mod.items, 1):
        st = states.get(it.key)
        for i, c in enumerate(it.choices):
            first = i == 0
            details.append(head + [
                f"{n}. {it.title}", c.title, "да" if c.key in checked else "нет",
                c.points if c.key in checked else 0, c.points,
                counts.get(it.key, 0) if first else "",
                ("да" if st and st["absent"] else "") if first else "",
                (st["comment"] or "") if first and st else "",
            ])
    clean = lambda rows: [[_cell(v) for v in r] for r in rows]
    return summary_headers, clean(summary), detail_headers, clean(details)


async def sync_to_google(bot: Bot, insp, pdf: bytes, contents: dict[int, bytes]) -> str | None:
    """Фото, PDF и строки таблицы → Google. Возвращает текст ошибки или None."""
    iid = insp["id"]
    try:
        for photo in db.photos(iid):
            if not photo["drive_file_id"] and not await upload_photo(bot, photo["id"], contents.get(photo["id"])):
                return "не все фото загрузились на Диск"
        folder = await gdrive.folder(_base_path(insp))
        db.set_inspection(iid, folder_url=folder["folderUrl"])
        pdf_name = f"Отчёт_{iid}_{_safe_filename(insp['company'])}_{_safe_filename(insp['zhk'])}.pdf"
        res = await gdrive.upload(_base_path(insp), pdf_name, "application/pdf", pdf)
        db.set_inspection(iid, report_url=res["fileUrl"])
        sh, srows, dh, drows = sheet_rows(db.get_inspection(iid))
        suffix = "Юр" if insp["kind"] == "legal" else "Тех"
        res = await gdrive.append_rows(f"Рейтинг — {suffix}", sh, srows)
        await gdrive.append_rows(f"Детали — {suffix}", dh, drows)
    except gdrive.DriveError as ex:
        logger.warning("Проверка %s не сохранена в Google: %s", iid, ex)
        return str(ex)
    db.set_setting("spreadsheet_url", res["spreadsheetUrl"])
    db.set_inspection(iid, synced=1)
    return None


async def finalize(app: Application, uid: int, iid: int, status_message) -> None:
    """Отчёт в чат + сохранение в Google. Повторный запуск безопасен."""
    bot = app.bot
    _busy.add(iid)
    try:
        insp = db.get_inspection(iid)
        if gdrive.enabled():
            try:  # ссылка на папку нужна внутри PDF
                folder = await gdrive.folder(_base_path(insp))
                db.set_inspection(iid, folder_url=folder["folderUrl"])
                insp = db.get_inspection(iid)
            except gdrive.DriveError as ex:
                logger.warning("Папка проверки %s не создана: %s", iid, ex)
        pdf, contents = await build_report(bot, insp)
        pdf_sent = await _try_send_pdf(bot, uid, insp, pdf)

        error = await sync_to_google(bot, insp, pdf, contents) if gdrive.enabled() else "Google Диск не подключён"
        insp = db.get_inspection(iid)
        await bot.send_message(uid, result_text(insp, error, pdf_sent), parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True,
                               reply_markup=result_keyboard(insp, error, pdf_sent))
    except Exception:
        logger.exception("Ошибка при завершении проверки %s", iid)
        await bot.send_message(uid, "⚠️ Не удалось сформировать отчёт. Проверка сохранена — "
                                    "нажмите кнопку, чтобы попробовать ещё раз.",
                               reply_markup=_kb([Btn("🔁 Повторить", callback_data=f"redo:{iid}")]))
    finally:
        _busy.discard(iid)
        try:
            await status_message.delete()
        except TelegramError:
            pass


async def _try_send_pdf(bot: Bot, uid: int, insp, pdf: bytes) -> bool:
    """Шлёт PDF в чат. False — если Telegram отклонил его (например, слишком большой)."""
    name = f"Проверка_{insp['id']}_{_safe_filename(insp['company'])}_{_safe_filename(insp['zhk'])}.pdf"
    try:
        await bot.send_document(uid, pdf, filename=name)
        return True
    except TelegramError as ex:
        logger.warning("PDF проверки %s не отправлен в чат (%s КБ): %s", insp["id"], len(pdf) // 1024, ex)
        return False


def result_text(insp, error: str | None, pdf_sent: bool = True) -> str:
    mod = module_for(insp["kind"])
    score = module_total(mod, db.checked_criteria(insp["id"]))
    lines = [f"✅ <b>Проверка №{insp['id']} завершена</b> · {mod.emoji} {mod.title}",
             f"{e(insp['company'])} / ЖК {e(insp['zhk'])}",
             f"\nИтог: <b>{_score_line(mod, score)}</b>"]
    missing = unvisited_items(insp)
    if missing:
        lines.append(f"Не проверены пункты: {', '.join(map(str, missing))}")
    if not pdf_sent:
        if insp["report_url"]:
            lines.append("\n📄 Отчёт получился большим для Telegram — откройте его кнопкой ниже (он на Диске).")
        else:
            lines.append("\n⚠️ Отчёт слишком большой для Telegram. Фото сохранены на Диске; "
                         "откройте папку проверки кнопкой ниже.")
    if error:
        lines.append(f"\n⚠️ В Google не сохранено: {e(error)}")
    elif insp["folder_url"]:
        lines.append("\n📁 Фото и отчёт сохранены на Google Диске, строка добавлена в таблицу рейтинга.")
    return "\n".join(lines)


def result_keyboard(insp, error: str | None, pdf_sent: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if not pdf_sent and insp["report_url"]:
        rows.append([Btn("📄 Открыть PDF-отчёт", url=insp["report_url"])])
    if insp["folder_url"]:
        rows.append([Btn("📁 Папка проверки", url=insp["folder_url"])])
    sheet_url = db.get_setting("spreadsheet_url")
    if sheet_url and not error:
        rows.append([Btn("📊 Таблица рейтинга", url=sheet_url)])
    if error and gdrive.enabled():
        rows.append([Btn("🔁 Повторить отправку в Google", callback_data=f"sync:{insp['id']}")])
    rows.append([Btn("🏠 Меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


async def resync(app: Application, uid: int, iid: int, status_message) -> None:
    """Повторная отправка в Google, когда PDF уже в чате, а Диск был недоступен."""
    bot = app.bot
    _busy.add(iid)
    try:
        insp = db.get_inspection(iid)
        pdf, contents = await build_report(bot, insp)
        pdf_sent = await _try_send_pdf(bot, uid, insp, pdf) if not insp["synced"] else True
        error = await sync_to_google(bot, insp, pdf, contents) if gdrive.enabled() else "Google Диск не подключён"
        insp = db.get_inspection(iid)
        await bot.send_message(uid, result_text(insp, error, pdf_sent), parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True,
                               reply_markup=result_keyboard(insp, error, pdf_sent))
    except Exception:
        logger.exception("Повторная отправка проверки %s не удалась", iid)
        await bot.send_message(uid, "⚠️ Снова не получилось. Попробуйте позже.",
                               reply_markup=_kb([Btn("🔁 Повторить", callback_data=f"sync:{iid}")]))
    finally:
        _busy.discard(iid)
        try:
            await status_message.delete()
        except TelegramError:
            pass


# ============================ Обработчики ============================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _touch(update)
    if not has_access(user):
        await send_no_access(context.bot, user)
        return
    if not user["display_name"]:
        db.set_user(user["tg_id"], mode="await_name")
        await update.message.reply_text(
            "👋 Добро пожаловать! Напишите ваши <b>фамилию и имя</b> — так вы будете указаны в отчётах.",
            parse_mode=ParseMode.HTML)
        return
    await show_menu(context.bot, user)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📘 <b>Как проводить проверку</b>\n\n"
        "1. /start → выберите раздел: ⚖️ Юридическая или 🔧 Техническая. Укажите компанию и ЖК.\n"
        "2. На каждом пункте сфотографируйте документ (можно несколько фото или PDF).\n"
        "3. В юридической части — выберите один вариант; в технической — отметьте галочками, что есть. "
        "Если документа нет — «🚫 Отсутствует».\n"
        "4. Замечание по пункту — просто напишите его сообщением.\n"
        "5. «📑 Пункты» — список всех пунктов, можно вернуться к любому.\n"
        "6. «🏁 Завершить проверку» — бот пришлёт PDF-отчёт и сохранит всё на Google Диск.\n\n"
        "Юридическая и техническая — отдельные проверки по объекту. "
        "Если прервались — /start → «▶️ Продолжить». Ничего не потеряется.",
        parse_mode=ParseMode.HTML)


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Ваш Telegram ID: <code>{update.effective_user.id}</code>",
                                    parse_mode=ParseMode.HTML)


async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    text, markup = staff_screen()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _touch(update)
    uid = user["tg_id"]
    if not has_access(user):
        await send_no_access(context.bot, user)
        return
    text = _clean_name(update.message.text)
    mode = user["mode"]

    if mode == "await_name":
        if len(text) < 2:
            await update.message.reply_text("Напишите, пожалуйста, фамилию и имя.")
            return
        db.set_user(uid, display_name=text, mode="idle")
        await show_menu(context.bot, db.get_user(uid))
    elif mode == "await_company":
        await ask_zhk(context.bot, uid, text)
    elif mode == "await_zhk":
        await start_inspection(context.bot, user, text)
    elif mode == "checklist" and (insp := db.active_inspection(uid)):
        item = module_for(insp["kind"]).items[insp["current_item"]]
        comment = None if text in ("-", "—") else update.message.text.strip()[:500]
        db.set_item_state(insp["id"], item.key, comment=comment, visited=1)
        await send_card(context.bot, uid, insp)
    else:
        await show_menu(context.bot, user)


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _touch(update)
    uid = user["tg_id"]
    if not has_access(user):
        await send_no_access(context.bot, user)
        return
    insp = db.active_inspection(uid)
    if user["mode"] != "checklist" or not insp:
        await update.message.reply_text("Чтобы прикрепить фото, начните или продолжите проверку: /start")
        return

    msg = update.message
    if msg.photo:
        file_id, kind, mime, name = msg.photo[-1].file_id, "photo", "image/jpeg", None
    else:
        doc = msg.document
        if doc.file_size and doc.file_size > MAX_DOWNLOAD:
            await msg.reply_text("⚠️ Файл больше 20 МБ — Telegram не даёт ботам скачивать такие. "
                                 "Сфотографируйте документ или пришлите файл поменьше.")
            return
        kind = "pdf" if doc.mime_type == "application/pdf" else "image"
        file_id, mime, name = doc.file_id, doc.mime_type, doc.file_name

    item = module_for(insp["kind"]).items[insp["current_item"]]
    photo_id = db.add_photo(insp["id"], item.key, file_id, kind, mime, name)
    db.set_item_state(insp["id"], item.key, visited=1, absent=0)
    if gdrive.enabled():
        context.application.create_task(upload_photo(context.bot, photo_id))
    schedule_card_refresh(context.application, uid)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = _touch(update)
    uid = user["tg_id"]
    data = query.data or ""
    action, _, rest = data.partition(":")

    # --- доступ ---
    if action == "acc":
        await handle_access(query, context, user, rest)
        return
    if not has_access(user):
        await query.answer()
        await send_no_access(context.bot, user)
        return

    # --- меню ---
    if action == "menu":
        await query.answer()
        await show_menu(context.bot, user, query)
        return
    if action == "staff" and is_admin(uid):
        await query.answer()
        text, markup = staff_screen()
        await _edit(query, text, markup)
        return
    if action == "mine":
        await query.answer()
        text, markup = reports_screen(uid, "mine")
        await _edit(query, text, markup)
        return
    if action == "all" and is_admin(uid):
        await query.answer()
        text, markup = reports_screen(uid, "all")
        await _edit(query, text, markup)
        return
    if action == "rep":
        insp = db.get_inspection(int(rest)) if rest.isdigit() else None
        if not insp or insp["status"] != "finished" or (
                insp["inspector_id"] != uid and not is_admin(uid)):
            await query.answer("Проверка не найдена", show_alert=True)
            return
        await query.answer("Готовлю отчёт…")
        status = await context.bot.send_message(uid, "⏳ Собираю PDF-отчёт…")
        context.application.create_task(send_report_copy(context.application, uid, insp["id"], status))
        return
    if action == "new":
        await query.answer()
        kind = rest if rest in MODULES else "tech"
        db.set_user(uid, draft_kind=kind)
        active = db.active_inspection(uid)
        if active:
            amod = module_for(active["kind"])
            await _edit(query, f"У вас есть незавершённая проверка:\n<b>{amod.emoji} {amod.title} · "
                               f"{e(active['company'])} / {e(active['zhk'])}</b>\n\nЧто сделать?",
                        _kb([Btn("▶️ Продолжить её", callback_data="cont")],
                            [Btn("🗑 Удалить её и начать новую", callback_data="newforce")],
                            [Btn("🏠 Меню", callback_data="menu")]))
        else:
            await ask_company(context.bot, uid, query, kind=kind)
        return
    if action == "newforce":
        await query.answer()
        active = db.active_inspection(uid)
        if active:
            db.set_inspection(active["id"], status="cancelled")
        await ask_company(context.bot, uid, query)
        return
    if action == "co":
        await query.answer()
        src = db.get_inspection(int(rest))
        if src:
            await ask_zhk(context.bot, uid, src["company"], query)
        return
    if action == "zk":
        await query.answer()
        src = db.get_inspection(int(rest))
        if src and user["mode"] == "await_zhk":
            await start_inspection(context.bot, user, src["zhk"], query)
        return
    if action == "cont":
        await query.answer()
        insp = db.active_inspection(uid)
        if not insp:
            await show_menu(context.bot, user, query)
            return
        db.set_user(uid, mode="checklist")
        await edit_card(query, insp)
        return
    if action in ("sync", "redo"):
        insp = db.get_inspection(int(rest)) if rest.isdigit() else None
        if not insp or (insp["inspector_id"] != uid and not is_admin(uid)) or insp["status"] != "finished":
            await query.answer("Проверка не найдена")
            return
        if insp["id"] in _busy:
            await query.answer("Уже отправляю, подождите…")
            return
        if action == "sync" and insp["synced"]:
            await query.answer("Уже сохранено в Google")
            return
        await query.answer("Отправляю…")
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass
        status = await context.bot.send_message(uid, "⏳ Повторяю…")
        job = resync if action == "sync" else finalize
        context.application.create_task(job(context.application, uid, insp["id"], status))
        return

    # --- действия внутри проверки: первым идёт номер проверки ---
    iid_str, _, arg = rest.partition(":")
    insp = db.active_inspection(uid)
    if not insp or not iid_str.isdigit() or insp["id"] != int(iid_str):
        await query.answer("Эта проверка уже закрыта", show_alert=True)
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass
        return
    iid = insp["id"]
    mod = module_for(insp["kind"])
    item = mod.items[insp["current_item"]]
    db.set_user(uid, mode="checklist")

    if action == "t":
        keys = {c.key for c in item.choices}
        if arg not in keys:
            await query.answer()
            return
        if item.single:  # один вариант на пункт — снимаем остальные
            db.clear_criteria(iid, [k for k in keys if k != arg])
        on = db.toggle_criterion(iid, arg)
        db.set_item_state(iid, item.key, visited=1, absent=0)
        await query.answer("✅ Отмечено" if on else "Снято")
    elif action == "abs":
        db.clear_criteria(iid, [c.key for c in item.choices])
        db.set_item_state(iid, item.key, visited=1, absent=1)
        if insp["current_item"] < len(mod.items) - 1:
            db.set_inspection(iid, current_item=insp["current_item"] + 1)
        await query.answer("🚫 Отсутствует — 0 баллов")
    elif action == "delph":
        removed = db.delete_last_photo(iid, item.key)
        if removed and removed["drive_file_id"]:
            context.application.create_task(_trash_quietly(removed["drive_file_id"]))
        await query.answer("Последнее фото удалено" if removed else "Фото нет")
    elif action == "next":
        db.set_item_state(iid, item.key, visited=1)
        db.set_inspection(iid, current_item=min(insp["current_item"] + 1, len(mod.items) - 1))
        await query.answer()
    elif action == "go":
        await query.answer()
        if arg.isdigit() and 0 <= int(arg) < len(mod.items):
            db.set_inspection(iid, current_item=int(arg))
    elif action == "list":
        await query.answer()
        text, markup = list_screen(insp)
        await _edit(query, text, markup)
        return
    elif action == "fin":
        await query.answer()
        text, markup = finish_screen(insp)
        await _edit(query, text, markup)
        return
    elif action == "finok":
        await query.answer("Формирую отчёт…")
        db.set_inspection(iid, status="finished", finished_at=db.now().isoformat())
        db.set_user(uid, mode="idle", card_message_id=None)
        await _edit(query, f"🏁 Проверка <b>{e(insp['company'])} / {e(insp['zhk'])}</b> завершена.", None)
        status = await context.bot.send_message(
            uid, "⏳ Собираю PDF-отчёт и сохраняю фото на Google Диск. Это может занять пару минут…")
        # В фоне, чтобы бот в это время отвечал другим сотрудникам.
        _busy.add(iid)
        context.application.create_task(finalize(context.application, uid, iid, status))
        return
    else:
        await query.answer()
        return

    await edit_card(query, db.get_inspection(iid))


async def handle_access(query, context: ContextTypes.DEFAULT_TYPE, user, rest: str) -> None:
    uid = user["tg_id"]
    if rest == "req":
        await query.answer()
        if has_access(user):
            await show_menu(context.bot, user, query)
            return
        if user["status"] == "blocked":
            await _edit(query, "⛔ Доступ к боту закрыт. Обратитесь к администратору.", None)
            return
        await request_access(context.bot, user)
        await _edit(query, "⏳ Запрос отправлен администратору. Как только он его одобрит, я напишу.", None)
        return

    if not is_admin(uid):
        await query.answer("Только для администратора", show_alert=True)
        return
    verb, _, target_str = rest.partition(":")
    target = db.get_user(int(target_str)) if target_str.isdigit() else None
    if not target:
        await query.answer("Пользователь не найден")
        return
    name = target["display_name"] or target["tg_name"]
    if verb == "ok":
        db.set_user(target["tg_id"], status="allowed")
        await query.answer("Доступ выдан")
        await _notify(context.bot, target["tg_id"], "✅ Доступ к боту открыт! Нажмите /start")
        if query.message and query.message.text and query.message.text.startswith("🔑"):
            await _edit(query, f"✅ Доступ выдан: {e(name)}", None)
            return
    elif verb == "no":
        db.set_user(target["tg_id"], status="blocked")
        await query.answer("Отклонено")
        await _notify(context.bot, target["tg_id"], "⛔ Администратор отклонил запрос доступа.")
        await _edit(query, f"❌ Отклонено: {e(name)}", None)
        return
    elif verb == "rm":
        db.set_user(target["tg_id"], status="blocked", mode="idle")
        await query.answer(f"Доступ убран: {name}")
    text, markup = staff_screen()
    await _edit(query, text, markup)


async def _notify(bot: Bot, uid: int, text: str) -> None:
    try:
        await bot.send_message(uid, text)
    except TelegramError as ex:
        logger.warning("Не удалось написать пользователю %s: %s", uid, ex)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Ошибка при обработке обновления", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(update.effective_chat.id,
                                           "⚠️ Что-то пошло не так. Попробуйте ещё раз или нажмите /start.")
        except TelegramError:
            pass


async def post_init(app: Application) -> None:
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("help", "📘 Как проводить проверку"),
        BotCommand("myid", "🆔 Мой Telegram ID"),
    ]
    await app.bot.set_my_commands(commands)
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands(commands + [BotCommand("staff", "👥 Сотрудники")],
                                          scope=BotCommandScopeChat(admin_id))
        except TelegramError as ex:  # админ ещё не писал боту
            logger.info("Команды для администратора %s не установлены: %s", admin_id, ex)
    if gdrive.enabled():
        try:
            info = await gdrive.ping()
            logger.info("Google Диск подключён, корневая папка: %s", info.get("root"))
        except gdrive.DriveError as ex:
            logger.error("Google Диск не отвечает: %s", ex)
    else:
        logger.warning("Google Диск не настроен — отчёты будут только в чате")


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=private))
    app.add_handler(CommandHandler("help", cmd_help, filters=private))
    app.add_handler(CommandHandler("myid", cmd_myid, filters=private))
    app.add_handler(CommandHandler("staff", cmd_staff, filters=private))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        private & (filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF), on_file))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
