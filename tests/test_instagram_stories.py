import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from bot.services.instagram import (
    parse_instagram_story_url,
    instagram_service,
    InstagramData,
    InstagramItem,
)
from bot.services.downloader import downloader_service, Platform, MediaType
from bot.config import settings


def test_parse_instagram_story_url():
    # URL with ID
    user, sid = parse_instagram_story_url("https://www.instagram.com/stories/cristiano/3456789012345678901/")
    assert user == "cristiano"
    assert sid == "3456789012345678901"

    # URL with ID and query parameters
    user, sid = parse_instagram_story_url("https://instagram.com/stories/username/1234567890?utm_source=ig_story_item_share&igsh=abc")
    assert user == "username"
    assert sid == "1234567890"

    # URL without ID (all stories)
    user, sid = parse_instagram_story_url("https://www.instagram.com/stories/cristiano/")
    assert user == "cristiano"
    assert sid is None

    # Highlights
    user, sid = parse_instagram_story_url("https://www.instagram.com/stories/highlights/17999888777/")
    assert user == "highlights"
    assert sid == "17999888777"

    # Non-story post
    user, sid = parse_instagram_story_url("https://www.instagram.com/p/DdRMjkEjpuX/")
    assert user is None
    assert sid is None


@pytest.mark.asyncio
async def test_instagram_story_gallery_dl_extraction():
    url = "https://www.instagram.com/stories/user/1234567890/"

    fake_gallery_dl_output = [
        [2, {"user": {"username": "user"}}],
        [3, "https://example.com/story_photo.jpg", {"extension": "jpg", "width": 1080, "height": 1920}],
    ]

    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 0
        import json
        mock_res.stdout = json.dumps(fake_gallery_dl_output)
        mock_run.return_value = mock_res

        data = await instagram_service._extract_story_via_gallery_dl(url)
        assert data is not None
        assert data.media_type == "photo"
        assert len(data.items) == 1
        assert data.items[0].url == "https://example.com/story_photo.jpg"
        assert data.uploader == "user"


@pytest.mark.asyncio
async def test_instagram_story_unreachable_auth_error():
    """Verify that 'This content is unreachable' is detected as an auth/cookies error."""
    url = "https://www.instagram.com/stories/user/1234567890/"

    with patch.object(type(settings), "has_cookies", property(lambda s: True)):
        # gallery-dl fails
        with patch.object(instagram_service, "_extract_story_via_gallery_dl", AsyncMock(return_value=None)):
            # yt-dlp raises unreachable
            with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("ERROR: [instagram:story] 1234567890: This content is unreachable. Use --cookies-from-browser or --cookies for the authentication."))):
                data = await instagram_service.extract_data(url)
                assert data is not None
                assert data.error_message == "AUTH_REQUIRED_INSTAGRAM_STORY"
