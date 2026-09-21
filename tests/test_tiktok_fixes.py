import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path

from bot.services.tiktok import (
    resolve_tiktok_url,
    tiktok_service,
    TikTokData,
)
from bot.services.downloader import (
    detect_platform,
    Platform,
    downloader_service,
    MediaType,
)


@pytest.mark.asyncio
async def test_resolve_tiktok_short_url_t():
    """Verify that tiktok.com/t/... urls are recognized for redirect resolution."""
    url = "https://www.tiktok.com/t/ZTUEwTuME/"
    expected_resolved = "https://www.tiktok.com/@wise_pay/photo/7659785252029615393?_r=1"

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.url = expected_resolved
        mock_get.return_value.__aenter__.return_value = mock_resp

        resolved = await resolve_tiktok_url(url)
        assert resolved == expected_resolved


@pytest.mark.asyncio
async def test_tiktok_gallery_dl_extraction():
    """Verify gallery-dl integration for TikTok photo slideshows."""
    url = "https://www.tiktok.com/@user/photo/12345"

    fake_gallery_dl_output = [
        [2, {"desc": "Amazing Slideshow <3", "author": {"nickname": "Photographer"}}],
        [3, "https://example.com/slide1.jpg", {"extension": "jpg"}],
        [3, "https://example.com/slide2.jpg", {"extension": "jpg"}],
        [3, "https://example.com/music.mp3", {"extension": "mp3"}],
    ]

    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 0
        import json
        mock_res.stdout = json.dumps(fake_gallery_dl_output)
        mock_run.return_value = mock_res

        data = await tiktok_service._extract_via_gallery_dl(url)
        assert data is not None
        assert data.is_photo is True
        assert len(data.image_urls) == 2
        assert data.title == "Amazing Slideshow <3"
        assert data.uploader == "Photographer"
        assert data.music_url == "https://example.com/music.mp3"


@pytest.mark.asyncio
async def test_downloader_tiktok_t_link_flow():
    """Verify that downloader_service.get_info resolves /t/ link and returns album."""
    short_url = "https://www.tiktok.com/t/ZTUEwTuME/"
    resolved_url = "https://www.tiktok.com/@user/photo/12345"

    fake_data = TikTokData(
        url=resolved_url,
        item_id="12345",
        title="Slideshow",
        uploader="creator",
        is_photo=True,
        image_urls=["https://example.com/1.jpg", "https://example.com/2.jpg"],
        music_url="https://example.com/music.mp3"
    )

    with patch("bot.services.downloader.resolve_tiktok_url", AsyncMock(return_value=resolved_url)):
        with patch("bot.services.tiktok.tiktok_service.extract_data", AsyncMock(return_value=fake_data)):
            info = await downloader_service.get_info(short_url)
            assert info is not None
            assert info.platform == Platform.TIKTOK
            assert info.media_type == MediaType.ALBUM
            assert info.title == "Slideshow"
