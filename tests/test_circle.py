import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram import types
from aiogram.types import FSInputFile

from bot.config import settings
from bot.handlers.download import handle_circle_command
from bot.services.ffmpeg_utils import convert_to_video_note
from bot.services.downloader import MediaInfo, MediaType, Platform


@pytest.mark.asyncio
async def test_convert_to_video_note_nonexistent_file(tmp_path):
    res = await convert_to_video_note(tmp_path / "nonexistent.mp4", tmp_path / "out.mp4")
    assert res is None


@pytest.mark.asyncio
async def test_convert_to_video_note_success(tmp_path):
    in_file = tmp_path / "input.mp4"
    in_file.write_bytes(b"dummy_video_data")
    out_file = tmp_path / "output.mp4"

    async def mock_subprocess(*cmd, **kwargs):
        # Emulate ffmpeg creating the output file
        out_file.write_bytes(b"converted_video_note_data")
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess) as mock_exec:
        res = await convert_to_video_note(in_file, out_file, max_duration=60, size=640)
        assert res == out_file
        assert res.exists()
        assert res.read_bytes() == b"converted_video_note_data"

        # Check ffmpeg command arguments
        called_cmd = mock_exec.call_args[0]
        assert "ffmpeg" in called_cmd
        assert "-t" in called_cmd
        assert "60" in called_cmd
        assert "crop='min(iw,ih)':'min(iw,ih)',scale=640:640" in called_cmd
        assert "libx264" in called_cmd
        assert "aac" in called_cmd


@pytest.mark.asyncio
async def test_convert_to_video_note_failure(tmp_path):
    in_file = tmp_path / "input.mp4"
    in_file.write_bytes(b"dummy_video_data")
    out_file = tmp_path / "output.mp4"

    async def mock_subprocess(*cmd, **kwargs):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"ffmpeg error"))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess):
        res = await convert_to_video_note(in_file, out_file)
        assert res is None


@pytest.mark.asyncio
async def test_handle_circle_command_no_media_shows_help():
    message = MagicMock(spec=types.Message)
    message.text = "/circle"
    message.video = None
    message.animation = None
    message.video_note = None
    message.document = None
    message.reply_to_message = None
    message.from_user = MagicMock(id=12345)
    message.reply = AsyncMock()

    await handle_circle_command(message)

    message.reply.assert_called_once()
    help_text = message.reply.call_args[0][0]
    assert "Как создать кружок" in help_text


@pytest.mark.asyncio
async def test_handle_circle_command_with_url(tmp_path):
    message = MagicMock(spec=types.Message)
    message.text = "/circle https://tiktok.com/@user/video/123"
    message.video = None
    message.animation = None
    message.video_note = None
    message.document = None
    message.reply_to_message = None
    message.from_user = MagicMock(id=12345)
    message.chat = MagicMock(id=67890)
    message.message_id = 42

    status_msg = AsyncMock()
    status_msg.edit_text = AsyncMock()
    status_msg.delete = AsyncMock()
    message.reply = AsyncMock(return_value=status_msg)

    bot = AsyncMock()
    bot.send_video_note = AsyncMock()
    message.bot = bot

    downloaded_video = tmp_path / "dl.mp4"
    downloaded_video.write_bytes(b"downloaded_video_content")

    media_info = MediaInfo(
        title="TikTok Video",
        duration=15,
        uploader="user",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.TIKTOK,
        url="https://tiktok.com/@user/video/123",
        media_type=MediaType.VIDEO,
        file_path=downloaded_video,
    )

    async def mock_convert(src, dst, max_duration=60, size=640):
        dst.write_bytes(b"circle_result")
        return dst

    with patch("bot.handlers.download.downloader_service.download_media", AsyncMock(return_value=media_info)), \
         patch("bot.handlers.download.convert_to_video_note", side_effect=mock_convert):
        await handle_circle_command(message)

    bot.send_video_note.assert_called_once()
    assert bot.send_video_note.call_args[1]["chat_id"] == 67890
    assert bot.send_video_note.call_args[1]["reply_to_message_id"] == 42


@pytest.mark.asyncio
async def test_handle_circle_command_busy_user():
    message = MagicMock(spec=types.Message)
    message.text = "/circle https://tiktok.com/@user/video/123"
    message.video = None
    message.animation = None
    message.video_note = None
    message.document = None
    message.reply_to_message = None
    message.from_user = MagicMock(id=99999)
    message.reply = AsyncMock()

    with patch("bot.handlers.download.queue_manager.can_user_download", AsyncMock(return_value=False)):
        await handle_circle_command(message)

    message.reply.assert_called_once()
    assert "уже выполняется задача" in message.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_circle_command_with_reply_video(tmp_path):
    reply_msg = MagicMock(spec=types.Message)
    reply_msg.video = MagicMock(spec=types.Video)
    reply_msg.animation = None
    reply_msg.video_note = None
    reply_msg.document = None
    reply_msg.text = None
    reply_msg.caption = None

    message = MagicMock(spec=types.Message)
    message.text = "/circle"
    message.video = None
    message.animation = None
    message.video_note = None
    message.document = None
    message.reply_to_message = reply_msg
    message.from_user = MagicMock(id=12345)
    message.chat = MagicMock(id=67890)
    message.message_id = 99

    status_msg = AsyncMock()
    status_msg.edit_text = AsyncMock()
    status_msg.delete = AsyncMock()
    message.reply = AsyncMock(return_value=status_msg)

    bot = AsyncMock()
    bot.send_video_note = AsyncMock()

    async def mock_download(media, destination):
        destination.write_bytes(b"tg_downloaded_video")

    bot.download = AsyncMock(side_effect=mock_download)
    message.bot = bot

    async def mock_convert(src, dst, max_duration=60, size=640):
        dst.write_bytes(b"circle_result")
        return dst

    with patch("bot.handlers.download.convert_to_video_note", side_effect=mock_convert):
        await handle_circle_command(message)

    bot.download.assert_called_once()
    bot.send_video_note.assert_called_once()
    assert bot.send_video_note.call_args[1]["chat_id"] == 67890
    assert bot.send_video_note.call_args[1]["reply_to_message_id"] == 99
