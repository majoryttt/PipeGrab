import pytest
from pathlib import Path

from bot.services.cookie_utils import (
    validate_netscape_cookies,
    parse_cookie_services,
    sanitize_netscape_content,
    ensure_cookies_file_normalized,
)


def validate_and_parse_cookies(content: str):
    valid = validate_netscape_cookies(content)
    if not valid:
        return False, []
    services = parse_cookie_services(content)
    return True, services


def test_valid_netscape_cookies():
    sample_cookies = (
        "# Netscape HTTP Cookie File\n"
        "# https://curl.haxx.se/docs/http-cookies.html\n"
        ".instagram.com\tTRUE\t/\tTRUE\t1750000000\tsessionid\t12345%3Aabcde\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1750000000\tSID\tabc123xyz\n"
        ".reddit.com\tTRUE\t/\tTRUE\t1750000000\treddit_session\trd123\n"
    )
    valid, services = validate_and_parse_cookies(sample_cookies)
    assert valid is True
    assert any("Instagram" in s for s in services)
    assert any("YouTube" in s for s in services)
    assert any("Reddit" in s for s in services)


def test_invalid_cookies():
    invalid_content = "This is not a cookie file.\nIt has just plain text or random json: {'a': 1}"
    valid, services = validate_and_parse_cookies(invalid_content)
    assert valid is False
    assert services == []


def test_sanitize_netscape_content():
    # .domain with FALSE flag violates Python http.cookiejar assertion
    broken = ".instagram.com\tFALSE\t/\tTRUE\t1800000000\tsessionid\tval\n"
    sanitized = sanitize_netscape_content(broken)
    assert ".instagram.com\tTRUE\t/\tTRUE\t1800000000\tsessionid\tval" in sanitized

    # host-only domain with TRUE flag
    broken_host = "instagram.com\tTRUE\t/\tTRUE\t1800000000\tsessionid\tval\n"
    sanitized_host = sanitize_netscape_content(broken_host)
    assert "instagram.com\tFALSE\t/\tTRUE\t1800000000\tsessionid\tval" in sanitized_host


def test_ensure_cookies_file_normalized(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    # Unsanitized: domain starts with dot, but flag is FALSE
    cookie_file.write_text(".youtube.com\tFALSE\t/\tTRUE\t1800000000\tSID\tval\n", encoding="utf-8")

    modified = ensure_cookies_file_normalized(cookie_file)
    assert modified is True

    content = cookie_file.read_text(encoding="utf-8")
    assert ".youtube.com\tTRUE\t/\tTRUE\t1800000000\tSID\tval" in content

    # Second run should not modify again
    modified_again = ensure_cookies_file_normalized(cookie_file)
    assert modified_again is False


def test_parse_cookies_for_domain(tmp_path):
    from bot.services.cookie_utils import parse_cookies_for_domain, get_cookies_for_domain
    from bot.config import settings

    raw = (
        ".reddit.com\tTRUE\t/\tTRUE\t1800000000\treddit_session\tabc%3A123\n"
        ".reddit.com\tTRUE\t/\tTRUE\t1800000000\tloid\txyz789\n"
        ".instagram.com\tTRUE\t/\tTRUE\t1800000000\tsessionid\tig_secret\n"
    )
    reddit_cookies = parse_cookies_for_domain(raw, "reddit.com")
    assert reddit_cookies == {"reddit_session": "abc%3A123", "loid": "xyz789"}

    ig_cookies = parse_cookies_for_domain(raw, "instagram.com")
    assert ig_cookies == {"sessionid": "ig_secret"}

    empty = parse_cookies_for_domain(raw, "tiktok.com")
    assert empty == {}

    # Test get_cookies_for_domain with patched settings
    c_file = tmp_path / "test_cookies.txt"
    c_file.write_text(raw, encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "cookies_file", c_file)
        result = get_cookies_for_domain("reddit.com")
        assert result.get("reddit_session") == "abc%3A123"

