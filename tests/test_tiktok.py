import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path

from bot.services.tiktok import (
    is_tiktok_url,
    TikTokService,
    TikTokData,
)
from bot.services.downloader import detect_platform, Platform, downloader_service, MediaType


def test_is_tiktok_url():
    assert is_tiktok_url("https://www.tiktok.com/@user/video/1234567890")
    assert is_tiktok_url("https://vm.tiktok.com/ZMh12345/")
    assert is_tiktok_url("https://vt.tiktok.com/ZSq4nH1q5/")
    assert is_tiktok_url("https://www.tiktok.com/@k1ry11/photo/7683211455331765512")
    assert not is_tiktok_url("https://youtube.com/watch?v=123")


@pytest.mark.asyncio
async def test_tiktok_photo_extract_and_download(tmp_path):
    service = TikTokService()

    fake_tikwm_response = {
        "code": 0,
        "data": {
            "id": "7683211455331765512",
            "title": "Photo slideshow title",
            "author": {"nickname": "tiktok_creator", "unique_id": "creator"},
            "images": [
                "https://example.com/slide1.jpg",
                "https://example.com/slide2.jpg"
            ],
            "music": "https://example.com/audio.mp3",
            "music_info": {"title": "Cool Song", "play": "https://example.com/audio.mp3"},
            "duration": 0
        }
    }

    async def fake_chunks(chunk_size):
        yield b"fake-image-bytes"

    with patch.object(service, "_extract_via_gallery_dl", new_callable=AsyncMock, return_value=None), \
         patch("aiohttp.ClientSession.get") as mock_get:
        # Mock API response
        mock_api_resp = AsyncMock()
        mock_api_resp.status = 200
        mock_api_resp.json = AsyncMock(return_value=fake_tikwm_response)

        # Mock Image & Audio downloads
        mock_media_resp = MagicMock()
        mock_media_resp.status = 200
        mock_media_resp.content.iter_chunked = fake_chunks
        mock_media_resp.read = AsyncMock(return_value=b"fake-image-bytes")

        # Set up side effects for get calls
        mock_get.return_value.__aenter__.side_effect = [
            mock_api_resp,     # TikWM API call
            mock_media_resp,   # Image 1
            mock_media_resp,   # Image 2
            mock_media_resp,   # Audio
        ]

        data = await service.extract_data("https://www.tiktok.com/@k1ry11/photo/7683211455331765512")
        assert data is not None
        assert data.is_photo is True
        assert len(data.image_urls) == 2
        assert data.title == "Photo slideshow title"
        assert data.uploader == "tiktok_creator"

        files, audio_file, media_type = await service.download_media(data, tmp_path)
        assert media_type == "album"
        assert len(files) == 2
        assert all(f.exists() for f in files)
        assert audio_file is not None and audio_file.exists()


@pytest.mark.asyncio
async def test_downloader_tiktok_photo_flow():
    url = "https://www.tiktok.com/@user/photo/12345"
    fake_data = TikTokData(
        url=url,
        item_id="12345",
        title="Slideshow",
        uploader="creator",
        is_photo=True,
        image_urls=["https://example.com/1.jpg", "https://example.com/2.jpg"],
        music_url="https://example.com/music.mp3"
    )

    with patch("bot.services.tiktok.tiktok_service.extract_data", AsyncMock(return_value=fake_data)):
        info = await downloader_service.get_info(url)
        assert info is not None
        assert info.platform == Platform.TIKTOK
        assert info.media_type == MediaType.ALBUM
        assert info.title == "Slideshow"
