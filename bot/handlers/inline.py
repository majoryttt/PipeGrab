import asyncio
import html
import logging
import time
import uuid
from dataclasses import dataclass, field

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InlineQueryResultUnion,
    InputMediaAudio,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
)

from bot.config import settings
from bot.handlers.download import build_safe_caption, truncate_text
from bot.services.cache_service import cache_service, hash_url
from bot.services.downloader import (
    MediaInfo,
    MediaType,
    Platform,
    detect_platform,
    downloader_service,
    extract_first_url,
)
from bot.services.queue_manager import queue_manager
from bot.services.stats_service import stats_service

logger = logging.getLogger(__name__)

router = Router(name="inline_router")
_background_tasks: set[asyncio.Task[None]] = set()


@dataclass
class PendingInlineTask:
    url: str
    platform: Platform
    title: str
    uploader: str
    is_audio: bool
    created_at: float = field(default_factory=time.time)


_pending_tasks: dict[str, PendingInlineTask] = {}
_PENDING_TASK_TTL = 900  # 15 minutes


def _cleanup_stale_tasks() -> None:
    now = time.time()
    stale_keys = [k for k, v in _pending_tasks.items() if now - v.created_at > _PENDING_TASK_TTL]
    for k in stale_keys:
        _pending_tasks.pop(k, None)


def store_pending_task(url: str, platform: Platform, title: str, uploader: str, is_audio: bool) -> str:
    _cleanup_stale_tasks()
    task_id = str(uuid.uuid4())[:12]
    _pending_tasks[task_id] = PendingInlineTask(
        url=url,
        platform=platform,
        title=title,
        uploader=uploader,
        is_audio=is_audio,
    )
    return task_id


def get_pending_task(task_id: str) -> PendingInlineTask | None:
    return _pending_tasks.get(task_id)


def _cleanup_media_files(media: MediaInfo | None) -> None:
    if not media:
        return
    files_to_delete = []
    if media.file_path:
        files_to_delete.append(media.file_path)
    if media.file_paths:
        files_to_delete.extend(media.file_paths)
    if media.thumbnail_path:
        files_to_delete.append(media.thumbnail_path)
    if media.audio_path:
        files_to_delete.append(media.audio_path)
    for f in files_to_delete:
        try:
            if f and f.exists():
                f.unlink(missing_ok=True)
        except OSError as e:
            logger.debug(f"Failed to unlink temp media file {f}: {e}")


@router.inline_query()
async def handle_inline_query(inline_query: InlineQuery, bot: Bot):
    """
    Handle Telegram Inline Query: @bot_username <url>.
    Returns cached media results instantly if available, or two-step article cards for new links.
    """
    query = (inline_query.query or "").strip()

    # 1. Empty query -> return guide card
    if not query:
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "PipeGrabBot"
        guide_text = (
            "💡 <b>PipeGrab — Инлайн-режим</b>\n\n"
            "Чтобы отправить видео прямо в диалог или группу, введите:\n"
            f"<code>@{bot_username} ссылка_на_видео</code>\n\n"
            "<b>Поддерживаются:</b>\n"
            "• 🎬 YouTube (видео и Shorts)\n"
            "• 🎵 TikTok (без водяных знаков)\n"
            "• 📸 Instagram (Reels и публикации)\n"
            "• 📌 Pinterest (видео и фото)\n"
            "• 🐦 Twitter / X\n"
            "• 🤖 Reddit (видео и галереи)"
        )
        help_results: list[InlineQueryResultUnion] = [
            InlineQueryResultArticle(
                id="inline_help",
                title="PipeGrab: Скачивание видео в чат",
                description=f"Введите @{bot_username} <ссылка>",
                input_message_content=InputTextMessageContent(
                    message_text=guide_text,
                    parse_mode="HTML"
                ),
            )
        ]
        await inline_query.answer(results=help_results, cache_time=settings.inline_cache_time, is_personal=True)
        return

    # 2. Extract first URL
    url = extract_first_url(query)
    if not url:
        no_url_results: list[InlineQueryResultUnion] = [
            InlineQueryResultArticle(
                id="inline_no_url",
                title="⚠️ Ссылка не найдена",
                description="Вставьте ссылку на YouTube, TikTok, Instagram, Pinterest или Reddit",
                input_message_content=InputTextMessageContent(
                    message_text="❌ <b>В запросе не найдена ссылка.</b>\nВведите: <code>@bot_username ссылка</code>",
                    parse_mode="HTML"
                ),
            )
        ]
        await inline_query.answer(results=no_url_results, cache_time=settings.inline_cache_time, is_personal=True)
        return

    platform = detect_platform(url)

    # 3. Check cache
    cached_video = await cache_service.get_cached(url, media_type="video")
    cached_audio = await cache_service.get_cached(url, media_type="audio")
    cached_photo = await cache_service.get_cached(url, media_type="photo")

    results: list[InlineQueryResultUnion] = []

    if cached_video:
        cap = cached_video.caption or build_safe_caption(
            title=cached_video.title,
            url=url,
            platform_name=cached_video.platform,
            prefix="🎬"
        )
        results.append(
            InlineQueryResultCachedVideo(
                id=f"cv_{cached_video.url_hash[:10]}",
                video_file_id=cached_video.file_id,
                title=f"🎬 {truncate_text(cached_video.title, 60)}",
                description=f"Готово к отправке ({cached_video.platform})",
                caption=cap,
                parse_mode="HTML",
            )
        )

    if cached_audio:
        a_cap = cached_audio.caption or build_safe_caption(
            title=cached_audio.title,
            url=url,
            platform_name=cached_audio.platform,
            prefix="🎵"
        )
        results.append(
            InlineQueryResultCachedAudio(
                id=f"ca_{cached_audio.url_hash[:10]}",
                audio_file_id=cached_audio.file_id,
                caption=a_cap,
                parse_mode="HTML",
            )
        )

    if cached_photo and not cached_video:
        p_cap = cached_photo.caption or build_safe_caption(
            title=cached_photo.title,
            url=url,
            platform_name=cached_photo.platform,
            prefix="📸"
        )
        results.append(
            InlineQueryResultCachedPhoto(
                id=f"cp_{cached_photo.url_hash[:10]}",
                photo_file_id=cached_photo.file_id,
                title=f"📸 {truncate_text(cached_photo.title, 60)}",
                description=f"Фото ({cached_photo.platform})",
                caption=p_cap,
                parse_mode="HTML",
            )
        )

    if results:
        await inline_query.answer(results=results, cache_time=settings.inline_cache_time, is_personal=True)
        return

    # 4. Link not in cache -> quick metadata probe (up to 2.5s)
    info: MediaInfo | None = None
    try:
        info = await asyncio.wait_for(downloader_service.get_info(url), timeout=2.5)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Quick info probe failed or timed out for {url}: {e}")

    title = info.title if (info and info.title) else f"Медиа ({platform.value})"
    uploader = info.uploader if (info and info.uploader) else ""

    # Check whether Dump Channel is configured
    if settings.dump_channel_id:
        task_id = store_pending_task(
            url=url,
            platform=platform,
            title=title,
            uploader=uploader,
            is_audio=False
        )
        esc_title = html.escape(truncate_text(title, 200))
        esc_url = html.escape(url, quote=True)
        esc_plat = html.escape(platform.value)

        video_placeholder = (
            "⏳ <b>Подготовка видео...</b>\n\n"
            f"🎬 <b>{esc_title}</b>\n"
            f"🔗 <a href='{esc_url}'>{esc_plat}</a>\n\n"
            "<i>Бот скачивает медиа и заменит это сообщение...</i>"
        )
        video_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏳ Загрузка видео...", callback_data="inline_status")]
            ]
        )
        results.append(
            InlineQueryResultArticle(
                id=f"dl_v_{task_id}",
                title=f"🎬 Отправить видео: {truncate_text(title, 50)}",
                description=f"Нажмите для отправки видео в чат ({platform.value})",
                input_message_content=InputTextMessageContent(
                    message_text=video_placeholder,
                    parse_mode="HTML"
                ),
                reply_markup=video_kb,
            )
        )

        if platform == Platform.YOUTUBE:
            task_a_id = store_pending_task(
                url=url,
                platform=platform,
                title=title,
                uploader=uploader,
                is_audio=True
            )
            audio_placeholder = (
                "⏳ <b>Подготовка аудио...</b>\n\n"
                f"🎵 <b>{esc_title}</b>\n"
                f"🔗 <a href='{esc_url}'>{esc_plat}</a>\n\n"
                "<i>Бот извлекает MP3 и заменит это сообщение...</i>"
            )
            audio_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⏳ Загрузка MP3...", callback_data="inline_status")]
                ]
            )
            results.append(
                InlineQueryResultArticle(
                    id=f"dl_a_{task_a_id}",
                    title=f"🎵 Отправить MP3: {truncate_text(title, 50)}",
                    description=f"Нажмите для отправки аудио ({platform.value})",
                    input_message_content=InputTextMessageContent(
                        message_text=audio_placeholder,
                        parse_mode="HTML"
                    ),
                    reply_markup=audio_kb,
                )
            )
    else:
        # Cache-only mode without dump channel
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "PipeGrabBot"
        deep_link = f"https://t.me/{bot_username}"

        esc_title = html.escape(truncate_text(title, 200))
        esc_url = html.escape(url, quote=True)

        card_text = (
            f"🎬 <b>{esc_title}</b>\n\n"
            f"🔗 {esc_url}\n\n"
            "ℹ️ <i>Это видео еще не было загружено в кэш бота.</i>\n"
            "Чтобы скачать его, перейдите в диалог с ботом:"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚡ Скачать в боте", url=deep_link)]
            ]
        )
        results.append(
            InlineQueryResultArticle(
                id=f"open_{hash_url(url)[:10]}",
                title=f"📥 Скачать через @{bot_username}: {truncate_text(title, 40)}",
                description="Ссылка еще не в кэше. Нажмите для перехода в бот.",
                input_message_content=InputTextMessageContent(
                    message_text=card_text,
                    parse_mode="HTML"
                ),
                reply_markup=kb,
            )
        )

    await inline_query.answer(results=results, cache_time=30, is_personal=True)


@router.callback_query(F.data == "inline_status")
async def handle_inline_status_callback(callback: types.CallbackQuery):
    """Notify user that media is currently downloading when placeholder button is clicked."""
    await callback.answer(
        "⏳ Бот скачивает медиа и заменит это сообщение, как только оно будет готово.",
        show_alert=False
    )


@router.chosen_inline_result()
async def handle_chosen_inline_result(chosen: types.ChosenInlineResult, bot: Bot):
    """
    Handle user selection of an inline result card.
    If DUMP_CHANNEL_ID is set, downloads the media in the background and replaces
    the placeholder message via edit_message_media(inline_message_id=...).
    """
    if not chosen.inline_message_id:
        return

    result_id = chosen.result_id or ""
    if not result_id.startswith(("dl_v_", "dl_a_")):
        return

    is_audio = result_id.startswith("dl_a_")
    task_id = result_id[5:]
    pending = get_pending_task(task_id)

    url = pending.url if pending else extract_first_url(chosen.query or "")
    if not url:
        logger.warning(f"Could not determine URL for chosen inline result {result_id}")
        return

    task = asyncio.create_task(
        process_inline_download(
            bot=bot,
            inline_message_id=chosen.inline_message_id,
            url=url,
            audio_only=is_audio,
            user_id=chosen.from_user.id
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def process_inline_download(
    bot: Bot,
    inline_message_id: str,
    url: str,
    audio_only: bool,
    user_id: int
) -> None:
    """
    Download media, upload to dump channel for file_id, save in cache,
    and update recipient chat message via edit_message_media.
    """
    dump_channel_id = settings.dump_channel_id
    if not dump_channel_id:
        return

    # 1. Fast check cache again in case another concurrent task resolved it
    cached = None
    if audio_only:
        cached = await cache_service.get_cached(url, media_type="audio")
    else:
        cached = await cache_service.get_cached(url, media_type="video")
        if not cached:
            cached = await cache_service.get_cached(url, media_type="photo")

    if cached:
        try:
            media_input: InputMediaAudio | InputMediaVideo | InputMediaPhoto
            if cached.media_type == "audio":
                media_input = InputMediaAudio(media=cached.file_id, caption=cached.caption, parse_mode="HTML")
            elif cached.media_type == "photo":
                media_input = InputMediaPhoto(
                    media=cached.file_id,
                    caption=cached.caption,
                    parse_mode="HTML",
                    has_spoiler=getattr(cached, "has_spoiler", False),
                )
            else:
                media_input = InputMediaVideo(
                    media=cached.file_id,
                    caption=cached.caption,
                    parse_mode="HTML",
                    supports_streaming=True,
                    has_spoiler=getattr(cached, "has_spoiler", False),
                )
            await bot.edit_message_media(inline_message_id=inline_message_id, media=media_input)
            return
        except TelegramAPIError as e:
            logger.warning(f"Failed to deliver cached media in inline update: {e}")

    # 2. Acquire concurrency slot
    if not await queue_manager.can_user_download(user_id):
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text="⏳ <b>У вас уже выполняется загрузка.</b> Пожалуйста, дождитесь её завершения.",
                parse_mode="HTML"
            )
        except TelegramAPIError as e:
            logger.debug(f"Failed to edit message for active user notice: {e}")
        return

    media: MediaInfo | None = None
    try:
        async with queue_manager.acquire(user_id):
            media = await downloader_service.download_media(url=url, audio_only=audio_only)

            has_files = media and (
                (media.file_path and media.file_path.exists()) or
                (media.file_paths and any(f.exists() for f in media.file_paths))
            )

            if not media or not has_files:
                if media and media.error_message == "NO_MEDIA_REDDIT":
                    err_msg_text = "ℹ️ <b>В посте Reddit нет медиафайла</b> (текстовая запись или внешняя ссылка)."
                elif media and media.error_message == "AUTH_REQUIRED":
                    err_msg_text = "❌ <b>Требуется авторизация для доступа к этому медиа.</b>"
                else:
                    raw_err = media.error_message if (media and media.error_message) else "Не удалось скачать файл"
                    err_msg_text = f"❌ <b>Ошибка при загрузке:</b> {html.escape(raw_err)}"
                try:
                    await bot.edit_message_text(
                        inline_message_id=inline_message_id,
                        text=err_msg_text,
                        parse_mode="HTML"
                    )
                except TelegramAPIError as e:
                    logger.debug(f"Failed to edit message for download error: {e}")
                return

            # Check file size limit
            if media.file_size > settings.max_file_size_bytes:
                size_mb = media.file_size / (1024 * 1024)
                try:
                    await bot.edit_message_text(
                        inline_message_id=inline_message_id,
                        text=(
                            f"⚠️ <b>Файл слишком большой ({size_mb:.1f} МБ).</b>\n"
                            f"Максимальный размер: {settings.max_file_size_mb} МБ."
                        ),
                        parse_mode="HTML"
                    )
                except TelegramAPIError as e:
                    logger.debug(f"Failed to edit message for size limit error: {e}")
                return

            platform_title = media.platform.value if hasattr(media.platform, "value") else str(media.platform)
            caption = build_safe_caption(
                title=media.title,
                url=url,
                platform_name=platform_title,
                prefix="🎵" if (audio_only or media.media_type == MediaType.AUDIO) else "🎬"
            )

            file_id: str | None = None
            file_unique_id: str | None = None
            edit_media: InputMediaAudio | InputMediaVideo | InputMediaPhoto | None = None

            # Upload to Dump Channel
            if audio_only or media.media_type == MediaType.AUDIO:
                dump_msg = await bot.send_audio(
                    chat_id=dump_channel_id,
                    audio=FSInputFile(str(media.file_path)),
                    title=truncate_text(media.title, 100),
                    performer=truncate_text(media.uploader, 100),
                    duration=media.duration,
                    caption=caption,
                    parse_mode="HTML"
                )
                if dump_msg.audio:
                    file_id = dump_msg.audio.file_id
                    file_unique_id = dump_msg.audio.file_unique_id
                    edit_media = InputMediaAudio(media=file_id, caption=caption, parse_mode="HTML")
            elif media.media_type == MediaType.PHOTO:
                dump_msg = await bot.send_photo(
                    chat_id=dump_channel_id,
                    photo=FSInputFile(str(media.file_path)),
                    caption=caption,
                    parse_mode="HTML",
                    has_spoiler=media.has_spoiler,
                )
                if dump_msg.photo:
                    file_id = dump_msg.photo[-1].file_id
                    file_unique_id = dump_msg.photo[-1].file_unique_id
                    edit_media = InputMediaPhoto(
                        media=file_id,
                        caption=caption,
                        parse_mode="HTML",
                        has_spoiler=media.has_spoiler,
                    )
            elif media.media_type == MediaType.ALBUM:
                first_file = next((f for f in media.file_paths if f.exists()), media.file_path)
                if not first_file or not first_file.exists():
                    raise ValueError("No files found in album")
                if first_file.suffix.lower() in [".mp4", ".mov", ".mkv", ".webm"]:
                    dump_msg = await bot.send_video(
                        chat_id=dump_channel_id,
                        video=FSInputFile(str(first_file)),
                        duration=media.duration,
                        width=media.width,
                        height=media.height,
                        caption=caption,
                        parse_mode="HTML",
                        supports_streaming=True,
                        has_spoiler=media.has_spoiler,
                    )
                    if dump_msg.video:
                        file_id = dump_msg.video.file_id
                        file_unique_id = dump_msg.video.file_unique_id
                        edit_media = InputMediaVideo(
                            media=file_id,
                            caption=caption,
                            parse_mode="HTML",
                            supports_streaming=True,
                            has_spoiler=media.has_spoiler,
                        )
                else:
                    dump_msg = await bot.send_photo(
                        chat_id=dump_channel_id,
                        photo=FSInputFile(str(first_file)),
                        caption=caption,
                        parse_mode="HTML",
                        has_spoiler=media.has_spoiler,
                    )
                    if dump_msg.photo:
                        file_id = dump_msg.photo[-1].file_id
                        file_unique_id = dump_msg.photo[-1].file_unique_id
                        edit_media = InputMediaPhoto(
                            media=file_id,
                            caption=caption,
                            parse_mode="HTML",
                            has_spoiler=media.has_spoiler,
                        )
            else:  # VIDEO
                thumb_file = FSInputFile(str(media.thumbnail_path)) if media.thumbnail_path and media.thumbnail_path.exists() else None
                dump_msg = await bot.send_video(
                    chat_id=dump_channel_id,
                    video=FSInputFile(str(media.file_path)),
                    duration=media.duration,
                    width=media.width,
                    height=media.height,
                    thumbnail=thumb_file,
                    caption=caption,
                    parse_mode="HTML",
                    supports_streaming=True,
                    has_spoiler=media.has_spoiler,
                )
                if dump_msg.video:
                    file_id = dump_msg.video.file_id
                    file_unique_id = dump_msg.video.file_unique_id
                    edit_media = InputMediaVideo(
                        media=file_id,
                        caption=caption,
                        parse_mode="HTML",
                        supports_streaming=True,
                        has_spoiler=media.has_spoiler,
                    )

            if not file_id or not edit_media:
                raise ValueError("Failed to obtain file_id from dump channel message")

            # Determine canonical media_type value for cache
            if audio_only or media.media_type == MediaType.AUDIO:
                m_type_val = "audio"
            elif isinstance(edit_media, InputMediaVideo):
                m_type_val = "video"
            else:
                m_type_val = "photo"

            # Save in persistent cache
            await cache_service.save_cached(
                url=url,
                file_id=file_id,
                platform=platform_title,
                media_type=m_type_val,
                title=media.title,
                file_unique_id=file_unique_id,
                file_size=media.file_size,
                duration=media.duration,
                width=media.width,
                height=media.height,
                caption=caption,
                has_spoiler=media.has_spoiler,
            )
            # Dual cache canonical URL if different from query URL
            if media.url and media.url != url:
                await cache_service.save_cached(
                    url=media.url,
                    file_id=file_id,
                    platform=platform_title,
                    media_type=m_type_val,
                    title=media.title,
                    file_unique_id=file_unique_id,
                    file_size=media.file_size,
                    duration=media.duration,
                    width=media.width,
                    height=media.height,
                    caption=caption,
                    has_spoiler=media.has_spoiler,
                )

            # Update the inline message in the recipient chat
            try:
                await bot.edit_message_media(
                    inline_message_id=inline_message_id,
                    media=edit_media
                )
            except TelegramBadRequest as t_err:
                # If HTML parse error occurred, retry without markup
                if "can't parse entities" in str(t_err).lower():
                    plain_caption = f"{truncate_text(media.title, 800)}\n{url}"
                    if isinstance(edit_media, InputMediaVideo):
                        edit_media = InputMediaVideo(media=file_id, caption=plain_caption, supports_streaming=True)
                    elif isinstance(edit_media, InputMediaAudio):
                        edit_media = InputMediaAudio(media=file_id, caption=plain_caption)
                    elif isinstance(edit_media, InputMediaPhoto):
                        edit_media = InputMediaPhoto(media=file_id, caption=plain_caption)
                    await bot.edit_message_media(
                        inline_message_id=inline_message_id,
                        media=edit_media
                    )
                else:
                    raise

            # Record stats
            await stats_service.record_download(
                user_id=user_id,
                platform=platform_title,
                media_type=m_type_val,
                file_size=media.file_size,
                status="success"
            )

    except Exception as e:  # noqa: BLE001
        logger.error(f"Error in process_inline_download for {url}: {e}")
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text="❌ <b>Не удалось отправить видео в чат.</b>",
                parse_mode="HTML"
            )
        except TelegramAPIError as edit_e:
            logger.debug(f"Failed to edit error notice: {edit_e}")
    finally:
        _cleanup_media_files(media)
