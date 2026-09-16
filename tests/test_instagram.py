import pytest
from unittest.mock import patch, AsyncMock
from bot.services.instagram import (
    is_instagram_url,
    extract_shortcode,
    get_highest_res_thumbnail,
    InstagramService,
    InstagramData,
    InstagramItem,
)
from bot.services.downloader import Platform, downloader_service, MediaType
from bot.config import settings


def test_is_instagram_url():
    assert is_instagram_url("https://www.instagram.com/p/DdRMjkEjpuX/")
    assert is_instagram_url("https://instagram.com/reel/C123abc/")
    assert is_instagram_url("https://www.instagram.com/stories/user/12345/")
    assert not is_instagram_url("https://tiktok.com/@user/video/123")


def test_extract_shortcode():
    assert extract_shortcode("https://www.instagram.com/p/DdRMjkEjpuX/?utm=1") == "DdRMjkEjpuX"
    assert extract_shortcode("https://instagram.com/reel/C123abc/") == "C123abc"
    assert extract_shortcode("https://www.instagram.com/stories/username/1234567890/") == "username"


def test_get_highest_res_thumbnail():
    thumbs = [
        {"url": "https://example.com/small.jpg", "width": 150, "height": 150},
        {"url": "https://example.com/large.jpg", "width": 1080, "height": 1080},
        {"url": "https://example.com/medium.jpg", "width": 640, "height": 640},
    ]
    assert get_highest_res_thumbnail(thumbs) == "https://example.com/large.jpg"


@pytest.mark.asyncio
async def test_instagram_extract_photo_post():
    service = InstagramService()

    fake_ydl_info = {
        "title": "Beautiful Sunset",
        "uploader": "photographer",
        "formats": [],  # No video formats!
        "thumbnails": [
            {"url": "https://example.com/thumb_small.jpg", "width": 320, "height": 320},
            {"url": "https://example.com/thumb_large.jpg", "width": 1080, "height": 1080},
        ]
    }

    with patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        data = await service.extract_data("https://www.instagram.com/p/DdRMjkEjpuX/")
        assert data is not None
        assert data.media_type == "photo"
        assert len(data.items) == 1
        assert data.items[0].url == "https://example.com/thumb_large.jpg"
        assert data.title == "Beautiful Sunset"
        assert data.uploader == "photographer"


@pytest.mark.asyncio
async def test_instagram_extract_carousel_post():
    service = InstagramService()

    fake_ydl_info = {
        "title": "My Travel Album",
        "uploader": "traveler",
        "_type": "playlist",
        "entries": [
            {
                "formats": [],
                "thumbnails": [{"url": "https://example.com/photo1.jpg", "width": 1080, "height": 1080}],
            },
            {
                "formats": [{"url": "https://example.com/video2.mp4", "vcodec": "h264"}],
                "thumbnails": [{"url": "https://example.com/thumb2.jpg"}],
            }
        ]
    }

    with patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        data = await service.extract_data("https://www.instagram.com/p/DdRMjkEjpuX/")
        assert data is not None
        assert data.media_type == "album"
        assert len(data.items) == 2
        assert data.items[0].media_type == "photo"
        assert data.items[0].url == "https://example.com/photo1.jpg"
        assert data.items[1].media_type == "video"
        assert data.items[1].url == "https://example.com/video2.mp4"


@pytest.mark.asyncio
async def test_instagram_story_without_cookies():
    with patch.object(type(settings), "has_cookies", property(lambda self: False)):
        info = await downloader_service.get_info("https://www.instagram.com/stories/username/1234567890/")
        assert info is not None
        assert info.error_message == "AUTH_REQUIRED_INSTAGRAM_STORY"
