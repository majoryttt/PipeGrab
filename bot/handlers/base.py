import aiofiles
import aiohttp
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Tuple
from aiogram import Bot, Router, types, F
from aiogram.filters import CommandStart, Command

from bot.config import settings
from bot.services.queue_manager import queue_manager
from bot.services.cookie_utils import (
    validate_netscape_cookies,
    parse_cookie_services,
    normalize_vk_cookies_content,
)

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
        "• 🔵 <b>VK (ВКонтакте)</b> — видео, клипы, посты со стены, фото и карусели (включая приватный контент через cookies и VK API)\n"
        "• 🐦 <b>Twitter / X</b> — видео и клипы\n\n"
        "🚀 <b>Как пользоваться:</b>\n"
        "Просто отправьте мне ссылку в сообщении!\n\n"
        "🍪 <b>Авторизация и Cookies (/cookies):</b>\n"
        "Истории Instagram, закрытые посты ВКонтакте и защита от проверок доступны при подключении cookies.\n\n"
        "🔑 <b>Прямая загрузка ВК (/vk_token):</b>\n"
        "Для скачивания любых видео и клипов ВКонтакте без ограничений зарубежных дата-центров используйте команду /vk_token."
    )
    await message.answer(welcome_text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 <b>Справка по использованию:</b>\n\n"
        "1️⃣ <b>Отправка ссылки:</b> скопируйте ссылку из приложения или браузера и отправьте боту.\n"
        "2️⃣ <b>VK (ВКонтакте):</b> скачивайте видео, клипы, посты со стены с фотографиями и альбомы (карусели).\n"
        "3️⃣ <b>Pinterest:</b> бот автоматически определяет тип контента (фото, альбом, GIF-анимация или видео) и скачивает в максимальном качестве.\n"
        "4️⃣ <b>YouTube:</b> бот предложит скачать видео в MP4 или аудиодорожку в MP3.\n"
        "5️⃣ <b>Плейлисты и доски:</b> бот предложит выбрать количество элементов для загрузки.\n"
        "6️⃣ <b>Авторизация и Cookies:</b> отправьте команду /cookies для инструкции по добавлению файла <code>cookies.txt</code> (для историй Instagram и закрытых постов).\n"
        "7️⃣ <b>Прямой доступ к видео ВК:</b> отправьте команду /vk_token для подключения официального токена ВК (решает проблему блокировок зарубежных IP).\n"
        "8️⃣ <b>Кружки (Video Notes):</b> ответьте командой /circle на любое видео или отправьте <code>/circle &lt;ссылка&gt;</code>.\n\n"
        "<b>Команды бота:</b>\n"
        "/start — Главное меню\n"
        "/help — Справка и возможности\n"
        "/circle — Превратить видео или ссылку в Telegram-кружок\n"
        "/vk_token — Статус и настройка прямого скачивания видео ВКонтакте\n"
        "/cookies — Статус и настройка авторизации Instagram / ВКонтакте / YouTube\n"
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
            services = parse_cookie_services(content)
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
            "• Доступ к <b>закрытым или ограниченным видео и записям ВКонтакте</b> (видео «только для друзей», закрытые группы)\n"
            "• Доступ к приватным или возрастным видео YouTube\n"
            "• Защита от блокировок и проверок роботов\n\n"
            "<b>Как установить cookies:</b>\n"
            "1. Установите расширение для браузера Chrome/Firefox (например, <i>Get cookies.txt LOCALLY</i> или <i>Cookie-Editor</i>).\n"
            "2. Войдите в свои аккаунты (Instagram, ВКонтакте, YouTube) в браузере.\n"
            "3. Экспортируйте cookies в формате <b>Netscape</b>.\n"
            "4. <b>Отправьте полученный файл <code>cookies.txt</code> прямо в этот чат Telegram как документ!</b>\n\n"
            "Бот автоматически проверит и установит файл."
        )
    await message.answer(text, parse_mode="HTML")


def extract_token_from_input(text: str) -> str:
    """
    Extract raw access_token from various user inputs:
    - full oauth URL: https://oauth.vk.com/blank.html#access_token=vk1.a...&expires_in=0
    - key=value string: access_token=vk1.a...
    - raw token: vk1.a...
    """
    text = text.strip()
    if "#" in text:
        text = text.split("#", 1)[1]
    if "?" in text:
        text = text.split("?", 1)[1]
    if "access_token=" in text:
        for part in text.split("&"):
            if part.startswith("access_token="):
                return part.split("access_token=", 1)[1].strip()
    return text.strip()


async def validate_vk_token(token: str) -> Tuple[bool, str]:
    """
    Check if a VK access token is valid by calling users.get.
    Returns (is_valid, user_or_error_info).
    """
    endpoint = f"https://api.vk.com/method/users.get?v=5.199&access_token={token}"
    try:
        from bot.services.http_client import http_client
        session = await http_client.get_session()
        async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=10), proxy=settings.vk_proxy) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            data = await resp.json()
            if "error" in data:
                err = data["error"]
                return False, f"[{err.get('error_code')}] {err.get('error_msg')}"
            items = data.get("response", [])
            if items:
                u = items[0]
                return True, f"{u.get('first_name', '')} {u.get('last_name', '')} (ID: {u.get('id')})".strip()
            return True, "Авторизован"
    except Exception as e:
        return False, str(e)


@router.message(Command("vk_token"))
async def cmd_vk_token(message: types.Message):
    if settings.admin_id and message.from_user.id != settings.admin_id:
        await message.reply(
            "⛔️ <b>Доступ ограничен.</b> Только администратор бота может настраивать токен ВКонтакте.",
            parse_mode="HTML"
        )
        return

    command_args = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            command_args = parts[1].strip()

    if not command_args:
        active_token = settings.active_vk_token
        if active_token:
            masked = f"{active_token[:6]}...{active_token[-4:]}" if len(active_token) > 10 else "***"
            status_line = f"✅ <b>Токен подключён и активен:</b> <code>{masked}</code>\n"
        else:
            status_line = "⚠️ <b>Токен не установлен.</b> Приватные/ограниченные видео ВК могут блокироваться дата-центром.\n"

        auth_url = (
            "https://oauth.vk.com/authorize?"
            "client_id=2685278&scope=1073737727&redirect_uri=https://oauth.vk.com/blank.html&response_type=token"
        )

        text = (
            "🔵 <b>Настройка VK API (VK_USER_TOKEN):</b>\n\n"
            f"{status_line}\n"
            "Токен пользователя позволяет скачивать любые видео («только для зарегистрированных», приватные) "
            "напрямую через мобильный API ВК на максимальной скорости и без блокировок зарубежных IP.\n\n"
            "<b>Инструкция по получению токена (30 секунд):</b>\n"
            "1. Войдите в свой аккаунт ВКонтакте в браузере.\n"
            f"2. Откройте ссылку авторизации Kate Mobile:\n"
            f"<a href=\"{auth_url}\">👉 Нажмите сюда для получения токена</a>\n"
            "<i>(Или перейдите на сайт <a href=\"https://vkhost.github.io/\">vkhost.github.io</a> и выберите Kate Mobile)</i>\n"
            "3. Нажмите кнопку <b>«Разрешить»</b>.\n"
            "4. В адресной строке браузера скопируйте полученную ссылку (или значение <code>access_token</code>).\n"
            "5. Отправьте боту команду:\n"
            "<code>/vk_token ВАШ_ТОКЕН</code>\n\n"
            "<i>(Также токен можно прописать в файле .env: VK_USER_TOKEN=...)</i>"
        )
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        return

    raw_input = command_args
    token = extract_token_from_input(raw_input)
    if not token or len(token) < 15:
        await message.reply("❌ Не удалось распознать токен. Проверьте отправленное значение.")
        return

    status_msg = await message.reply("⏳ <i>Проверяю токен через API ВКонтакте...</i>", parse_mode="HTML")
    is_valid, user_info = await validate_vk_token(token)

    if not is_valid:
        await status_msg.edit_text(
            f"❌ <b>Токен недействителен:</b> {user_info}\n\n"
            "Убедитесь, что вы скопировали полный <code>access_token</code> без лишних символов.",
            parse_mode="HTML"
        )
        return

    try:
        settings.vk_token_file.parent.mkdir(parents=True, exist_ok=True)
        settings.vk_token_file.write_text(token, encoding="utf-8")
        settings.vk_user_token = token

        await status_msg.edit_text(
            "✅ <b>Токен ВКонтакте успешно подключён и сохранён!</b>\n\n"
            f"👤 Авторизован как: <b>{user_info}</b>\n"
            f"📁 Сохранено в: <code>{settings.vk_token_file}</code>\n\n"
            "🎉 Теперь любые видео и клипы ВКонтакте скачиваются на максимальной скорости в обход всех веб-блокировок!",
            parse_mode="HTML"
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка сохранения токена: {e}")


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
    async with bot.session.stream_content(url=url, timeout=30, chunk_size=65536) as stream:
        async with aiofiles.open(destination, "wb") as f:
            async for chunk in stream:
                await f.write(chunk)
    return destination


@router.message(F.document)
async def handle_document_upload(message: types.Message):
    doc = message.document
    filename = (doc.file_name or "").lower()

    # Handle VK token file upload
    if filename in ["vk_token.txt", "token.txt"]:
        if settings.admin_id and message.from_user.id != settings.admin_id:
            await message.reply(
                "⛔️ <b>Доступ ограничен.</b> Только администратор бота может загружать токен ВКонтакте.",
                parse_mode="HTML"
            )
            return

        temp_path = settings.downloads_dir / f"temp_token_{message.from_user.id}.txt"
        try:
            await download_telegram_document(message.bot, doc, destination=temp_path)
            content = temp_path.read_text(encoding="utf-8", errors="ignore").strip()
            token = extract_token_from_input(content)
            is_valid, user_info = await validate_vk_token(token)
            if is_valid:
                settings.vk_token_file.parent.mkdir(parents=True, exist_ok=True)
                settings.vk_token_file.write_text(token, encoding="utf-8")
                settings.vk_user_token = token
                await message.reply(
                    f"✅ <b>Токен ВКонтакте успешно подключён из файла!</b>\n\n👤 Авторизован как: <b>{user_info}</b>",
                    parse_mode="HTML"
                )
                return
            else:
                await message.reply(f"❌ Токен в файле недействителен: {user_info}")
                return
        except Exception as e:
            await message.reply(f"❌ Ошибка обработки файла токена: {e}")
            return
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

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

        # Normalize VK cookies across domains (.vk.com, .vk.ru, .vkvideo.ru)
        content = normalize_vk_cookies_content(content)
        temp_path.write_text(content, encoding="utf-8")

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
            f"🎉 Теперь бот может скачивать истории Instagram, закрытый контент ВКонтакте и другие авторизованные медиа!",
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

    if settings.active_vk_token:
        masked = f"{settings.active_vk_token[:6]}...{settings.active_vk_token[-4:]}" if len(settings.active_vk_token) > 10 else "***"
        vk_token_status = f"Подключён ✅ ({masked})"
    else:
        vk_token_status = "Не настроен ⚠️ (/vk_token)"

    status_text = (
        "📊 <b>Статус бота:</b>\n\n"
        f"• <b>Режим Bot API:</b> {api_mode}\n"
        f"• <b>Cookies:</b> {cookies_status}\n"
        f"• <b>VK API Token:</b> {vk_token_status}\n"
        f"• <b>Активных загрузок:</b> {len(queue_manager._active_users)}\n"
        f"• <b>Свободно места на диске:</b> {free // (1024 * 1024 * 1024)} ГБ / {total // (1024 * 1024 * 1024)} ГБ\n"
        f"• <b>Лимит размера файла:</b> {settings.max_file_size_mb} МБ\n"
    )
    await message.answer(status_text, parse_mode="HTML")
