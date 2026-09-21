import pytest
from bot.handlers.download import truncate_text, build_safe_caption


def test_truncate_text():
    assert truncate_text("short text", 50) == "short text"
    long_str = "a" * 100
    res = truncate_text(long_str, 50)
    assert len(res) == 50
    assert res.endswith("...")


def test_build_safe_caption_escaping():
    title = "Funny video <3 with & and <tag> text"
    url = "https://example.com/video?a=1&b=2"
    platform = "TikTok"
    uploader = "User <Admin>"

    caption = build_safe_caption(
        title=title,
        url=url,
        platform_name=platform,
        uploader=uploader,
        prefix="🎬"
    )

    # Must contain escaped versions
    assert "&lt;3" in caption
    assert "&amp;" in caption
    assert "&lt;tag&gt;" in caption
    assert "&lt;Admin&gt;" in caption
    # Unescaped angle brackets must NOT exist outside the approved tags
    # The only valid tags in caption are <b>, </b>, <i>, </i>, <a href='...'>, </a>
    for chunk in caption.split("<")[1:]:
        tag = chunk.split(">")[0]
        assert tag in ["b", "/b", "i", "/i", "/a"] or tag.startswith("a href=")


def test_build_safe_caption_length_limit():
    super_long_title = "🔥 Best compilation ever! " * 100  # ~2600 chars
    caption = build_safe_caption(
        title=super_long_title,
        url="https://example.com/long",
        platform_name="TikTok",
        uploader="Very Long Uploader Name" * 10
    )
    assert len(caption) <= 1024
