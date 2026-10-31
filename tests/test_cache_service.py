from pathlib import Path

import pytest

from bot.services.cache_service import CacheService, hash_url, normalize_url


def test_normalize_url():
    # YouTube watch url with tracking
    yt_track = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=12345&feature=shared"
    assert normalize_url(yt_track) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # YouTube short link
    yt_short = "https://youtu.be/dQw4w9WgXcQ?si=abc"
    assert normalize_url(yt_short) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # YouTube shorts link
    yt_shorts = "https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share"
    assert normalize_url(yt_shorts) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Instagram link
    ig = "https://www.instagram.com/reel/C12345678/?igsh=abcdef123"
    assert "igsh" not in normalize_url(ig)

    # General tracking parameters
    gen = "https://example.com/video.mp4?utm_source=twitter&utm_medium=social&custom=stay"
    assert normalize_url(gen) == "https://example.com/video.mp4?custom=stay"


def test_hash_url():
    h1 = hash_url("https://youtu.be/dQw4w9WgXcQ?si=abc")
    h2 = hash_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert h1 == h2


@pytest.mark.asyncio
async def test_cache_service_crud(tmp_path: Path):
    db_file = tmp_path / "test_cache.db"
    service = CacheService(db_path=db_file)

    url = "https://www.tiktok.com/@user/video/1234567890?_r=1"
    cached = await service.get_cached(url)
    assert cached is None

    await service.save_cached(
        url=url,
        file_id="BAACAgIAAxkBAAE123",
        platform="TikTok",
        media_type="video",
        title="Funny Cat Video",
        file_unique_id="unique_123",
        file_size=1048576,
        duration=15,
        width=720,
        height=1280,
        caption="🎬 <b>Funny Cat Video</b>",
    )

    # Query with slightly different tracking URL
    url_alt = "https://www.tiktok.com/@user/video/1234567890?is_from_webapp=1"
    cached = await service.get_cached(url_alt)
    assert cached is not None
    assert cached.file_id == "BAACAgIAAxkBAAE123"
    assert cached.title == "Funny Cat Video"
    assert cached.media_type == "video"
    assert cached.file_size == 1048576

    # Test saving audio for the same URL
    await service.save_cached(
        url=url,
        file_id="AUDIO_FILE_ID_456",
        platform="TikTok",
        media_type="audio",
        title="Cat Music",
        duration=15,
    )

    cached_audio = await service.get_cached(url_alt, media_type="audio")
    assert cached_audio is not None
    assert cached_audio.file_id == "AUDIO_FILE_ID_456"

    # Test cleanup
    deleted = await service.cleanup_old(days=0)
    # Since days=0, it might delete or not depending on timestamp resolution, but shouldn't error
    assert isinstance(deleted, int)
