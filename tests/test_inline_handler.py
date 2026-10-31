import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import (
    CallbackQuery,
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    Message,
    User,
    Video,
)

from bot.config import settings
from bot.handlers.inline import (
    handle_chosen_inline_result,
    handle_inline_query,
    handle_inline_status_callback,
    process_inline_download,
)
from bot.services.cache_service import cache_service
from bot.services.downloader import MediaInfo, MediaType, Platform


@pytest.fixture(autouse=True)
def isolate_cache_db(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test_inline_cache.db"
    monkeypatch.setattr(cache_service, "db_path", test_db)
    cache_service._init_db()


@pytest.fixture
def mock_bot():
    bot = AsyncMock(spec=Bot)
    bot_user = MagicMock(spec=User)
    bot_user.username = "PipeGrabBot"
    bot_user.id = 999999
    bot.get_me.return_value = bot_user
    return bot


@pytest.fixture
def test_user():
    user = MagicMock(spec=User)
    user.id = 12345
    user.username = "test_user"
    user.is_bot = False
    return user


@pytest.mark.asyncio
async def test_inline_query_empty(mock_bot, test_user):
    query = MagicMock(spec=InlineQuery)
    query.query = ""
    query.from_user = test_user
    query.answer = AsyncMock()

    await handle_inline_query(query, mock_bot)

    query.answer.assert_called_once()
    results = query.answer.call_args.kwargs["results"]
    assert len(results) == 1
    assert isinstance(results[0], InlineQueryResultArticle)
    assert results[0].id == "inline_help"


@pytest.mark.asyncio
async def test_inline_query_no_url(mock_bot, test_user):
    query = MagicMock(spec=InlineQuery)
    query.query = "какой-то текст без ссылки"
    query.from_user = test_user
    query.answer = AsyncMock()

    await handle_inline_query(query, mock_bot)

    query.answer.assert_called_once()
    results = query.answer.call_args.kwargs["results"]
    assert len(results) == 1
    assert isinstance(results[0], InlineQueryResultArticle)
    assert results[0].id == "inline_no_url"


@pytest.mark.asyncio
async def test_inline_query_cached_video(mock_bot, test_user, tmp_path: Path):
    test_url = "https://www.tiktok.com/@creator/video/9876543210"
    file_id = "BAACAgIAAxkBAAICachedFileId"

    await cache_service.save_cached(
        url=test_url,
        file_id=file_id,
        platform="TikTok",
        media_type="video",
        title="Epic TikTok Video",
        duration=25,
        width=720,
        height=1280,
    )

    query = MagicMock(spec=InlineQuery)
    query.query = f"смотри {test_url}"
    query.from_user = test_user
    query.answer = AsyncMock()

    await handle_inline_query(query, mock_bot)

    query.answer.assert_called_once()
    results = query.answer.call_args.kwargs["results"]
    assert len(results) >= 1
    cached_res = next((r for r in results if isinstance(r, InlineQueryResultCachedVideo)), None)
    assert cached_res is not None
    assert cached_res.video_file_id == file_id
    assert "Epic TikTok Video" in cached_res.title


@pytest.mark.asyncio
async def test_inline_query_uncached_with_dump_channel(mock_bot, test_user, monkeypatch):
    monkeypatch.setattr(settings, "dump_channel_id", -1001999888777)
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    fake_info = MediaInfo(
        title="Rick Astley - Never Gonna Give You Up",
        duration=212,
        uploader="RickAstleyVEVO",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.YOUTUBE,
        url=test_url,
    )

    with patch("bot.services.downloader.downloader_service.get_info", AsyncMock(return_value=fake_info)):
        query = MagicMock(spec=InlineQuery)
        query.query = test_url
        query.from_user = test_user
        query.answer = AsyncMock()

        await handle_inline_query(query, mock_bot)

        query.answer.assert_called_once()
        results = query.answer.call_args.kwargs["results"]
        # For YouTube with dump channel, both video and audio cards should be presented
        assert len(results) == 2
        assert any(r.id.startswith("dl_v_") for r in results)
        assert any(r.id.startswith("dl_a_") for r in results)
        assert all(r.reply_markup is not None for r in results)


@pytest.mark.asyncio
async def test_inline_query_uncached_without_dump_channel(mock_bot, test_user, monkeypatch):
    monkeypatch.setattr(settings, "dump_channel_id", None)
    test_url = "https://instagram.com/reel/C99999999"

    fake_info = MediaInfo(
        title="Insta Reel Video",
        duration=15,
        uploader="creator",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.INSTAGRAM,
        url=test_url,
    )

    with patch("bot.services.downloader.downloader_service.get_info", AsyncMock(return_value=fake_info)):
        query = MagicMock(spec=InlineQuery)
        query.query = test_url
        query.from_user = test_user
        query.answer = AsyncMock()

        await handle_inline_query(query, mock_bot)

        query.answer.assert_called_once()
        results = query.answer.call_args.kwargs["results"]
        assert len(results) == 1
        assert results[0].id.startswith("open_")
        assert "Скачать через @" in results[0].title


@pytest.mark.asyncio
async def test_chosen_inline_result_cached(mock_bot, test_user, monkeypatch):
    monkeypatch.setattr(settings, "dump_channel_id", -1001999888777)
    test_url = "https://www.tiktok.com/@cat/video/111222333"
    file_id = "CAACAgIAAxkBAAIAlreadyCached"

    await cache_service.save_cached(
        url=test_url,
        file_id=file_id,
        platform="TikTok",
        media_type="video",
        title="Cached Cat",
    )

    chosen = MagicMock(spec=ChosenInlineResult)
    chosen.inline_message_id = "inline_msg_id_999"
    chosen.result_id = "dl_v_sometaskid"
    chosen.query = test_url
    chosen.from_user = test_user

    await process_inline_download(
        bot=mock_bot,
        inline_message_id="inline_msg_id_999",
        url=test_url,
        audio_only=False,
        user_id=test_user.id
    )

    mock_bot.edit_message_media.assert_called_once()
    media_call = mock_bot.edit_message_media.call_args.kwargs["media"]
    assert media_call.media == file_id


@pytest.mark.asyncio
async def test_process_inline_download_flow(mock_bot, test_user, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "dump_channel_id", -1001999888777)
    test_url = "https://www.tiktok.com/@nature/video/555666777"

    fake_video_file = tmp_path / "video.mp4"
    fake_video_file.write_bytes(b"dummy video content")

    fake_media = MediaInfo(
        title="Beautiful Nature",
        duration=30,
        uploader="NatureChannel",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.TIKTOK,
        url=test_url,
        file_path=fake_video_file,
        file_size=fake_video_file.stat().st_size,
        media_type=MediaType.VIDEO,
    )

    dump_msg = MagicMock(spec=Message)
    dump_video = MagicMock(spec=Video)
    dump_video.file_id = "FILE_ID_NEW_DUMP"
    dump_video.file_unique_id = "UNIQUE_ID_NEW"
    dump_msg.video = dump_video
    mock_bot.send_video.return_value = dump_msg

    with patch("bot.services.downloader.downloader_service.download_media", AsyncMock(return_value=fake_media)):
        await process_inline_download(
            bot=mock_bot,
            inline_message_id="inline_msg_target_777",
            url=test_url,
            audio_only=False,
            user_id=test_user.id
        )

        mock_bot.send_video.assert_called_once()
        assert mock_bot.send_video.call_args.kwargs["chat_id"] == -1001999888777

        # Check edit_message_media was called with the uploaded file_id
        mock_bot.edit_message_media.assert_called_once()
        call_kwargs = mock_bot.edit_message_media.call_args.kwargs
        assert call_kwargs["inline_message_id"] == "inline_msg_target_777"
        assert call_kwargs["media"].media == "FILE_ID_NEW_DUMP"

        # Check cached entry in DB
        cached = await cache_service.get_cached(test_url, media_type="video")
        assert cached is not None
        assert cached.file_id == "FILE_ID_NEW_DUMP"
        assert cached.title == "Beautiful Nature"


@pytest.mark.asyncio
async def test_handle_chosen_inline_result_dispatch(mock_bot, test_user):
    chosen = MagicMock(spec=ChosenInlineResult)
    chosen.inline_message_id = "msg_123"
    chosen.result_id = "dl_v_validtask"
    chosen.query = "https://www.tiktok.com/@user/video/111"
    chosen.from_user = test_user

    with patch("bot.handlers.inline.process_inline_download", AsyncMock()) as mock_proc:
        await handle_chosen_inline_result(chosen, mock_bot)
        # Give event loop a microtick for create_task
        await asyncio.sleep(0.01)
        mock_proc.assert_called_once()


@pytest.mark.asyncio
async def test_inline_status_callback():
    cb = MagicMock(spec=CallbackQuery)
    cb.answer = AsyncMock()
    await handle_inline_status_callback(cb)
    cb.answer.assert_called_once()
    assert "скачивает медиа" in cb.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_process_inline_download_album_with_video(mock_bot, test_user, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "dump_channel_id", -1001999888777)
    test_url = "https://www.instagram.com/p/C_carousel123"

    v_file = tmp_path / "item1.mp4"
    v_file.write_bytes(b"carousel video item")

    fake_media = MediaInfo(
        title="Carousel with video",
        duration=10,
        uploader="insta_user",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.INSTAGRAM,
        url=test_url,
        file_paths=[v_file],
        file_size=v_file.stat().st_size,
        media_type=MediaType.ALBUM,
    )

    dump_msg = MagicMock(spec=Message)
    dump_video = MagicMock(spec=Video)
    dump_video.file_id = "ALBUM_VIDEO_FILE_ID"
    dump_video.file_unique_id = "ALBUM_VID_UID"
    dump_msg.video = dump_video
    mock_bot.send_video.return_value = dump_msg

    with patch("bot.services.downloader.downloader_service.download_media", AsyncMock(return_value=fake_media)):
        await process_inline_download(
            bot=mock_bot,
            inline_message_id="inline_msg_album_1",
            url=test_url,
            audio_only=False,
            user_id=test_user.id
        )

        mock_bot.send_video.assert_called_once()
        mock_bot.edit_message_media.assert_called_once()
        cached = await cache_service.get_cached(test_url, media_type="video")
        assert cached is not None
        assert cached.file_id == "ALBUM_VIDEO_FILE_ID"


@pytest.mark.asyncio
async def test_process_inline_download_fast_cache_photo(mock_bot, test_user, monkeypatch):
    monkeypatch.setattr(settings, "dump_channel_id", -1001999888777)
    test_url = "https://www.pinterest.com/pin/123456789"
    photo_file_id = "AgACAgIAAxkBAAIPhotoId"

    await cache_service.save_cached(
        url=test_url,
        file_id=photo_file_id,
        platform="Pinterest",
        media_type="photo",
        title="Pins photo",
    )

    await process_inline_download(
        bot=mock_bot,
        inline_message_id="inline_photo_target",
        url=test_url,
        audio_only=False,
        user_id=test_user.id
    )

    mock_bot.edit_message_media.assert_called_once()
    call_kwargs = mock_bot.edit_message_media.call_args.kwargs
    assert call_kwargs["media"].media == photo_file_id

