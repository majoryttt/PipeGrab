import pytest
from bot.services.downloader import extract_first_url, detect_platform, Platform


@pytest.mark.parametrize("text,expected_url", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("Посмотри вот это видео https://youtu.be/dQw4w9WgXcQ класс!", "https://youtu.be/dQw4w9WgXcQ"),
    ("https://vm.tiktok.com/ZMh12345/.", "https://vm.tiktok.com/ZMh12345/"),
    ("Ссылка: https://instagram.com/reel/C123abc/ и ещё текст", "https://instagram.com/reel/C123abc/"),
    ("https://x.com/user/status/1234567890", "https://x.com/user/status/1234567890"),
    ("Просто текст без ссылки", None),
])
def test_extract_first_url(text, expected_url):
    assert extract_first_url(text) == expected_url


@pytest.mark.parametrize("url,expected_platform", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", Platform.YOUTUBE),
    ("https://youtu.be/dQw4w9WgXcQ", Platform.YOUTUBE),
    ("https://www.youtube.com/shorts/abcdef123", Platform.YOUTUBE),
    ("https://www.youtube.com/playlist?list=PL1234567890", Platform.YOUTUBE),
    ("https://www.tiktok.com/@user/video/1234567890", Platform.TIKTOK),
    ("https://vm.tiktok.com/ZMh12345/", Platform.TIKTOK),
    ("https://instagram.com/reel/Cx12345/", Platform.INSTAGRAM),
    ("https://www.instagram.com/p/Cy56789/", Platform.INSTAGRAM),
    ("https://twitter.com/user/status/1234567890", Platform.TWITTER),
    ("https://x.com/user/status/1234567890", Platform.TWITTER),
    ("https://pinterest.com/pin/1234567890/", Platform.PINTEREST),
    ("https://ru.pinterest.com/pin/1113444707905303804/", Platform.PINTEREST),
    ("https://pin.it/7xYz123", Platform.PINTEREST),
    ("https://pin.it/5hYq47KDQ", Platform.PINTEREST),
    ("https://example.com/random/video.mp4", Platform.UNKNOWN),
])
def test_detect_platform(url, expected_platform):
    assert detect_platform(url) == expected_platform
