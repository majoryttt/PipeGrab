import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from bot.services.instagram import (
    is_instagram_url,
    extract_shortcode,
    clean_instagram_url,
    get_highest_res_thumbnail,
    select_best_video_format,
    instagram_service,
    InstagramService,
    InstagramData,
    InstagramItem,
)
from bot.services.downloader import Platform, downloader_service, MediaType
from bot.config import settings


def test_clean_instagram_url():
    url = "https://www.instagram.com/reel/Dc0H9LLthrq/?stkn=MTZiOHF6dnpwdTFudQ=="
    assert clean_instagram_url(url) == "https://www.instagram.com/reel/Dc0H9LLthrq/"

    url_story = "https://www.instagram.com/stories/username/1234567890/?igsh=abc"
    assert clean_instagram_url(url_story) == "https://www.instagram.com/stories/username/1234567890/"

    url_post = "https://www.instagram.com/p/DdRMjkEjpuX/?utm_source=ig_web_copy_link"
    assert clean_instagram_url(url_post) == "https://www.instagram.com/p/DdRMjkEjpuX/"


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
async def test_instagram_extract_carousel_with_dash_video():
    """Verify carousel items with DASH streams select the stream with audio."""
    service = InstagramService()

    fake_ydl_info = {
        "title": "Album with Video",
        "uploader": "traveler",
        "_type": "playlist",
        "entries": [
            {
                "formats": [],
                "thumbnails": [{"url": "https://example.com/photo1.jpg", "width": 1080, "height": 1080}],
            },
            {
                "formats": [
                    {
                        "url": "https://example.com/video_audio.mp4",
                        "ext": "mp4",
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                        "width": 720,
                        "height": 1280,
                    },
                    {
                        "url": "https://example.com/video_silent_dash.mp4",
                        "ext": "mp4",
                        "vcodec": "avc1",
                        "acodec": "none",
                        "width": 1080,
                        "height": 1920,
                    },
                ],
                "thumbnails": [{"url": "https://example.com/thumb2.jpg"}],
            }
        ]
    }

    with patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        data = await service.extract_data("https://www.instagram.com/p/DdRMjkEjpuX/")
        assert data is not None
        assert data.media_type == "album"
        assert len(data.items) == 2
        assert data.items[1].media_type == "video"
        assert data.items[1].url == "https://example.com/video_audio.mp4"


@pytest.mark.asyncio
async def test_instagram_story_without_cookies():
    with patch.object(type(settings), "has_cookies", property(lambda self: False)):
        info = await downloader_service.get_info("https://www.instagram.com/stories/username/1234567890/")
        assert info is not None
        assert info.error_message == "AUTH_REQUIRED_INSTAGRAM_STORY"


def test_select_best_video_format_prefers_audio_stream():
    """Verify that video formats with audio are selected over video-only DASH formats."""
    formats = [
        # Progressive format with audio (e.g., 720p)
        {
            "format_id": "0",
            "url": "https://example.com/progressive_720p.mp4",
            "ext": "mp4",
            "vcodec": "avc1.4d401f",
            "acodec": "mp4a.40.2",
            "width": 720,
            "height": 1280,
        },
        # DASH video format without audio (e.g., 1080p, acodec='none')
        {
            "format_id": "dash-video-1",
            "url": "https://example.com/dash_video_1080p_silent.mp4",
            "ext": "mp4",
            "vcodec": "avc1.640028",
            "acodec": "none",
            "width": 1080,
            "height": 1920,
        },
        # DASH audio format without video (vcodec='none')
        {
            "format_id": "dash-audio-1",
            "url": "https://example.com/dash_audio.m4a",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
        },
    ]

    best_url = select_best_video_format(formats)
    assert best_url == "https://example.com/progressive_720p.mp4"


@pytest.mark.asyncio
async def test_instagram_extract_video_with_dash_selects_audio_stream():
    """Verify extract_data picks the format with audio for Instagram single video/reel."""
    service = InstagramService()

    fake_ydl_info = {
        "title": "Cool Reel",
        "uploader": "creator",
        "duration": 30,
        "formats": [
            {
                "url": "https://example.com/audio_video.mp4",
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "mp4a",
                "width": 720,
                "height": 1280,
            },
            {
                "url": "https://example.com/silent_dash_video.mp4",
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "none",
                "width": 1080,
                "height": 1920,
            },
        ],
        "thumbnails": [{"url": "https://example.com/thumb.jpg"}],
    }

    with patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        data = await service.extract_data("https://www.instagram.com/reel/C123abc/")
        assert data is not None
        assert data.media_type == "video"
        assert len(data.items) == 1
        assert data.items[0].url == "https://example.com/audio_video.mp4"


@pytest.mark.asyncio
async def test_instagram_extract_video_with_none_vcodec():
    """Verify progressive video with vcodec=None is correctly recognized as video, not photo."""
    service = InstagramService()

    fake_ydl_info = {
        "title": "Progressive Reel",
        "uploader": "creator",
        "duration": 45,
        "ext": "mp4",
        "formats": [
            {
                "url": "https://example.com/progressive.mp4",
                "ext": "mp4",
                "vcodec": None,
                "acodec": None,
                "width": 360,
                "height": 640,
            }
        ],
        "thumbnails": [{"url": "https://example.com/thumb.jpg"}],
    }

    with patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        data = await service.extract_data("https://www.instagram.com/reel/DTqW-nMjvUv/")
        assert data is not None
        assert data.media_type == "video"
        assert len(data.items) == 1
        assert data.items[0].url == "https://example.com/progressive.mp4"


@pytest.mark.asyncio
async def test_instagram_reel_routes_to_ytdlp_download(tmp_path):
    """
    Verify that downloader_service.download_media for an Instagram Reel
    does NOT intercept via direct HTTP download, but lets yt-dlp handle it with audio merge.
    """
    fake_info = {
        "title": "Instagram Reel Video",
        "uploader": "musician",
        "duration": 25,
        "width": 1080,
        "height": 1920,
        "ext": "mp4",
    }

    def fake_extract_info(url, download=True):
        # yt-dlp creates the downloaded file according to outtmpl
        opts = mock_ydl_cls.call_args[0][0]
        outtmpl = opts["outtmpl"]
        file_path = Path(outtmpl.replace("%(title).100B.%(ext)s", "reel.mp4"))
        file_path.write_bytes(b"dummy-video-content-with-audio")
        return fake_info

    with patch.object(downloader_service, "download_dir", tmp_path), \
         patch.object(type(settings), "has_cookies", property(lambda s: False)), \
         patch("yt_dlp.YoutubeDL") as mock_ydl_cls, \
         patch("bot.services.downloader.generate_thumbnail", new_callable=AsyncMock) as mock_thumb:

        mock_ydl_instance = mock_ydl_cls.return_value.__enter__.return_value
        mock_ydl_instance.extract_info.side_effect = fake_extract_info
        mock_thumb.return_value = None

        media = await downloader_service.download_media("https://www.instagram.com/reel/DTqW-nMjvUv/")

        assert media is not None
        assert media.media_type == MediaType.VIDEO
        assert media.file_path.exists()
        assert media.file_path.read_bytes() == b"dummy-video-content-with-audio"
        # yt-dlp was called to download and merge
        mock_ydl_instance.extract_info.assert_called_once()


@pytest.mark.asyncio
async def test_instagram_reel_with_cookies_uses_gallery_dl():
    """
    Verify that when cookies are present and yt-dlp fails (e.g. HTTP 400 Bad Request),
    gallery-dl fallback is used and extracts the Reel with audio.
    """
    url = "https://www.instagram.com/reel/Dc0H9LLthrq/?stkn=MTZiOHF6dnpwdTFudQ=="

    fake_gallery_data = InstagramData(
        url="https://www.instagram.com/reel/Dc0H9LLthrq/",
        shortcode="Dc0H9LLthrq",
        title="Reel with sound",
        uploader="creator",
        media_type="video",
        items=[InstagramItem(media_type="video", url="https://example.com/progressive_audio.mp4")],
        duration=15
    )

    with patch.object(type(settings), "has_cookies", property(lambda s: True)), \
         patch.object(instagram_service, "_extract_via_gallery_dl", AsyncMock(return_value=fake_gallery_data)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("HTTP Error 400: Bad Request"))):
        data = await instagram_service.extract_data(url)
        assert data is not None
        assert data.title == "Reel with sound"
        assert data.items[0].url == "https://example.com/progressive_audio.mp4"

