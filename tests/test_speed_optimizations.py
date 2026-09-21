import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from bot.services.http_client import HTTPClient, http_client
from bot.services.downloader import DownloaderService, Platform, MediaType, MediaInfo, detect_platform
from bot.services.tiktok import TikTokData, tiktok_service
from bot.services.instagram import InstagramData, InstagramItem, instagram_service


@pytest.mark.asyncio
async def test_http_client_singleton_and_session():
    client = HTTPClient()
    session1 = await client.get_session()
    session2 = await client.get_session()
    assert session1 is session2
    assert not session1.closed
    await client.close()
    assert session1.closed


@pytest.mark.asyncio
async def test_downloader_service_metadata_cache():
    downloader = DownloaderService()
    url = "https://www.youtube.com/watch?v=speedtest123"
    info = MediaInfo(
        title="Test Video",
        duration=120,
        uploader="Test Channel",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.YOUTUBE,
        url=url,
        media_type=MediaType.VIDEO
    )

    # Initially not cached
    assert downloader.get_cached_info(url) is None

    # Set cache
    downloader.set_cached_info(url, info)
    cached = downloader.get_cached_info(url)
    assert cached is not None
    assert cached.title == "Test Video"

    # Verify TTL expiration
    downloader._cache[url] = (time.time() - 301, info)
    assert downloader.get_cached_info(url) is None


def test_ydl_speed_opts():
    downloader = DownloaderService()
    opts = downloader._get_base_ydl_opts()
    assert opts.get("concurrent_fragment_downloads") == 8
    assert opts.get("buffersize") == 1048576
    assert opts.get("http_chunk_size") == 10485760


@pytest.mark.asyncio
async def test_tiktok_direct_video_download(tmp_path: Path):
    tk_data = TikTokData(
        url="https://www.tiktok.com/@user/video/123456789",
        item_id="123456789",
        title="Speedy TikTok Video",
        uploader="user",
        is_photo=False,
        video_url="https://fake.tiktokcdn.com/video.mp4",
        duration=15
    )

    with patch.object(http_client, "download_file", new_callable=AsyncMock) as mock_dl:
        async def fake_download(url, dest, headers=None):
            dest.write_bytes(b"fake-video-content")
            return True

        mock_dl.side_effect = fake_download

        files, audio, mtype = await tiktok_service.download_media(tk_data, tmp_path)
        assert mtype == "video"
        assert len(files) == 1
        assert files[0].exists()
        assert audio is None
        mock_dl.assert_called_once()


@pytest.mark.asyncio
async def test_tiktok_parallel_slideshow_download(tmp_path: Path):
    tk_data = TikTokData(
        url="https://www.tiktok.com/@user/photo/123456789",
        item_id="123456789",
        title="Photo Slideshow",
        uploader="user",
        is_photo=True,
        image_urls=[
            "https://fake.tiktokcdn.com/1.jpg",
            "https://fake.tiktokcdn.com/2.jpg",
            "https://fake.tiktokcdn.com/3.jpg"
        ],
        music_url="https://fake.tiktokcdn.com/music.mp3",
        duration=0
    )

    with patch.object(http_client, "download_file", new_callable=AsyncMock) as mock_dl:
        async def fake_download(url, dest, headers=None):
            dest.write_bytes(b"fake-media-content")
            return True

        mock_dl.side_effect = fake_download

        files, audio, mtype = await tiktok_service.download_media(tk_data, tmp_path)
        assert mtype == "album"
        assert len(files) == 3
        assert audio is not None
        assert audio.exists()
        # 3 images + 1 audio = 4 calls
        assert mock_dl.call_count == 4


@pytest.mark.asyncio
async def test_instagram_parallel_items_download(tmp_path: Path):
    ig_data = InstagramData(
        url="https://www.instagram.com/p/ABC123xyz/",
        shortcode="ABC123xyz",
        title="Instagram Album",
        uploader="insta_user",
        media_type="album",
        items=[
            InstagramItem(media_type="photo", url="https://fake.cdninstagram.com/1.jpg"),
            InstagramItem(media_type="video", url="https://fake.cdninstagram.com/2.mp4"),
        ]
    )

    with patch.object(http_client, "download_file", new_callable=AsyncMock) as mock_dl:
        async def fake_download(url, dest, headers=None):
            dest.write_bytes(b"fake-media-content")
            return True

        mock_dl.side_effect = fake_download

        files, final_type = await instagram_service.download_media(ig_data, tmp_path)
        assert final_type == "album"
        assert len(files) == 2
        assert mock_dl.call_count == 2
