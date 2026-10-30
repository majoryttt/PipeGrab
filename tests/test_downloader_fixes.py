import asyncio
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from bot.config import settings
from bot.services.downloader import (
    downloader_service,
    detect_platform,
    Platform,
    DownloadProgress,
    MediaType,
)


@pytest.mark.asyncio
async def test_progress_hook_threadsafe_execution():
    """
    Verifies that progress callbacks dispatched from a worker thread
    using the captured running event loop do not raise
    'RuntimeError: There is no current event loop in thread asyncio_0'.
    """
    loop = asyncio.get_running_loop()
    received_progress = []

    async def async_callback(p: DownloadProgress):
        received_progress.append(p)

    def worker_thread():
        # Simulates yt-dlp running in a separate thread
        p = DownloadProgress(status="downloading", downloaded_bytes=1000, total_bytes=2000, percent=50.0)
        # Dispatching safely with captured loop:
        future = asyncio.run_coroutine_threadsafe(async_callback(p), loop)
        future.result(timeout=2.0)

    await asyncio.to_thread(worker_thread)

    assert len(received_progress) == 1
    assert received_progress[0].downloaded_bytes == 1000
    assert received_progress[0].percent == 50.0


@pytest.mark.asyncio
async def test_instagram_story_requires_cookies_warning():
    """
    When cookies are missing and an Instagram story URL is requested,
    get_info should immediately return AUTH_REQUIRED_INSTAGRAM_STORY.
    """
    url = "https://www.instagram.com/stories/someuser/1234567890/"
    # Ensure has_cookies is False for this test by patching property on class
    with patch.object(type(settings), "has_cookies", property(lambda self: False)):
        info = await downloader_service.get_info(url)
        assert info is not None
        assert info.platform == Platform.INSTAGRAM
        assert info.error_message == "AUTH_REQUIRED_INSTAGRAM_STORY"

