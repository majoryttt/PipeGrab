import pytest
from bot.services.pinterest import (
    is_pinterest_url,
    normalize_to_original_image_url,
    extract_image_signature,
    PinterestService,
    PinterestData,
)
from bot.services.downloader import detect_platform, Platform


@pytest.mark.parametrize("url,expected", [
    ("https://ru.pinterest.com/pin/1113444707905303804/", True),
    ("https://pin.it/5hYq47KDQ", True),
    ("https://www.pinterest.com/pin/123456789/", True),
    ("https://www.pinterest.co.uk/pin/987654321/", True),
    ("https://pinterest.com/creator/awesome-board/", True),
    ("https://youtube.com/watch?v=123", False),
    ("https://instagram.com/reel/123", False),
])
def test_is_pinterest_url(url, expected):
    assert is_pinterest_url(url) == expected
    if expected:
        assert detect_platform(url) == Platform.PINTEREST


def test_normalize_image_url():
    thumb_url = "https://i.pinimg.com/736x/71/6e/f7/716ef79b23557039988a751e5777890a.jpg"
    orig_url = normalize_to_original_image_url(thumb_url)
    assert orig_url == "https://i.pinimg.com/originals/71/6e/f7/716ef79b23557039988a751e5777890a.jpg"

    thumb236 = "https://i.pinimg.com/236x/ab/cd/ef/abcdef1234567890abcdef1234567890.png"
    assert normalize_to_original_image_url(thumb236) == "https://i.pinimg.com/originals/ab/cd/ef/abcdef1234567890abcdef1234567890.png"


def test_extract_image_signature():
    url1 = "https://i.pinimg.com/originals/71/6e/f7/716ef79b23557039988a751e5777890a.png"
    url2 = "https://i.pinimg.com/736x/71/6e/f7/716ef79b23557039988a751e5777890a.jpg"
    assert extract_image_signature(url1) == "716ef79b23557039988a751e5777890a"
    assert extract_image_signature(url2) == "716ef79b23557039988a751e5777890a"
    # Signatures match, allowing deduplication
    assert extract_image_signature(url1) == extract_image_signature(url2)


@pytest.mark.asyncio
async def test_extract_mock_html():
    service = PinterestService()

    # Mock HTML with JSON-LD
    mock_html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@type": "SocialMediaPosting",
            "headline": "Test Pin Title",
            "author": {"name": "Photographer"},
            "image": "https://i.pinimg.com/originals/11/22/33/11223344556677889900aabbccddeeff.jpg"
        }
        </script>
    </head>
    <body></body>
    </html>
    """
    from unittest.mock import patch, AsyncMock, MagicMock

    with patch.object(service, "_extract_with_gallery_dl", return_value=None):
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.text.return_value = mock_html
            mock_resp.__aenter__.return_value = mock_resp
            mock_get.return_value = mock_resp

            pin_data = await service.extract_data("https://www.pinterest.com/pin/123456789/")
            assert pin_data is not None
            assert pin_data.title == "Test Pin Title"
            assert pin_data.uploader == "Photographer"
            assert pin_data.media_type == "photo"
            assert len(pin_data.image_urls) == 1
            assert "11223344556677889900aabbccddeeff" in pin_data.image_urls[0]


def test_extract_with_gallery_dl_single():
    service = PinterestService()
    import json
    from unittest.mock import patch, MagicMock

    gdl_output = json.dumps([
        [2, {"id": "941041284696943105", "title": "Cute Art", "pinner": {"full_name": "Artist"}}],
        [3, "https://i.pinimg.com/originals/ff/e4/74/ffe47453cdf631077a354206fa283bdc.jpg", {"extension": "jpg"}]
    ])

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=gdl_output, stderr="")
        data = service._extract_with_gallery_dl("https://www.pinterest.com/pin/941041284696943105/")
        assert data is not None
        assert data.pin_id == "941041284696943105"
        assert data.media_type == "photo"
        assert data.title == "Cute Art"
        assert data.uploader == "Artist"
        assert len(data.image_urls) == 1


def test_extract_with_gallery_dl_carousel():
    service = PinterestService()
    import json
    from unittest.mock import patch, MagicMock

    gdl_output = json.dumps([
        [2, {"id": "1129207306672255199", "title": "Instructions", "native_creator": {"username": "razil"}}],
        [3, "https://i.pinimg.com/originals/11/11/11/1.jpg", {"extension": "jpg"}],
        [3, "https://i.pinimg.com/originals/22/22/22/2.jpg", {"extension": "jpg"}]
    ])

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=gdl_output, stderr="")
        data = service._extract_with_gallery_dl("https://www.pinterest.com/pin/1129207306672255199/")
        assert data is not None
        assert data.pin_id == "1129207306672255199"
        assert data.media_type == "album"
        assert data.uploader == "razil"
        assert len(data.image_urls) == 2

