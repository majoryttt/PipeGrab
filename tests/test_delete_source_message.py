import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from bot.config import settings
from bot.handlers.download import cleanup_messages_in_background, run_download_task
from bot.keyboards.inline import (
    register_url,
    get_cached_url,
    get_cached_message_id,
    get_download_format_kb,
    get_playlist_kb,
)
from bot.services.downloader import MediaInfo, MediaType, Platform


def test_keyboards_url_and_message_id_caching():
    url = "https://youtube.com/watch?v=test12345"
    orig_msg_id = 9999

    cid = register_url(url, original_message_id=orig_msg_id)
    assert get_cached_url(cid) == url
    assert get_cached_message_id(cid) == orig_msg_id

    # Test format kb generates valid cid
    kb = get_download_format_kb(url, original_message_id=8888)
    # Extract cid from callback_data: dl:video:{cid}
    cb_data = kb.inline_keyboard[0][0].callback_data
    assert cb_data.startswith("dl:video:")
    extracted_cid = cb_data.split(":")[2]
    assert get_cached_url(extracted_cid) == url
    assert get_cached_message_id(extracted_cid) == 8888

    # Test playlist kb
    pl_kb = get_playlist_kb(url, total_count=10, original_message_id=7777)
    pl_cb_data = pl_kb.inline_keyboard[0][0].callback_data
    extracted_pl_cid = pl_cb_data.split(":")[2]
    assert get_cached_url(extracted_pl_cid) == url
    assert get_cached_message_id(extracted_pl_cid) == 7777


@pytest.mark.asyncio
async def test_cleanup_messages_in_background_bulk():
    bot = AsyncMock(spec=Bot)
    bot.delete_messages = AsyncMock()

    cleanup_messages_in_background(bot, chat_id=12345, message_ids=[101, 100])
    await asyncio.sleep(0.05)  # Let background task run

    bot.delete_messages.assert_called_once_with(chat_id=12345, message_ids=[101, 100])


@pytest.mark.asyncio
async def test_cleanup_messages_in_background_fallback():
    bot = MagicMock(spec=Bot)
    # Simulate delete_messages failing (e.g. older Bot API or TelegramBadRequest)
    bot.delete_messages = AsyncMock(side_effect=Exception("Bulk delete failed"))
    bot.delete_message = AsyncMock()

    cleanup_messages_in_background(bot, chat_id=12345, message_ids=[101, 100])
    await asyncio.sleep(0.05)

    assert bot.delete_message.call_count == 2
    bot.delete_message.assert_any_call(chat_id=12345, message_id=101)
    bot.delete_message.assert_any_call(chat_id=12345, message_id=100)


@pytest.mark.asyncio
async def test_run_download_task_deletes_both_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "delete_source_message", True)

    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"video content")

    fake_media = MediaInfo(
        title="Test Video",
        duration=10,
        uploader="creator",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.TIKTOK,
        url="https://tiktok.com/@user/video/123",
        media_type=MediaType.VIDEO,
        file_path=fake_video,
        file_size=len(b"video content")
    )

    status_msg = AsyncMock()
    status_msg.message_id = 200
    status_msg.bot = AsyncMock(spec=Bot)
    status_msg.bot.delete_messages = AsyncMock()

    with patch("bot.handlers.download.downloader_service.download_media", new_callable=AsyncMock) as mock_dl:
        mock_dl.return_value = fake_media

        await run_download_task(
            chat_id=111,
            user_id=222,
            url="https://tiktok.com/@user/video/123",
            status_msg=status_msg,
            audio_only=False,
            original_message_id=199
        )

        await asyncio.sleep(0.05)  # Wait for fire-and-forget deletion

        # Check that video was sent
        status_msg.bot.send_video.assert_called_once()

        # Check that both status_msg (200) and user message (199) were requested for deletion
        status_msg.bot.delete_messages.assert_called_once_with(chat_id=111, message_ids=[200, 199])


@pytest.mark.asyncio
async def test_run_download_task_preserves_source_when_setting_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "delete_source_message", False)

    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"video content")

    fake_media = MediaInfo(
        title="Test Video",
        duration=10,
        uploader="creator",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.TIKTOK,
        url="https://tiktok.com/@user/video/123",
        media_type=MediaType.VIDEO,
        file_path=fake_video,
        file_size=len(b"video content")
    )

    status_msg = AsyncMock()
    status_msg.message_id = 200
    status_msg.bot = AsyncMock(spec=Bot)
    status_msg.bot.delete_messages = AsyncMock()
    status_msg.bot.delete_message = AsyncMock()

    with patch("bot.handlers.download.downloader_service.download_media", new_callable=AsyncMock) as mock_dl:
        mock_dl.return_value = fake_media

        await run_download_task(
            chat_id=111,
            user_id=222,
            url="https://tiktok.com/@user/video/123",
            status_msg=status_msg,
            audio_only=False,
            original_message_id=199
        )

        await asyncio.sleep(0.05)

        # Only status message should be deleted, NOT original_message_id
        if status_msg.bot.delete_messages.called:
            assert status_msg.bot.delete_messages.call_args[1]["message_ids"] == [200]
        else:
            status_msg.bot.delete_message.assert_called_once_with(chat_id=111, message_id=200)


@pytest.mark.asyncio
async def test_run_download_task_preserves_source_on_error(monkeypatch):
    monkeypatch.setattr(settings, "delete_source_message", True)

    status_msg = AsyncMock()
    status_msg.message_id = 200
    status_msg.bot = AsyncMock(spec=Bot)
    status_msg.bot.delete_messages = AsyncMock()
    status_msg.bot.delete_message = AsyncMock()

    # Downloader returns None (failed download)
    with patch("bot.handlers.download.downloader_service.download_media", new_callable=AsyncMock) as mock_dl:
        mock_dl.return_value = None

        await run_download_task(
            chat_id=111,
            user_id=222,
            url="https://invalid-link.com",
            status_msg=status_msg,
            audio_only=False,
            original_message_id=199
        )

        await asyncio.sleep(0.05)

        # Neither delete_messages nor delete_message should be called (status message shows error instead)
        assert not status_msg.bot.delete_messages.called
        assert not status_msg.bot.delete_message.called
