import logging
import os
import shutil
import time
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command

from bot.config import settings
from bot.services.queue_manager import queue_manager

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
        "🍪 <b>Истории Instagram и Cookies:</b>\n"
        "Истории нельзя смотреть анонимно. Используйте команду /cookies для проверки и добавления cookies."
    )
    await message.answer(welcome_text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 <b>Справка по использованию:</b>\n\n"
        "1️⃣ <b>Отправка ссылки:</b> скопируйте ссылку из приложения или браузера и отправьте боту.\n"
        "2️⃣ <b>Pinterest:</b> бот автоматически определяет тип контента (фото, альбом, GIF-анимация или видео) и скачивает в максимальном качестве.\n"
        "3️⃣ <b>YouTube:</b> бот предложит скачать видео в MP4 или аудиодорожку в MP3.\n"
        "4️⃣ <b>Плейлисты и доски:</b> бот предложит выбрать количество элементов для загрузки.\n"
        "5️⃣ <b>Instagram Истории:</b> отправьте команду /cookies для инструкции по добавлению файла <code>cookies.txt</code>.\n\n"
        "<b>Команды бота:</b>\n"
        "/start — Главное меню\n"
        "/help — Справка и возможности\n"
        "/cookies — Статус и настройка авторизации Instagram / YouTube\n"
        "/status — Состояние сервера и лимиты"
    )
    await message.answer(help_text, parse_mode="HTML")


@router.message(Command("cookies"))
async def cmd_cookies(message: types.Message):
    if settings.has_cookies:
        stat = settings.cookies_file.stat()
        file_size_kb = stat.st_size / 1024
        mtime = time.strftime('%d.%m.%Y %H:%M', time.localtime(stat.st_mtime))

        services = []
        try:
            content = settings.cookies_file.read_text(encoding="utf-8", errors="ignore")
            if "instagram.com" in content:
                has_session = "sessionid" in content
                services.append(f"Instagram ({'авторизован ✅' if has_session else 'нет sessionid ⚠️'})")
            if "youtube.com" in content or "google.com" in content:
                services.append("YouTube / Google ✅")
            if "tiktok.com" in content:
                services.append("TikTok ✅")
            if "pinterest.com" in content:
                services.append("Pinterest ✅")
        except Exception:
            pass

        services_str = "\n".join(f"• {s}" for s in services) if services else "• Общие cookies"

        text = (
            "🍪 <b>Статус Cookies:</b>\n\n"
            "✅ <b>Файл cookies подключен и активен!</b>\n"
            f"📁 <b>Путь:</b> <code>{settings.cookies_file}</code>\n"
            f"📦 <b>Размер:</b> {file_size_kb:.1f} КБ\n"
            f"📅 <b>Обновлен:</b> {mtime}\n\n"
            f"🌐 <b>Найденные авторизации:</b>\n{services_str}\n\n"
            "<i>Чтобы обновить куки, просто отправьте новый файл <code>cookies.txt</code> в этот чат.</i>"
        )
    else:
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
            "2. Войдите в свой Instagram в браузере.\n"
            "3. Экспортируйте cookies в формате <b>Netscape</b>.\n"
            "4. <b>Отправьте полученный файл <code>cookies.txt</code> прямо в этот чат Telegram как документ!</b>\n\n"
            "Бот автоматически проверит и установит файл."
        )
    await message.answer(text, parse_mode="HTML")


@router.message(F.document)
async def handle_document_upload(message: types.Message):
    doc = message.document
    filename = (doc.file_name or "").lower()

    # Process if file is cookies.txt or contains cookie and ends in .txt
    if not (filename == "cookies.txt" or ("cookie" in filename and filename.endswith(".txt"))):
        return

    # Check admin privileges if configured
    if settings.admin_id and message.from_user.id != settings.admin_id:
        await message.reply(
            "⛔️ <b>Доступ ограничен.</b> Только администратор бота может загружать cookies.",
            parse_mode="HTML"
        )
        return

    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await message.reply("❌ Файл слишком большой для cookies (максимум 5 МБ).")
        return

    status_msg = await message.reply("⏳ <i>Проверяю и сохраняю cookies...</i>", parse_mode="HTML")

    temp_path = settings.downloads_dir / f"temp_cookies_{message.from_user.id}.txt"
    try:
        await message.bot.download(doc, destination=temp_path)
        content = temp_path.read_text(encoding="utf-8", errors="ignore")

        # Validate Netscape format
        is_netscape = (
            "# Netscape HTTP Cookie File" in content or
            "# HTTP Cookie File" in content or
            any("\t" in line and len(line.split("\t")) >= 7 for line in content.splitlines() if not line.startswith("#"))
        )

        if not is_netscape:
            await status_msg.edit_text(
                "❌ <b>Файл не распознан как Netscape cookies.txt.</b>\n\n"
                "Убедитесь, что вы экспортировали cookies именно в формате <b>Netscape</b> "
                "(строки с табуляцией, начинающиеся с домена).",
                parse_mode="HTML"
            )
            return

        services = []
        if "instagram.com" in content:
            has_session = "sessionid" in content
            services.append(f"Instagram ({'найден sessionid ✅' if has_session else 'нет sessionid ⚠️'})")
        if "youtube.com" in content or "google.com" in content:
            services.append("YouTube / Google ✅")
        if "tiktok.com" in content:
            services.append("TikTok ✅")
        if "pinterest.com" in content:
            services.append("Pinterest ✅")

        # Move to persistent cookies location
        settings.cookies_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_path), str(settings.cookies_file))

        services_str = "\n".join(f"• {s}" for s in services) if services else "• Общие cookies"
        file_size_kb = settings.cookies_file.stat().st_size / 1024

        await status_msg.edit_text(
            f"✅ <b>Файл cookies успешно сохранён и подключён!</b>\n\n"
            f"📁 <b>Путь:</b> <code>{settings.cookies_file}</code>\n"
            f"📦 <b>Размер:</b> {file_size_kb:.1f} КБ\n"
            f"🌐 <b>Обнаруженные сервисы:</b>\n{services_str}\n\n"
            f"🎉 Теперь бот может скачивать истории Instagram и авторизованный контент!",
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
    total, used, free = shutil.disk_usage(settings.downloads_dir)
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
