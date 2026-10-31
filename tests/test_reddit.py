from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.downloader import (
    MediaType,
    Platform,
    downloader_service,
)
from bot.services.gallery_dl_utils import GalleryDlResult
from bot.services.reddit import (
    RedditData,
    RedditItem,
    is_reddit_url,
    reddit_service,
    resolve_reddit_url,
)


def test_is_reddit_url():
    assert is_reddit_url("https://www.reddit.com/r/memes/comments/123/funny/")
    assert is_reddit_url("https://old.reddit.com/r/python/comments/456/")
    assert is_reddit_url("https://redd.it/12345")
    assert is_reddit_url("https://v.redd.it/abc12345")
    assert is_reddit_url("https://i.redd.it/xyz98765.jpg")
    assert is_reddit_url("https://reddit.com:8080/r/test")
    assert not is_reddit_url("https://example.com/not_reddit")
    assert not is_reddit_url("https://youtube.com/watch?v=123")
    assert not is_reddit_url("https://attacker-reddit.com/post")
    assert not is_reddit_url("https://reddit.com.attacker.com/evil")
    assert not is_reddit_url("https://fakeredd.it/post")


@pytest.mark.asyncio
async def test_resolve_reddit_url_direct():
    url = "https://www.reddit.com/r/python/comments/123/code/"
    resolved = await resolve_reddit_url(url)
    assert resolved == url

    # v.redd.it and i.redd.it should not trigger any HTTP request
    assert await resolve_reddit_url("https://v.redd.it/abc12345") == "https://v.redd.it/abc12345"
    assert await resolve_reddit_url("https://i.redd.it/pic123.jpg") == "https://i.redd.it/pic123.jpg"


@pytest.mark.asyncio
async def test_resolve_reddit_url_short():
    short_url = "https://redd.it/abc123"
    target_url = "https://www.reddit.com/r/python/comments/abc123/test/"

    mock_resp = MagicMock()
    mock_resp.url = target_url

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        resolved = await resolve_reddit_url(short_url)
        assert resolved == target_url


@pytest.mark.asyncio
async def test_extract_data_single_photo():
    fake_meta = {
        "id": "post123",
        "title": "A beautiful sunset",
        "author": "photographer",
        "subreddit": "EarthPorn",
        "over_18": False,
        "spoiler": False,
        "is_self": False,
        "is_video": False,
        "url": "https://i.redd.it/sunset123.jpg",
    }
    fake_result = GalleryDlResult(
        metadata=fake_meta,
        media_urls=["https://i.redd.it/sunset123.jpg"],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_result)):
        data = await reddit_service.extract_data("https://www.reddit.com/r/EarthPorn/comments/post123/sunset/")
        assert data is not None
        assert data.post_id == "post123"
        assert data.title == "A beautiful sunset"
        assert data.uploader == "photographer"
        assert data.subreddit == "EarthPorn"
        assert data.media_type == "photo"
        assert len(data.items) == 1
        assert not data.over_18
        assert not data.is_spoiler
        assert not data.is_video


@pytest.mark.asyncio
async def test_extract_data_gallery():
    fake_meta = {
        "id": "gal123",
        "title": "My pet album",
        "author": "catlover",
        "subreddit": "aww",
        "over_18": False,
        "spoiler": False,
        "is_self": False,
        "is_video": False,
        "url": "https://www.reddit.com/gallery/gal123",
    }
    fake_result = GalleryDlResult(
        metadata=fake_meta,
        media_urls=[
            "https://i.redd.it/cat1.jpg",
            "https://i.redd.it/cat2.jpg",
            "https://i.redd.it/cat3.jpg",
        ],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_result)):
        data = await reddit_service.extract_data("https://www.reddit.com/gallery/gal123")
        assert data is not None
        assert data.media_type == "album"
        assert len(data.items) == 3
        assert data.items[0].url == "https://i.redd.it/cat1.jpg"
        assert data.items[1].url == "https://i.redd.it/cat2.jpg"


@pytest.mark.asyncio
async def test_extract_data_video_v_redd_it():
    fake_meta = {
        "id": "vid123",
        "title": "Incredible stunt",
        "author": "stuntman",
        "subreddit": "nextfuckinglevel",
        "over_18": False,
        "spoiler": False,
        "is_self": False,
        "is_video": True,
        "url": "https://v.redd.it/vid123",
    }
    fake_result = GalleryDlResult(
        metadata=fake_meta,
        media_urls=["ytdl:https://v.redd.it/vid123"],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_result)):
        data = await reddit_service.extract_data("https://www.reddit.com/r/nextfuckinglevel/comments/vid123/stunt/")
        assert data is not None
        assert data.is_video is True
        assert data.media_type == "video"


@pytest.mark.asyncio
async def test_extract_data_redgifs_external():
    fake_meta = {
        "id": "nsfw123",
        "title": "Short loop",
        "author": "creator",
        "subreddit": "gifs",
        "over_18": True,
        "spoiler": False,
        "is_self": False,
        "is_video": False,
        "url": "https://www.redgifs.com/watch/popularclip",
    }
    fake_result = GalleryDlResult(
        metadata=fake_meta,
        media_urls=["https://www.redgifs.com/watch/popularclip"],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_result)):
        data = await reddit_service.extract_data("https://www.reddit.com/r/gifs/comments/nsfw123/short_loop/")
        assert data is not None
        assert data.over_18 is True
        assert data.external_url == "https://www.redgifs.com/watch/popularclip"
        assert data.items == []


@pytest.mark.asyncio
async def test_extract_data_text_self_post():
    fake_meta = {
        "id": "text123",
        "title": "What is your favorite book?",
        "author": "reader",
        "subreddit": "AskReddit",
        "over_18": False,
        "spoiler": False,
        "is_self": True,
        "is_video": False,
        "url": "https://www.reddit.com/r/AskReddit/comments/text123/book/",
    }
    fake_result = GalleryDlResult(
        metadata=fake_meta,
        media_urls=[],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_result)):
        data = await reddit_service.extract_data("https://www.reddit.com/r/AskReddit/comments/text123/book/")
        assert data is not None
        assert data.is_self is True
        assert data.error_message == "NO_MEDIA_REDDIT"


@pytest.mark.asyncio
async def test_extract_data_link_post_no_media():
    fake_meta = {
        "id": "link123",
        "title": "Interesting Article on Science",
        "author": "science_guy",
        "subreddit": "science",
        "over_18": False,
        "spoiler": False,
        "is_self": False,
        "is_video": False,
        "url": "https://www.nature.com/articles/sample-paper",
    }
    fake_result = GalleryDlResult(
        metadata=fake_meta,
        media_urls=[],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_result)):
        data = await reddit_service.extract_data("https://www.reddit.com/r/science/comments/link123/interesting_article/")
        assert data is not None
        assert data.is_self is False
        assert data.error_message == "NO_MEDIA_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_get_info_reddit_gallery():
    fake_data = RedditData(
        post_id="gal123",
        url="https://www.reddit.com/gallery/gal123",
        title="Gallery Title",
        uploader="u_author",
        subreddit="pics",
        media_type="album",
        items=[
            RedditItem(url="https://i.redd.it/pic1.jpg"),
            RedditItem(url="https://i.redd.it/pic2.jpg"),
        ],
        over_18=True,
    )

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_data)):
        info = await downloader_service.get_info("https://www.reddit.com/gallery/gal123")
        assert info is not None
        assert info.platform == Platform.REDDIT
        assert info.title == "Gallery Title"
        assert info.uploader == "r/pics • u/u_author"
        assert info.media_type == MediaType.ALBUM
        assert info.has_spoiler is True


@pytest.mark.asyncio
async def test_downloader_service_get_info_reddit_text_only():
    fake_data = RedditData(
        post_id="text123",
        url="https://www.reddit.com/r/AskReddit/comments/text123/",
        title="Discussion",
        uploader="asker",
        subreddit="AskReddit",
        media_type="photo",
        is_self=True,
        error_message="NO_MEDIA_REDDIT",
    )

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_data)):
        info = await downloader_service.get_info("https://www.reddit.com/r/AskReddit/comments/text123/")
        assert info is not None
        assert info.error_message == "NO_MEDIA_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_download_media_reddit_album(tmp_path: Path):
    fake_file1 = tmp_path / "item_1.jpg"
    fake_file2 = tmp_path / "item_2.jpg"
    fake_file1.write_bytes(b"image 1")
    fake_file2.write_bytes(b"image 2")

    fake_data = RedditData(
        post_id="gal123",
        url="https://www.reddit.com/gallery/gal123",
        title="Cat Album",
        uploader="catlover",
        subreddit="cats",
        media_type="album",
        items=[
            RedditItem(url="https://i.redd.it/cat1.jpg"),
            RedditItem(url="https://i.redd.it/cat2.jpg"),
        ],
        is_spoiler=True,
    )

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_data)), \
         patch("bot.services.reddit.reddit_service.download_media", AsyncMock(return_value=([fake_file1, fake_file2], "album"))):
        media_info = await downloader_service.download_media("https://www.reddit.com/gallery/gal123")
        assert media_info is not None
        assert media_info.platform == Platform.REDDIT
        assert media_info.media_type == MediaType.ALBUM
        assert media_info.has_spoiler is True
        assert len(media_info.file_paths) == 2
        assert media_info.file_size == len(b"image 1") + len(b"image 2")


@pytest.mark.asyncio
async def test_downloader_service_download_media_reddit_video(tmp_path: Path):
    fake_video = tmp_path / "dummytas_video.mp4"
    fake_video.write_bytes(b"video bytes")

    fake_data = RedditData(
        post_id="vid123",
        url="https://www.reddit.com/r/funny/comments/vid123/",
        title="Funny video",
        uploader="comedian",
        subreddit="funny",
        media_type="video",
        is_video=True,
        over_18=True,
    )

    fake_ydl_info = {
        "title": "Funny video",
        "duration": 15,
        "uploader": "comedian",
        "age_limit": 18,
    }

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_data)), \
         patch.object(downloader_service, "download_dir", tmp_path), \
         patch("bot.services.downloader.uuid.uuid4", return_value="dummytask123"), \
         patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        media_info = await downloader_service.download_media("https://www.reddit.com/r/funny/comments/vid123/")
        assert media_info is not None
        assert media_info.platform == Platform.REDDIT
        assert media_info.media_type == MediaType.VIDEO
        assert media_info.has_spoiler is True
        assert media_info.uploader == "r/funny • u/comedian"
