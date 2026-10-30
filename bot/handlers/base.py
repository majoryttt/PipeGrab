import logging
import shutil
import time
from pathlib import Path

import aiofiles
from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart

from bot.config import settings
from bot.keyboards.inline import get_stats_keyboard
from bot.services.cookie_utils import (
    parse_cookie_services,
    validate_netscape_cookies,
)
from bot.services.queue_manager import queue_manager
from bot.services.stats_service import stats_service

logger = logging.getLogger(__name__)

router = Router(name="base_router")


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 <b>Привет! Я PipeGrab — бот для скачивания медиа с популярных платформ.</b>\n\n"
        "<b>Поддерживаемые сервисы:</b>\n"
        "• 🎬 <b>YouTube</b> — видео, Shorts, аудио (MP3) и плейлисты\n"
        "• 🎵 <b>TikTok</b> — видео без водяных знаков\n"
        "• 📸 <b>Instagram</b> — Reels, видео, публикации, а также <b>истории</b> (через cookies)\n"
        "• 📌 <b>Pinterest</b> — фото в оригинальном качестве, GIF, альбомы (карусели) и видео\n"
        "• 🐦 <b>Twitter / X</b> — видео и клипы\n\n"
        "🚀 <b>Как пользоваться:</b>\n"
        "Просто отправьте мне ссылку в сообщении!\n\n"
        "🍪 <b>Авторизация и Cookies (/cookies):</b>\n"
        "Истории Instagram и защита от проверок доступны при подключении cookies."
    )
    await message.answer(welcome_text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    is_admin = bool(settings.admin_id and message.from_user and message.from_user.id == settings.admin_id)
    cookies_line = (
        "5️⃣ <b>Авторизация и Cookies:</b> отправьте команду /cookies для инструкции по добавлению файла <code>cookies.txt</code> (для историй Instagram).\n"
        if is_admin else
        "5️⃣ <b>Авторизация и Cookies:</b> отправьте команду /cookies для проверки статуса авторизации Instagram / YouTube.\n"
    )
    help_text = (
        "📖 <b>Справка по использованию:</b>\n\n"
        "1️⃣ <b>Отправка ссылки:</b> скопируйте ссылку из приложения или браузера и отправьте боту.\n"
        "2️⃣ <b>Pinterest:</b> бот автоматически определяет тип контента (фото, альбом, GIF-анимация или видео) и скачивает в максимальном качестве.\n"
        "3️⃣ <b>YouTube:</b> бот предложит скачать видео в MP4 или аудиодорожку в MP3.\n"
        "4️⃣ <b>Плейлисты и доски:</b> бот предложит выбрать количество элементов для загрузки.\n"
        f"{cookies_line}"
        "6️⃣ <b>Кружки (Video Notes):</b> ответьте командой /circle на любое видео или отправьте <code>/circle &lt;ссылка&gt;</code>.\n\n"
        "<b>Команды бота:</b>\n"
        "/start — Главное меню\n"
        "/help — Справка и возможности\n"
        "/circle — Превратить видео или ссылку в Telegram-кружок\n"
        "/cookies — Статус и настройка авторизации Instagram / YouTube\n"
        "/status — Состояние сервера и лимиты"
        + ("\n/stats — Статистика загрузок и аналитика" if is_admin else "")
    )
    await message.answer(help_text, parse_mode="HTML")


@router.message(Command("cookies"))
async def cmd_cookies(message: types.Message):
    is_admin = bool(settings.admin_id and message.from_user and message.from_user.id == settings.admin_id)

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
            "\n\n<i>Чтобы обновить куки, просто отправьте новый файл <code>cookies.txt</code> в этот чат.</i>"
            if is_admin else ""
        )

        text = (
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
            text = (
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
            text = (
                "🍪 <b>Статус Cookies:</b>\n\n"
                "⚠️ <b>Файл cookies не подключен.</b>\n\n"
                "Для скачивания историй Instagram и обхода ограничений требуются cookies.\n"
                "Настройка и загрузка cookies доступны только администратору бота."
            )
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
            f"🎉 Теперь бот может скачивать истории Instagram и другие авторизованные медиа!",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to process cookies upload: {e}")
        await status_msg.edit_text(f"❌ Ошибка при обработке файла: {e}")
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    total, _used, free = shutil.disk_usage(settings.downloads_dir)
    api_mode = "Локальный (до 2 ГБ)" if settings.local_bot_api_url else "Стандартный Bot API (до 50 МБ)"

    if settings.has_cookies:
        cookies_status = "Подключены ✅"
    else:
        cookies_status = "Отсутствуют ⚠️ (истории Instagram недоступны)"

    status_text = (
        "📊 <b>Статус бота:</b>\n\n"
        f"• <b>Режим Bot API:</b> {api_mode}\n"
        f"• <b>Cookies:</b> {cookies_status}\n"
        f"• <b>Активных загрузок:</b> {len(queue_manager._active_users)}\n"
        f"• <b>Свободно места на диске:</b> {free // (1024 * 1024 * 1024)} ГБ / {total // (1024 * 1024 * 1024)} ГБ\n"
        f"• <b>Лимит размера файла:</b> {settings.max_file_size_mb} МБ\n"
    )
    await message.answer(status_text, parse_mode="HTML")


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

