import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, InputMediaDocument

from bot.services.image_utils import is_valid_image_file, sanitize_image
from bot.handlers.download import send_media_album_robust


def test_is_valid_image_file(tmp_path: Path):
    # 1. Valid JPEG
    jpg_file = tmp_path / "test.jpg"
    jpg_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200)
    assert is_valid_image_file(jpg_file) is True

    # 2. Valid PNG
    png_file = tmp_path / "test.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    assert is_valid_image_file(png_file) is True

    # 3. Valid WebP
    webp_file = tmp_path / "test.webp"
    webp_file.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 200)
    assert is_valid_image_file(webp_file) is True

    # 4. HTML error response
    html_file = tmp_path / "error.html"
    html_file.write_bytes(b"<!DOCTYPE html><html><body>403 Forbidden</body></html>" * 5)
    assert is_valid_image_file(html_file) is False

    # 5. JSON error response
    json_file = tmp_path / "error.json"
    json_file.write_bytes(b'{"error": "Access denied", "code": 403}' * 5)
    assert is_valid_image_file(json_file) is False

    # 6. Empty file
    empty_file = tmp_path / "empty.jpg"
    empty_file.write_bytes(b"")
    assert is_valid_image_file(empty_file) is False


@pytest.mark.asyncio
async def test_sanitize_image_discards_html(tmp_path: Path):
    bad_img = tmp_path / "bad.jpg"
    bad_img.write_bytes(b"<html><head><title>Error</title></head></html>" * 5)
    res = await sanitize_image(bad_img)
    assert res is None


@pytest.mark.asyncio
async def test_send_media_album_robust_recovers_from_image_process_failed(tmp_path: Path):
    file1 = tmp_path / "slide_1.jpg"
    file1.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)
    file2 = tmp_path / "slide_2.jpg"
    file2.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)
    file3 = tmp_path / "slide_3.jpg"
    file3.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)

    bot = MagicMock()
    # First call fails on message #2 with IMAGE_PROCESS_FAILED
    # Second call (after removing #2) succeeds
    bot.send_media_group = AsyncMock(
        side_effect=[
            TelegramBadRequest(
                method=MagicMock(),
                message='failed to send message #2 with the error message "IMAGE_PROCESS_FAILED"'
            ),
            MagicMock()
        ]
    )
    bot.send_document = AsyncMock()

    ok = await send_media_album_robust(
        bot=bot,
        chat_id=12345,
        valid_files=[file1, file2, file3],
        title="Test Slideshow",
        url="https://tiktok.com/test",
        platform_name="TikTok"
    )

    assert ok is True
    assert bot.send_media_group.call_count == 2
    # The bad item (#2) was sent as a document
    bot.send_document.assert_called_once()
    # The retry sent a media group with 2 items
    retry_media = bot.send_media_group.call_args_list[1].kwargs["media"]
    assert len(retry_media) == 2


@pytest.mark.asyncio
async def test_send_media_album_robust_falls_back_to_documents_on_full_failure(tmp_path: Path):
    file1 = tmp_path / "slide_1.jpg"
    file1.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)
    file2 = tmp_path / "slide_2.jpg"
    file2.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)

    bot = MagicMock()
    # First call fails with generic IMAGE_PROCESS_FAILED (no index)
    bot.send_media_group = AsyncMock(
        side_effect=[
            TelegramBadRequest(
                method=MagicMock(),
                message='Bad Request: IMAGE_PROCESS_FAILED'
            ),
            MagicMock()  # Document fallback succeeds
        ]
    )

    ok = await send_media_album_robust(
        bot=bot,
        chat_id=12345,
        valid_files=[file1, file2],
        title="Test Slideshow",
        url="https://tiktok.com/test",
        platform_name="TikTok"
    )

    assert ok is True
    assert bot.send_media_group.call_count == 2
    # Verify fallback called with InputMediaDocument
    fallback_media = bot.send_media_group.call_args_list[1].kwargs["media"]
    assert all(isinstance(m, InputMediaDocument) for m in fallback_media)
