import pytest
from pathlib import Path


def validate_and_parse_cookies(content: str):
    is_netscape = (
        "# Netscape HTTP Cookie File" in content or
        "# HTTP Cookie File" in content or
        any("\t" in line and len(line.split("\t")) >= 7 for line in content.splitlines() if not line.startswith("#"))
    )
    if not is_netscape:
        return False, []

    services = []
    if "instagram.com" in content:
        services.append("Instagram")
    if "youtube.com" in content or "google.com" in content:
        services.append("YouTube")
    if "tiktok.com" in content:
        services.append("TikTok")
    if "pinterest.com" in content:
        services.append("Pinterest")

    return True, services


def test_valid_netscape_cookies():
    sample_cookies = (
        "# Netscape HTTP Cookie File\n"
        "# https://curl.haxx.se/docs/http-cookies.html\n"
        ".instagram.com\tTRUE\t/\tTRUE\t1750000000\tsessionid\t12345%3Aabcde\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1750000000\tSID\tabc123xyz\n"
    )
    valid, services = validate_and_parse_cookies(sample_cookies)
    assert valid is True
    assert "Instagram" in services
    assert "YouTube" in services


def test_invalid_cookies():
    invalid_content = "This is not a cookie file.\nIt has just plain text or random json: {'a': 1}"
    valid, services = validate_and_parse_cookies(invalid_content)
    assert valid is False
    assert services == []
