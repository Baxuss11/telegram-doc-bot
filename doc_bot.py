import os
import asyncio
import logging

# Секреты читаем из файла .env (в репозиторий он не попадает), а не из кода.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from PIL import Image

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
STAGES = [
    "1. Статус земельного участка ФОТО",
    "2. ГЗ фото",
    "3. АГЗ фото",
    "4. ИТУ фото",
    "5. 📄 Выписка из ЕГРПНИ",
    "6. Заключение гос экспертизы ФОТО",
    "7. Лицензия ФОТО",
    "8. Налоги ФОТО"
]
# Состояния диалога
CHOOSING_ACTION, UPLOADING_FILES = range(2)

# --- ФУНКЦИИ ---

async def post_init(application: Application):
    """Устанавливает меню с командами после запуска бота."""
    commands = [
        BotCommand("start", "🔄 Начать новый сбор"),
        BotCommand("done", "✅ Создать итоговый PDF"),
        BotCommand("cancel", "❌ Отменить текущий сбор"),
        BotCommand("ads", "📊 Отчёт по рекламе (только админ)"),
    ]
    await application.bot.set_my_commands(commands)

def generate_action_keyboard(stage_index: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора действия на этапе."""
    keyboard = [[InlineKeyboardButton("➕ Добавить фото/документ", callback_data='add_photo')]]
    nav_row = []
    if stage_index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data='previous_stage'))
    if stage_index < len(STAGES) - 1:
        nav_row.append(InlineKeyboardButton("Пропустить ➡️", callback_data='skip_stage'))
    else:
        # На последнем этапе кнопка "Пропустить" заменяется на "Завершить"
        nav_row.append(InlineKeyboardButton("🏁 Завершить", callback_data='finish'))
    
    if nav_row:
        keyboard.append(nav_row)
        
    return InlineKeyboardMarkup(keyboard)

async def send_stage_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение с инструкцией для текущего этапа."""
    stage_index = context.user_data.get('stage_index', 0)
    stage_name = STAGES[stage_index]
    reply_markup = generate_action_keyboard(stage_index)
    
    message_text = f"➡️ **{stage_name}**\n\nЧто вы хотите сделать?"
    
    # Редактируем сообщение, если это ответ на кнопку, или отправляем новое
    if update.callback_query:
        await update.callback_query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')

# --- Обработчики состояний ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог."""
    user = update.message.from_user
    logger.info(f"Пользователь {user.first_name} начал сбор.")
    context.user_data.clear()
    context.user_data['photos'] = {} # Словарь для хранения фото по этапам
    context.user_data['stage_index'] = 0
    await send_stage_prompt(update, context)
    return CHOOSING_ACTION

async def choosing_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает кнопки 'Добавить', 'Пропустить', 'Назад', 'Завершить'."""
    query = update.callback_query
    await query.answer()
    
    action = query.data
    stage_index = context.user_data.get('stage_index', 0)

    if action == 'add_photo':
        await query.edit_message_text(text="Хорошо, жду ваши файлы...")
        return UPLOADING_FILES
    
    if action == 'previous_stage':
        stage_index -= 1
    elif action == 'skip_stage':
        stage_index += 1
    
    context.user_data['stage_index'] = stage_index
    
    if action == 'finish':
        return await done(query, context)

    await send_stage_prompt(update, context)
    return CHOOSING_ACTION

async def uploading_files_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Принимает файл от пользователя."""
    file_to_save = update.message.photo[-1] if update.message.photo else update.message.document
    photo_file = await file_to_save.get_file()

    if not os.path.exists('temp_photos'): os.makedirs('temp_photos')
    
    file_path = os.path.join('temp_photos', photo_file.file_unique_id)
    await photo_file.download_to_drive(file_path)

    stage_index = context.user_data['stage_index']
    if stage_index not in context.user_data['photos']:
        context.user_data['photos'][stage_index] = []
    context.user_data['photos'][stage_index].append(file_path)

    total_photos = sum(len(v) for v in context.user_data['photos'].values())
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить еще фото", callback_data='add_more')],
        [InlineKeyboardButton("Идем дальше ➡️", callback_data='next_stage_after_add')],
    ]
    await update.message.reply_text(
        f"✅ Файл принят! Всего собрано: {total_photos} шт.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return UPLOADING_FILES

async def after_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает кнопки 'Добавить еще' и 'Идем дальше'."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'add_more':
        await query.edit_message_text(text="Хорошо, жду следующий файл...")
        return UPLOADING_FILES
    
    elif query.data == 'next_stage_after_add':
        context.user_data['stage_index'] += 1
        await send_stage_prompt(update, context)
        return CHOOSING_ACTION

async def done(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Создает и отправляет PDF."""
    user = update_or_query.from_user
    photos_dict = context.user_data.get('photos', {})
    
    all_photos = []
    for stage_idx in sorted(photos_dict.keys()):
        all_photos.extend(photos_dict[stage_idx])
        
    if not all_photos:
        # ... обработка, если фото нет ...
        return ConversationHandler.END

    message = update_or_query.message if hasattr(update_or_query, 'message') else update_or_query
    await message.reply_text(f"Отлично! Собираю PDF из {len(all_photos)} файлов...")
    # ... остальная логика создания PDF ...
    # (код ниже идентичен предыдущему, можно скопировать)
    pdf_path = os.path.join('temp_photos', f'{user.id}_final_document.pdf')
    try:
        image_list = [Image.open(p).convert("RGB") for p in all_photos]
        if image_list:
            image_list[0].save(pdf_path, "PDF", resolution=100.0, save_all=True, append_images=image_list[1:])
            with open(pdf_path, 'rb') as doc:
                await context.bot.send_document(chat_id=message.chat_id, document=doc)
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Начать новый сбор", callback_data='start_over')]])
            await message.reply_text("Ваш документ готов. Хотите начать заново?", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при создании PDF: {e}")
        await message.reply_text(f"Произошла ошибка: {e}")
    finally:
        if os.path.exists(pdf_path): os.remove(pdf_path)
        for file_path in all_photos:
            if os.path.exists(file_path): os.remove(file_path)
        context.user_data.clear()
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Логика отмены
    return ConversationHandler.END


def _admin_ids() -> set:
    """Telegram-ID, которым разрешён /ads. Задаётся в .env: ADMIN_IDS=123,456"""
    raw = os.getenv("ADMIN_IDS", "")
    ids = set()
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            ids.add(int(part))
    return ids


async def ads_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отчёт «расход vs заявки по креативам». Доступен только админам."""
    user_id = update.effective_user.id if update.effective_user else None
    admins = _admin_ids()
    if not admins:
        await update.message.reply_text(
            "⚠️ Команда не настроена: в .env не задан ADMIN_IDS."
        )
        return
    if user_id not in admins:
        await update.message.reply_text("⛔ Эта команда доступна только администратору.")
        return

    # Период: /ads 7 — за 7 дней, по умолчанию 30.
    days = 30
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])

    await update.message.reply_text(f"⏳ Собираю отчёт за последние {days} дн…")

    # Импорт внутри функции, чтобы ошибка конфигурации не ломала запуск бота.
    from ad_analytics.service import generate_report
    from ad_analytics.report import format_text
    from ad_analytics.config import ConfigError
    from ad_analytics.meta import MetaError
    from ad_analytics.bitrix import BitrixError

    try:
        # Сетевые вызовы синхронные — уводим в отдельный поток, чтобы не блокировать бота.
        report, since, until = await asyncio.to_thread(generate_report, None, None, days)
        text = format_text(report, since, until)
    except ConfigError as exc:
        await update.message.reply_text(f"Ошибка настройки: {exc}")
        return
    except MetaError as exc:
        await update.message.reply_text(f"Ошибка Meta: {exc}")
        return
    except BitrixError as exc:
        await update.message.reply_text(f"Ошибка Bitrix24: {exc}")
        return
    except Exception as exc:  # на всякий случай, чтобы бот не падал
        logger.exception("Ошибка /ads")
        await update.message.reply_text(f"Непредвиденная ошибка: {exc}")
        return

    # Telegram ограничивает сообщение ~4096 символами — отправляем частями.
    for chunk in _split_message(text):
        await update.message.reply_text(f"<pre>{chunk}</pre>", parse_mode="HTML")


def _split_message(text: str, limit: int = 3500):
    """Режет длинный отчёт по строкам в куски не длиннее limit символов."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)
    return chunks


def main() -> None:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN. Впишите токен бота в файл .env "
            "(TELEGRAM_BOT_TOKEN=...) рядом с doc_bot.py."
        )
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("ads", ads_command))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(choosing_action_handler, pattern='^(add_photo|previous_stage|skip_stage|finish)$'),
            ],
            UPLOADING_FILES: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, uploading_files_handler),
                CallbackQueryHandler(after_upload_handler, pattern='^(add_more|next_stage_after_add)$'),
            ]
        },
        fallbacks=[
            CommandHandler('done', lambda u, c: done(u, c)),
            CommandHandler('cancel', cancel),
            CommandHandler('start', start),
            CallbackQueryHandler(lambda u,c: start(u.callback_query, c), pattern='^start_over$')
        ],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)
    print("Бот 'Умный Ассистент' запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()