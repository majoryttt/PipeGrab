import asyncio
import html
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, List

from aiogram import Router, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, CallbackQuery, InputMediaPhoto, InputMediaVideo

from bot.config import settings
from bot.keyboards.inline import (
    get_download_format_kb,
    get_playlist_kb,
    get_cached_url,
)
from bot.services.downloader import (
    downloader_service,
    extract_first_url,
    detect_platform,
    Platform,
    MediaType,
    DownloadProgress,
    MediaInfo,
)
from bot.services.queue_manager import queue_manager

logger = logging.getLogger(__name__)

router = Router(name="download_router")


def format_progress_bar(percent: float, length: int = 10) -> str:
    filled = int(length * percent / 100)
    filled = min(max(0, filled), length)
    return "█" * filled + "░" * (length - filled)


def format_size(bytes_num: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} TB"


def format_eta(seconds: int) -> str:
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02d}:{secs:02d}"


def truncate_text(text: str, max_length: int = 500) -> str:
    text = (text or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length - 3].rstrip() + "..."


def build_safe_caption(
    title: str,
    url: str,
    platform_name: str,
    uploader: Optional[str] = None,
    prefix: str = "🎬",
    extra: str = ""
) -> str:
    clean_title = truncate_text(title, 400)
    esc_title = html.escape(clean_title)
    esc_url = html.escape(url, quote=True)
    esc_platform = html.escape(platform_name)

    parts = [f"{prefix} <b>{esc_title}</b>"]
    if uploader:
        esc_uploader = html.escape(truncate_text(uploader, 100))
        parts.append(f"👤 <i>{esc_uploader}</i>")
    if extra:
        parts.append(html.escape(extra))
    parts.append(f"🔗 <a href='{esc_url}'>{esc_platform}</a>")

    caption = "\n".join(parts)
    if len(caption) > 1024:
        caption = caption[:1020] + "..."
    return caption


async def update_status_safely(message: types.Message, text: str):
    try:
        await message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        try:
            import re
            plain = re.sub(r'<[^>]+>', '', text)
            await message.edit_text(plain)
        except Exception:
            pass
    except Exception:
        # Ignore Telegram rate limits or identical content errors
        pass


@router.message(F.text)
async def handle_text_message(message: types.Message):
    text = message.text.strip()
    url = extract_first_url(text)

    if not url:
        return

    platform = detect_platform(url)
    user_id = message.from_user.id

    if not await queue_manager.can_user_download(user_id):
        await message.reply(
            "⏳ <b>У вас уже выполняется загрузка.</b>\n"
            "Пожалуйста, дождитесь её завершения перед отправкой новой ссылки.",
            parse_mode="HTML"
        )
        return

    # Fast path for direct-download platforms (TikTok, Instagram, Twitter/X, Pinterest single pins):
    # Skip get_info() to eliminate redundant metadata extraction and double network roundtrips.
    is_pinterest_board = (
        platform == Platform.PINTEREST and (
            "/boards/" in url or bool(re.search(r'pinterest\.[^/]+/[^/]+/[^/]+/?$', url))
        )
    )
    if platform != Platform.YOUTUBE and not is_pinterest_board:
        status_msg = await message.reply("⚡ <i>Загружаю...</i>", parse_mode="HTML")
        asyncio.create_task(
            run_download_task(
                chat_id=message.chat.id,
                user_id=user_id,
                url=url,
                status_msg=status_msg,
                audio_only=False
            )
        )
        return

    status_msg = await message.reply("🔍 <i>Получаю информацию...</i>", parse_mode="HTML")

    # Extract metadata for YouTube and playlists
    info = await downloader_service.get_info(url)

    # Check for authentication or privacy errors
    if info and info.error_message:
        if info.error_message in ["AUTH_REQUIRED_INSTAGRAM_STORY", "AUTH_REQUIRED_INSTAGRAM"]:
            await update_status_safely(
                status_msg,
                "🔒 <b>Для скачивания историй Instagram требуется авторизация (cookies).</b>\n\n"
                "Instagram не позволяет просматривать истории анонимно.\n\n"
                "<b>Как решить эту проблему:</b>\n"
                "1️⃣ Войдите в Instagram в браузере (на компьютере).\n"
                "2️⃣ Экспортируйте cookies с помощью расширения (например, <i>Get cookies.txt LOCALLY</i> или <i>Cookie-Editor</i> в формате Netscape).\n"
                "3️⃣ Отправьте полученный файл <code>cookies.txt</code> прямо в этот чат Telegram!\n\n"
                "<i>После загрузки cookies бот сможет скачивать любые истории и закрытый контент.</i>"
            )
            return
        elif info.error_message == "AUTH_REQUIRED":
            await update_status_safely(
                status_msg,
                "🔒 <b>Для этого контента требуется авторизация.</b>\n\n"
                "Сервис запросил вход в аккаунт или проверку безопасности.\n"
                "Вы можете загрузить файл <code>cookies.txt</code> в бот для авторизации."
            )
            return
        elif info.error_message == "PRIVATE_CONTENT":
            await update_status_safely(
                status_msg,
                "🔒 <b>Этот аккаунт или публикация является приватной.</b>\n"
                "Скачивание возможно только если в бот загружены cookies аккаунта, подписанного на этот профиль."
            )
            return

    if not info:
        await update_status_safely(
            status_msg,
            "❌ <b>Не удалось получить данные по ссылке.</b>\n"
            "Проверьте, что ссылка корректна и публикация доступна."
        )
        return

    # If it's a playlist or board
    if info.is_playlist and info.playlist_count > 0:
        source_label = "доска Pinterest" if platform == Platform.PINTEREST else "плейлист"
        items_label = "пинов" if platform == Platform.PINTEREST else "видео"
        esc_title = html.escape(truncate_text(info.title, 100))
        await update_status_safely(
            status_msg,
            f"📋 <b>Обнаружен(а) {source_label}:</b> <i>{esc_title}</i>\n"
            f"📊 Всего {items_label}: <b>{info.playlist_count}</b>\n\n"
            f"Выберите, сколько {items_label} скачать:"
        )
        await status_msg.edit_reply_markup(reply_markup=get_playlist_kb(url, info.playlist_count))
        return

    # If YouTube single video: offer format choice (Video or MP3)
    if platform == Platform.YOUTUBE:
        duration_str = f"⏱ Длительность: {format_eta(info.duration)}\n" if info.duration else ""
        esc_title = html.escape(truncate_text(info.title, 150))
        esc_uploader = html.escape(truncate_text(info.uploader, 100))
        await update_status_safely(
            status_msg,
            f"🎬 <b>{esc_title}</b>\n"
            f"👤 Автор: <i>{esc_uploader}</i>\n"
            f"{duration_str}\n"
            f"Выберите формат:"
        )
        await status_msg.edit_reply_markup(reply_markup=get_download_format_kb(url))
        return

    # For TikTok, Instagram, Twitter, Pinterest: download directly
    asyncio.create_task(
        run_download_task(
            chat_id=message.chat.id,
            user_id=user_id,
            url=url,
            status_msg=status_msg,
            audio_only=False
        )
    )


@router.callback_query(F.data.startswith("dl:"))
async def handle_download_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    action = parts[1]

    if action == "cancel":
        await callback.answer("Загрузка отменена")
        try:
            await callback.message.delete()
        except Exception:
            await callback.message.edit_text("❌ <i>Отменено</i>", parse_mode="HTML")
        return

    cid = parts[2]
    url = get_cached_url(cid)
    if not url:
        await callback.answer("Ссылка устарела, отправьте её снова", show_alert=True)
        return

    user_id = callback.from_user.id
    if not await queue_manager.can_user_download(user_id):
        await callback.answer("У вас уже есть активная загрузка!", show_alert=True)
        return

    await callback.answer()
    audio_only = (action == "audio")

    asyncio.create_task(
        run_download_task(
            chat_id=callback.message.chat.id,
            user_id=user_id,
            url=url,
            status_msg=callback.message,
            audio_only=audio_only
        )
    )


@router.callback_query(F.data.startswith("pl:"))
async def handle_playlist_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    choice = parts[1]
    cid = parts[2]

    url = get_cached_url(cid)
    if not url:
        await callback.answer("Ссылка устарела, отправьте её снова", show_alert=True)
        return

    user_id = callback.from_user.id
    if not await queue_manager.can_user_download(user_id):
        await callback.answer("У вас уже есть активная загрузка!", show_alert=True)
        return

    await callback.answer()

    audio_only = choice.startswith("a")
    count_str = choice[1:] if audio_only else choice
    try:
        max_count = int(count_str)
    except ValueError:
        max_count = 5

    asyncio.create_task(
        run_playlist_task(
            chat_id=callback.message.chat.id,
            user_id=user_id,
            url=url,
            status_msg=callback.message,
            max_items=max_count,
            audio_only=audio_only
        )
    )


async def run_download_task(
    chat_id: int,
    user_id: int,
    url: str,
    status_msg: types.Message,
    audio_only: bool
):
    media: Optional[MediaInfo] = None
    action_task = None
    try:
        # Background chat action loop to show native Telegram uploading indicator
        async def _action_loop():
            try:
                act = "upload_document" if audio_only else "upload_video"
                while True:
                    await status_msg.bot.send_chat_action(chat_id=chat_id, action=act)
                    await asyncio.sleep(4.5)
            except (asyncio.CancelledError, Exception):
                pass

        action_task = asyncio.create_task(_action_loop())

        async with queue_manager.acquire(user_id):
            start_time = time.time()
            last_edit_time = 0.0

            async def progress_hook(p: DownloadProgress):
                nonlocal last_edit_time
                now = time.time()
                # Fast downloads (< 2.5s) don't need progress bar edits to minimize latency
                if now - start_time < 2.5:
                    return
                if now - last_edit_time < 3.5 and p.status != "finished":
                    return
                last_edit_time = now

                bar = format_progress_bar(p.percent)
                downloaded = format_size(p.downloaded_bytes)
                total = format_size(p.total_bytes) if p.total_bytes > 0 else "..."
                speed = f"{format_size(int(p.speed))}/s" if p.speed > 0 else ""
                eta = f" | ETA: {format_eta(p.eta)}" if p.eta > 0 else ""

                text = (
                    f"⬇️ <b>Скачивание...</b>\n"
                    f"<code>[{bar}] {p.percent:.1f}%</code>\n"
                    f"💾 {downloaded} / {total} | {speed}{eta}"
                )
                await update_status_safely(status_msg, text)

            media = await downloader_service.download_media(
                url=url,
                audio_only=audio_only,
                progress_callback=progress_hook
            )

            # Check if error message returned
            if media and media.error_message:
                if media.error_message in ["AUTH_REQUIRED_INSTAGRAM_STORY", "AUTH_REQUIRED_INSTAGRAM"]:
                    await update_status_safely(
                        status_msg,
                        "🔒 <b>Для скачивания историй Instagram требуется авторизация (cookies).</b>\n\n"
                        "Отправьте файл <code>cookies.txt</code> прямо в этот чат для активации доступа."
                    )
                else:
                    await update_status_safely(status_msg, "❌ <b>Требуется авторизация для доступа к этому медиа.</b>")
                return

            has_files = media and (
                (media.file_path and media.file_path.exists()) or
                (media.file_paths and any(f.exists() for f in media.file_paths))
            )

            if not media or not has_files:
                await update_status_safely(
                    status_msg,
                    "❌ <b>Ошибка при загрузке медиафайла.</b>\n"
                    "Попробуйте другую ссылку или проверьте доступность контента."
                )
                return

            # Check file size against configured limit
            if media.file_size > settings.max_file_size_bytes:
                size_mb = media.file_size / (1024 * 1024)
                await update_status_safely(
                    status_msg,
                    f"⚠️ <b>Файл слишком большой ({size_mb:.1f} МБ).</b>\n"
                    f"Максимальный размер для отправки: {settings.max_file_size_mb} МБ.\n"
                    f"💡 Попробуйте скачать аудио (MP3) или подключить Local Bot API."
                )
                return

            await update_status_safely(status_msg, "📤 <i>Отправка файла в Telegram...</i>")

            # Send media according to media_type
            try:
                platform_title = media.platform.value
                caption = build_safe_caption(
                    title=media.title,
                    url=url,
                    platform_name=platform_title,
                    prefix="🎬"
                )

                try:
                    if media.media_type == MediaType.PHOTO:
                        p_caption = build_safe_caption(
                            title=media.title,
                            url=url,
                            platform_name=platform_title,
                            uploader=media.uploader,
                            prefix="📌"
                        )
                        await status_msg.bot.send_photo(
                            chat_id=chat_id,
                            photo=FSInputFile(str(media.file_path)),
                            caption=p_caption,
                            parse_mode="HTML"
                        )
                    elif media.media_type == MediaType.ANIMATION:
                        p_caption = build_safe_caption(
                            title=media.title,
                            url=url,
                            platform_name=platform_title,
                            prefix="📌"
                        )
                        await status_msg.bot.send_animation(
                            chat_id=chat_id,
                            animation=FSInputFile(str(media.file_path)),
                            caption=p_caption,
                            parse_mode="HTML"
                        )
                    elif media.media_type == MediaType.ALBUM:
                        # Send media group, chunked to max 10 items per group (Telegram API limit)
                        valid_files = [f for f in media.file_paths if f.exists()]
                        chunks = [valid_files[i:i + 10] for i in range(0, len(valid_files), 10)]
                        for chunk_idx, chunk in enumerate(chunks):
                            group = []
                            for idx, fpath in enumerate(chunk):
                                chunk_caption = (
                                    build_safe_caption(
                                        title=media.title,
                                        url=url,
                                        platform_name=platform_title,
                                        prefix="📌",
                                        extra=f"({len(valid_files)} медиа)"
                                    )
                                    if (chunk_idx == 0 and idx == 0) else None
                                )
                                if fpath.suffix.lower() in [".mp4", ".mov", ".mkv"]:
                                    group.append(InputMediaVideo(media=FSInputFile(str(fpath)), caption=chunk_caption, parse_mode="HTML"))
                                else:
                                    group.append(InputMediaPhoto(media=FSInputFile(str(fpath)), caption=chunk_caption, parse_mode="HTML"))
                            await status_msg.bot.send_media_group(chat_id=chat_id, media=group)
                            if chunk_idx < len(chunks) - 1:
                                await asyncio.sleep(1.0)
                    elif audio_only or media.media_type == MediaType.AUDIO:
                        a_caption = build_safe_caption(
                            title=media.title,
                            url=url,
                            platform_name=platform_title,
                            prefix="🎵"
                        )
                        await status_msg.bot.send_audio(
                            chat_id=chat_id,
                            audio=FSInputFile(str(media.file_path)),
                            title=truncate_text(media.title, 100),
                            performer=truncate_text(media.uploader, 100),
                            duration=media.duration,
                            caption=a_caption,
                            parse_mode="HTML"
                        )
                    else:  # VIDEO
                        thumb_file = FSInputFile(str(media.thumbnail_path)) if media.thumbnail_path and media.thumbnail_path.exists() else None
                        await status_msg.bot.send_video(
                            chat_id=chat_id,
                            video=FSInputFile(str(media.file_path)),
                            duration=media.duration,
                            width=media.width,
                            height=media.height,
                            thumbnail=thumb_file,
                            caption=caption,
                            parse_mode="HTML",
                            supports_streaming=True
                        )
                except TelegramBadRequest as tb_err:
                    logger.warning(f"TelegramBadRequest sending HTML media ({tb_err}), retrying with plain text")
                    plain_caption = f"{truncate_text(media.title, 900)}\n{url}"
                    if media.media_type == MediaType.PHOTO:
                        await status_msg.bot.send_photo(chat_id=chat_id, photo=FSInputFile(str(media.file_path)), caption=plain_caption)
                    elif media.media_type == MediaType.ANIMATION:
                        await status_msg.bot.send_animation(chat_id=chat_id, animation=FSInputFile(str(media.file_path)), caption=plain_caption)
                    elif media.media_type == MediaType.ALBUM:
                        valid_files = [f for f in media.file_paths if f.exists()]
                        chunks = [valid_files[i:i + 10] for i in range(0, len(valid_files), 10)]
                        for chunk_idx, chunk in enumerate(chunks):
                            group = []
                            for idx, fpath in enumerate(chunk):
                                chunk_caption = plain_caption if (chunk_idx == 0 and idx == 0) else None
                                if fpath.suffix.lower() in [".mp4", ".mov", ".mkv"]:
                                    group.append(InputMediaVideo(media=FSInputFile(str(fpath)), caption=chunk_caption))
                                else:
                                    group.append(InputMediaPhoto(media=FSInputFile(str(fpath)), caption=chunk_caption))
                            await status_msg.bot.send_media_group(chat_id=chat_id, media=group)
                    elif audio_only or media.media_type == MediaType.AUDIO:
                        await status_msg.bot.send_audio(
                            chat_id=chat_id,
                            audio=FSInputFile(str(media.file_path)),
                            title=truncate_text(media.title, 100),
                            performer=truncate_text(media.uploader, 100),
                            duration=media.duration,
                            caption=plain_caption
                        )
                    else:
                        thumb_file = FSInputFile(str(media.thumbnail_path)) if media.thumbnail_path and media.thumbnail_path.exists() else None
                        await status_msg.bot.send_video(
                            chat_id=chat_id,
                            video=FSInputFile(str(media.file_path)),
                            duration=media.duration,
                            width=media.width,
                            height=media.height,
                            thumbnail=thumb_file,
                            caption=plain_caption,
                            supports_streaming=True
                        )

                # If there's an accompanying audio track (e.g. TikTok slideshow music)
                if media.audio_path and media.audio_path.exists():
                    audio_caption = build_safe_caption(
                        title=f"Звук из публикации: {media.title}",
                        url=url,
                        platform_name=platform_title,
                        prefix="🎵"
                    )
                    try:
                        await status_msg.bot.send_audio(
                            chat_id=chat_id,
                            audio=FSInputFile(str(media.audio_path)),
                            caption=audio_caption,
                            parse_mode="HTML"
                        )
                    except TelegramBadRequest:
                        await status_msg.bot.send_audio(
                            chat_id=chat_id,
                            audio=FSInputFile(str(media.audio_path)),
                            caption=f"🎵 Звук из публикации: {truncate_text(media.title, 400)}\n{url}"
                        )
                    except Exception as a_err:
                        logger.warning(f"Failed to send accompanying audio: {a_err}")

                # Delete status message on success
                await status_msg.delete()
            except Exception as send_err:
                logger.error(f"Error sending media to chat {chat_id}: {send_err}")
                esc_err = html.escape(str(send_err))
                await update_status_safely(status_msg, f"❌ <b>Ошибка при отправке в Telegram:</b> {esc_err}")

    except ValueError:
        await update_status_safely(
            status_msg,
            "⏳ У вас уже есть активная задача загрузки. Дождитесь её окончания."
        )
    except Exception as e:
        logger.error(f"Unexpected error in download task: {e}")
        await update_status_safely(status_msg, f"❌ Произошла непредвиденная ошибка: {e}")
    finally:
        if action_task:
            action_task.cancel()
        # Guaranteed cleanup of all downloaded files
        if media:
            if media.file_path and media.file_path.exists():
                media.file_path.unlink(missing_ok=True)
            for fp in media.file_paths:
                if fp.exists():
                    fp.unlink(missing_ok=True)
            if media.thumbnail_path and media.thumbnail_path.exists():
                media.thumbnail_path.unlink(missing_ok=True)
            if media.audio_path and media.audio_path.exists():
                media.audio_path.unlink(missing_ok=True)


async def run_playlist_task(
    chat_id: int,
    user_id: int,
    url: str,
    status_msg: types.Message,
    max_items: int,
    audio_only: bool
):
    try:
        async with queue_manager.acquire(user_id):
            await update_status_safely(status_msg, f"🔍 <i>Получаю список элементов (до {max_items})...</i>")
            items = await downloader_service.get_playlist_items(url, max_items=max_items)

            if not items:
                await update_status_safely(status_msg, "❌ Не удалось извлечь элементы из плейлиста или доски.")
                return

            total_items = len(items)
            await update_status_safely(status_msg, f"📥 <b>Начинаю загрузку {total_items} элементов...</b>")

            for index, item in enumerate(items, 1):
                item_url = item["url"]
                item_title = item["title"]

                esc_item_title = html.escape(truncate_text(item_title, 100))
                await update_status_safely(
                    status_msg,
                    f"⬇️ <b>Загрузка {index}/{total_items}:</b>\n<i>{esc_item_title}</i>"
                )

                media = await downloader_service.download_media(item_url, audio_only=audio_only)
                if media and (media.file_path or media.file_paths):
                    try:
                        caption = build_safe_caption(
                            title=media.title,
                            url=item_url,
                            platform_name=media.platform.value,
                            prefix="🎬",
                            extra=f"({index}/{total_items})"
                        )
                        if media.media_type == MediaType.PHOTO:
                            await status_msg.bot.send_photo(
                                chat_id=chat_id,
                                photo=FSInputFile(str(media.file_path)),
                                caption=caption,
                                parse_mode="HTML"
                            )
                        elif audio_only or media.media_type == MediaType.AUDIO:
                            await status_msg.bot.send_audio(
                                chat_id=chat_id,
                                audio=FSInputFile(str(media.file_path)),
                                title=truncate_text(media.title, 100),
                                performer=truncate_text(media.uploader, 100),
                                duration=media.duration,
                                caption=caption,
                                parse_mode="HTML"
                            )
                        else:
                            thumb_file = FSInputFile(str(media.thumbnail_path)) if media.thumbnail_path and media.thumbnail_path.exists() else None
                            await status_msg.bot.send_video(
                                chat_id=chat_id,
                                video=FSInputFile(str(media.file_path)),
                                duration=media.duration,
                                width=media.width,
                                height=media.height,
                                thumbnail=thumb_file,
                                caption=caption,
                                parse_mode="HTML",
                                supports_streaming=True
                            )
                    except TelegramBadRequest:
                        plain_cap = f"{truncate_text(media.title, 800)} ({index}/{total_items})\n{item_url}"
                        if media.media_type == MediaType.PHOTO:
                            await status_msg.bot.send_photo(chat_id=chat_id, photo=FSInputFile(str(media.file_path)), caption=plain_cap)
                        elif audio_only or media.media_type == MediaType.AUDIO:
                            await status_msg.bot.send_audio(chat_id=chat_id, audio=FSInputFile(str(media.file_path)), caption=plain_cap)
                        else:
                            thumb_file = FSInputFile(str(media.thumbnail_path)) if media.thumbnail_path and media.thumbnail_path.exists() else None
                            await status_msg.bot.send_video(chat_id=chat_id, video=FSInputFile(str(media.file_path)), thumbnail=thumb_file, caption=plain_cap, supports_streaming=True)
                    except Exception as e:
                        logger.error(f"Failed to send item {index}: {e}")
                    finally:
                        if media.file_path and media.file_path.exists():
                            media.file_path.unlink(missing_ok=True)
                        for fp in media.file_paths:
                            if fp.exists():
                                fp.unlink(missing_ok=True)
                        if media.thumbnail_path:
                            media.thumbnail_path.unlink(missing_ok=True)

                # Delay between items to prevent Telegram FloodWait
                await asyncio.sleep(1.5)

            await update_status_safely(status_msg, f"✅ <b>Завершено!</b> Отправлено: {total_items}")

    except ValueError:
        await update_status_safely(status_msg, "⏳ У вас уже есть активная задача загрузки.")
    except Exception as e:
        logger.error(f"Playlist task error: {e}")
        await update_status_safely(status_msg, f"❌ Ошибка при обработке плейлиста: {e}")
