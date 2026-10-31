import logging
import shutil
import time
from pathlib import Path

import aiofiles
from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart

from bot.config import settings
from bot.keyboards.inline import (
    get_admin_keyboard,
    get_start_keyboard,
    get_stats_keyboard,
)
from bot.services.cookie_utils import (
    parse_cookie_services,
    validate_netscape_cookies,
)
from bot.services.queue_manager import queue_manager
from bot.services.stats_service import stats_service

logger = logging.getLogger(__name__)

router = Router(name="base_router")


def get_help_text(is_admin: bool = False) -> str:
    admin_line = "\n/admin — Панель управления (сервер, статистика, cookies)" if is_admin else ""
    return (
        "📖 <b>Справка по использованию PipeGrab:</b>\n\n"
        "1️⃣ <b>Отправка ссылки:</b> скопируйте ссылку на видео или публикацию и отправьте боту.\n"
        "2️⃣ <b>YouTube:</b> бот предложит скачать видео в MP4 или аудиодорожку в MP3.\n"
        "3️⃣ <b>TikTok:</b> видео скачиваются без водяных знаков, а фото-посты — аккуратным альбомом с прикрепленной музыкой.\n"
        "4️⃣ <b>Instagram:</b> Reels, видео, публикации и карусели фото (истории доступны при наличии cookies у бота).\n"
        "5️⃣ <b>Pinterest:</b> оригинальные фотографии, видео со звуком, анимированные GIF и карусели.\n"
        "6️⃣ <b>Reddit и Twitter / X:</b> видео со звуком (v.redd.it), посты-галереи, анимации RedGifs и клипы.\n"
        "7️⃣ <b>Кружки (Video Notes):</b> ответьте командой /circle на любое видео или отправьте <code>/circle &lt;ссылка&gt;</code>.\n"
        "8️⃣ <b>Инлайн-режим:</b> наберите <code>@имя_бота &lt;ссылка&gt;</code> прямо в любом диалоге или группе, чтобы отправить медиа собеседнику.\n\n"
        "<b>Команды бота:</b>\n"
        "/start — Главное меню\n"
        "/help — Справка и возможности\n"
        "/circle — Создать Telegram-кружок из видео или ссылки"
        f"{admin_line}"
    )


def get_server_status_text() -> str:
    total, _used, free = shutil.disk_usage(settings.downloads_dir)
    api_mode = "Локальный (до 2 ГБ)" if settings.local_bot_api_url else "Стандартный Bot API (до 50 МБ)"
    cookies_status = "Подключены ✅" if settings.has_cookies else "Отсутствуют ⚠️ (истории Instagram недоступны)"

    return (
        "🖥 <b>Состояние сервера и системы:</b>\n\n"
        f"• <b>Режим Bot API:</b> {api_mode}\n"
        f"• <b>Cookies:</b> {cookies_status}\n"
        f"• <b>Активных загрузок в очереди:</b> {len(queue_manager._active_users)}\n"
        f"• <b>Свободно места на диске:</b> {free // (1024 * 1024 * 1024)} ГБ / {total // (1024 * 1024 * 1024)} ГБ\n"
        f"• <b>Лимит размера файла:</b> {settings.max_file_size_mb} МБ\n"
    )


def get_cookies_status_text(is_admin: bool = True) -> str:
    if settings.has_cookies:
        stat = settings.cookies_file.stat()
        file_size_kb = stat.st_size / 1024
        mtime = time.strftime('%d.%m.%Y %H:%M', time.localtime(stat.st_mtime))

        services = []
        try:
            content = settings.cookies_file.read_text(encoding="utf-8", errors="ignore")
            services = parse_cookie_services(content)
        except Exception:
            pass

        services_str = "\n".join(f"• {s}" for s in services) if services else "• Общие cookies"

        update_hint = (
            "\n\n<i>Чтобы обновить куки, просто отправьте новый файл <code>cookies.txt</code> в этот чат как документ.</i>"
            if is_admin else ""
        )

        return (
            "🍪 <b>Статус Cookies:</b>\n\n"
            "✅ <b>Файл cookies подключен и активен!</b>\n"
            f"📁 <b>Путь:</b> <code>{settings.cookies_file}</code>\n"
            f"📦 <b>Размер:</b> {file_size_kb:.1f} КБ\n"
            f"📅 <b>Обновлен:</b> {mtime}\n\n"
            f"🌐 <b>Найденные авторизации:</b>\n{services_str}"
            f"{update_hint}"
        )
    else:
        if is_admin:
            return (
                "🍪 <b>Статус Cookies:</b>\n\n"
                "⚠️ <b>Файл cookies не найден или пуст.</b>\n"
                f"📁 Ожидается: <code>{settings.cookies_file}</code>\n\n"
                "<b>Зачем нужны cookies:</b>\n"
                "• Скачивание <b>историй Instagram</b> (Instagram блокирует анонимный доступ к историям)\n"
                "• Доступ к приватным или возрастным видео YouTube\n"
                "• Защита от блокировок и проверок роботов\n\n"
                "<b>Как установить cookies:</b>\n"
                "1. Установите расширение для браузера Chrome/Firefox (например, <i>Get cookies.txt LOCALLY</i> или <i>Cookie-Editor</i>).\n"
                "2. Войдите в свои аккаунты (Instagram, YouTube) в браузере.\n"
                "3. Экспортируйте cookies в формате <b>Netscape</b>.\n"
                "4. <b>Отправьте полученный файл <code>cookies.txt</code> прямо в этот чат Telegram как документ!</b>\n\n"
                "Бот автоматически проверит и установит файл."
            )
        else:
            return (
                "🍪 <b>Статус Cookies:</b>\n\n"
                "⚠️ <b>Файл cookies не подключен.</b>\n\n"
                "Для скачивания историй Instagram и обхода ограничений требуются cookies.\n"
                "Настройка и загрузка cookies доступны только администратору бота."
            )


async def get_admin_content(tab: str = "stats", period: str = "day") -> str:
    if tab == "server":
        return get_server_status_text()
    elif tab == "cookies":
        return get_cookies_status_text(is_admin=True)
    else:
        summary = await stats_service.get_stats(period)
        return stats_service.format_stats_message(summary)


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 <b>Привет! Я PipeGrab — бот для быстрого скачивания медиа.</b>\n\n"
        "Отправьте мне ссылку, и я мгновенно пришлю видео, аудио или фото в оригинальном качестве.\n\n"
        "<b>Поддерживаемые платформы:</b>\n"
        "• 🎬 <b>YouTube</b> — видео, Shorts, плейлисты и MP3\n"
        "• 🎵 <b>TikTok</b> — видео без водяных знаков и фото-альбомы с музыкой\n"
        "• 📸 <b>Instagram</b> — Reels, посты, карусели и истории\n"
        "• 📌 <b>Pinterest</b> — фото в исходном качестве, альбомы, GIF и видео\n"
        "• 🐦 <b>Twitter / X</b> | 🤖 <b>Reddit</b>\n\n"
        "💡 <i>Отправьте ссылку прямо в чат или используйте инлайн-режим:</i> <code>@имя_бота &lt;ссылка&gt;</code>"
    )
    await message.answer(welcome_text, reply_markup=get_start_keyboard(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    is_admin = bool(settings.admin_id and message.from_user and message.from_user.id == settings.admin_id)
    text = get_help_text(is_admin=is_admin)
    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "help_info")
async def handle_help_callback(callback: types.CallbackQuery):
    is_admin = bool(settings.admin_id and callback.from_user and callback.from_user.id == settings.admin_id)
    text = get_help_text(is_admin=is_admin)
    if isinstance(callback.message, types.Message):
        await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.message(Command("cookies"))
async def cmd_cookies(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    is_admin = bool(settings.admin_id and user_id == settings.admin_id)
    text = get_cookies_status_text(is_admin=is_admin)
    if is_admin:
        kb = get_admin_keyboard(active_tab="cookies", active_period="day")
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


async def download_telegram_document(bot: Bot, doc: types.Document, destination: Path) -> Path:
    """
    Safely download a document from Telegram.
    Handles local Bot API path remapping, local file reading,
    and falls back to HTTP streaming if local filesystem access fails.
    """
    try:
        await bot.download(doc, destination=destination)
        if destination.exists() and destination.stat().st_size > 0:
            return destination
    except Exception as e:
        logger.warning(f"Standard bot.download failed: {e}. Attempting fallback...")

    # Fallback: inspect file info returned by Bot API
    file_info = await bot.get_file(doc.file_id)
    if not file_info.file_path:
        raise RuntimeError("Telegram Bot API did not return file_path")

    # 1. Direct path check if it's already an existing file
    raw_p = Path(file_info.file_path)
    if raw_p.exists():
        destination.write_bytes(raw_p.read_bytes())
        return destination

    # 2. Check via wrap_local_file if local Bot API is configured
    if hasattr(bot.session.api, "wrap_local_file"):
        local_p = Path(bot.session.api.wrap_local_file.to_local(file_info.file_path))
        if local_p.exists():
            destination.write_bytes(local_p.read_bytes())
            return destination

    # 3. Direct HTTP fetch fallback
    rel_path = file_info.file_path
    if hasattr(bot.session.api, "wrap_local_file") and hasattr(bot.session.api.wrap_local_file, "server_path"):
        try:
            rel_path = str(Path(file_info.file_path).relative_to(bot.session.api.wrap_local_file.server_path))
        except Exception:
            pass
    url = bot.session.api.file_url(bot.token, rel_path)
    async with aiofiles.open(destination, "wb") as f:
        async for chunk in bot.session.stream_content(url=url, timeout=30, chunk_size=65536):
            await f.write(chunk)
    return destination


@router.message(F.document)
async def handle_document_upload(message: types.Message):
    doc = message.document
    if not doc or not message.from_user or not message.bot:
        return

    filename = (doc.file_name or "").lower()

    # Process if file is cookies.txt or contains cookie and ends in .txt
    if not (filename == "cookies.txt" or ("cookie" in filename and filename.endswith(".txt"))):
        return

    # Check admin privileges: only ADMIN_ID can upload/modify cookies
    user_id = message.from_user.id
    if not settings.admin_id or user_id != settings.admin_id:
        await message.reply(
            "⛔️ <b>Доступ ограничен.</b> Только администратор бота может загружать и изменять cookies.",
            parse_mode="HTML"
        )
        return

    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await message.reply("❌ Файл слишком большой для cookies (максимум 5 МБ).")
        return

    status_msg = await message.reply("⏳ <i>Проверяю и сохраняю cookies...</i>", parse_mode="HTML")

    temp_path = settings.downloads_dir / f"temp_cookies_{user_id}.txt"
    try:
        await download_telegram_document(message.bot, doc, destination=temp_path)
        content = temp_path.read_text(encoding="utf-8", errors="ignore")

        # Validate Netscape format
        if not validate_netscape_cookies(content):
            await status_msg.edit_text(
                "❌ <b>Файл не распознан как Netscape cookies.txt.</b>\n\n"
                "Убедитесь, что вы экспортировали cookies именно в формате <b>Netscape</b> "
                "(строки с табуляцией, начинающиеся с домена).",
                parse_mode="HTML"
            )
            return

        services = parse_cookie_services(content)

        # Move to persistent cookies location
        settings.cookies_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_path), str(settings.cookies_file))
        file_size_kb = settings.cookies_file.stat().st_size / 1024
        logger.info(f"Updated cookies file: {settings.cookies_file} ({file_size_kb:.1f} KB), detected: {services}")

        services_str = "\n".join(f"• {s}" for s in services) if services else "• Общие cookies"

        await status_msg.edit_text(
            f"✅ <b>Файл cookies успешно сохранён и подключён!</b>\n\n"
            f"📁 <b>Путь:</b> <code>{settings.cookies_file}</code>\n"
            f"📦 <b>Размер:</b> {file_size_kb:.1f} КБ\n"
            f"🌐 <b>Обнаруженные сервисы:</b>\n{services_str}\n\n"
            f"🎉 Теперь бот может скачивать истории Instagram, посты Reddit и другие авторизованные медиа!",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to process cookies upload: {e}")
        await status_msg.edit_text(f"❌ Ошибка при обработке файла: {e}")
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    if not settings.admin_id or user_id != settings.admin_id:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    text = await get_admin_content(tab="stats", period="day")
    kb = get_admin_keyboard(active_tab="stats", active_period="day")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    is_admin = bool(settings.admin_id and user_id == settings.admin_id)
    if is_admin:
        text = get_server_status_text()
        kb = get_admin_keyboard(active_tab="server", active_period="day")
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        text = (
            "🟢 <b>PipeGrab работает в штатном режиме!</b>\n\n"
            "Просто отправьте мне ссылку для скачивания или используйте инлайн-режим: <code>@имя_бота &lt;ссылка&gt;</code>."
        )
        await message.answer(text, parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    if not settings.admin_id or user_id != settings.admin_id:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    summary = await stats_service.get_stats("day")
    text = stats_service.format_stats_message(summary)
    kb = get_stats_keyboard("day")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("stats:"))
async def handle_stats_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not settings.admin_id or user_id != settings.admin_id:
        await callback.answer("⛔ У вас нет доступа к этой команде.", show_alert=True)
        return

    data = callback.data or ""
    parts = data.split(":")
    if len(parts) == 2:
        period = parts[1]
    elif len(parts) == 3 and parts[1] == "refresh":
        period = parts[2]
    else:
        period = "day"

    if period not in ("day", "week", "month", "all"):
        period = "day"

    summary = await stats_service.get_stats(period)
    text = stats_service.format_stats_message(summary)
    kb = get_stats_keyboard(period)

    if isinstance(callback.message, types.Message):
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            await callback.answer("Данные обновлены 🔄" if "refresh" in data else None)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                await callback.answer("Статистика актуальна ✅")
            else:
                logger.warning(f"Failed to edit stats message: {e}")
                await callback.answer()
    else:
        await callback.answer()


@router.callback_query(F.data.startswith("admin:"))
async def handle_admin_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not settings.admin_id or user_id != settings.admin_id:
        await callback.answer("⛔ У вас нет доступа к этой команде.", show_alert=True)
        return

    data = callback.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "tab"

    tab = "stats"
    period = "day"

    if action == "tab":
        tab = parts[2] if len(parts) > 2 else "stats"
        period = parts[3] if len(parts) > 3 else "day"
    elif action == "stats":
        tab = "stats"
        period = parts[2] if len(parts) > 2 else "day"
    elif action == "refresh":
        tab = parts[2] if len(parts) > 2 else "stats"
        period = parts[3] if len(parts) > 3 else "day"

    if period not in ("day", "week", "month", "all"):
        period = "day"
    if tab not in ("stats", "server", "cookies"):
        tab = "stats"

    text = await get_admin_content(tab=tab, period=period)
    kb = get_admin_keyboard(active_tab=tab, active_period=period)

    if isinstance(callback.message, types.Message):
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            await callback.answer("Данные обновлены 🔄" if action == "refresh" else None)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                await callback.answer("Данные актуальны ✅")
            else:
                logger.warning(f"Failed to edit admin message: {e}")
                await callback.answer()
    else:
        await callback.answer()

