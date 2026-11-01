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
    normalize_reddit_url,
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


def test_normalize_reddit_url():
    assert (
        normalize_reddit_url("https://www.reddit.com/r/python/comments/123/my_code/")
        == "https://www.reddit.com/r/python/comments/123/"
    )
    assert (
        normalize_reddit_url(
            "https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/%25D0%25BE%25D0%25BA_%25D0%25BA%25D0%25BB%25D0%25BE%25D0%25B4/"
        )
        == "https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/"
    )
    assert (
        normalize_reddit_url("https://www.reddit.com/r/python/comments/123/")
        == "https://www.reddit.com/r/python/comments/123/"
    )
    assert (
        normalize_reddit_url("https://www.reddit.com/comments/123/title/")
        == "https://www.reddit.com/comments/123/"
    )
    assert (
        normalize_reddit_url("https://www.reddit.com/r/sub/s/share123")
        == "https://www.reddit.com/r/sub/s/share123"
    )
    assert normalize_reddit_url("https://redd.it/123") == "https://redd.it/123"


@pytest.mark.asyncio
async def test_resolve_reddit_url_direct():
    url = "https://www.reddit.com/r/python/comments/123/code/"
    resolved = await resolve_reddit_url(url)
    assert resolved == "https://www.reddit.com/r/python/comments/123/"

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
        assert resolved == "https://www.reddit.com/r/python/comments/abc123/"


@pytest.mark.asyncio
async def test_resolve_reddit_url_301_location():
    share_url = "https://www.reddit.com/r/mybabessin/s/mEDaAAdHj4"
    target_with_params = "https://www.reddit.com/r/mybabessin/comments/1ur3fmw/teens_love/?share_id=qVQKqn4goPHUw8v0nNhLq&utm_content=1"
    clean_target = "https://www.reddit.com/r/mybabessin/comments/1ur3fmw/"

    mock_resp = MagicMock()
    mock_resp.status = 301
    mock_resp.headers = {"Location": target_with_params}

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        resolved = await resolve_reddit_url(share_url)
        assert resolved == clean_target


@pytest.mark.asyncio
async def test_resolve_reddit_url_403_blocked():
    share_url = "https://www.reddit.com/r/sub/s/blocked123"

    mock_resp = MagicMock()
    mock_resp.status = 403

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        resolved = await resolve_reddit_url(share_url)
        assert resolved == share_url


@pytest.mark.asyncio
async def test_extract_data_auth_required():
    fake_result = GalleryDlResult(
        auth_required=True,
        error_message="AUTH_REQUIRED",
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_result)):
        data = await reddit_service.extract_data("https://www.reddit.com/r/test/comments/123/post/")
        assert data is not None
        assert data.error_message == "AUTH_REQUIRED_REDDIT"



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


@pytest.mark.asyncio
async def test_downloader_service_reddit_auth_required_ytdlp():
    url = "https://www.reddit.com/r/funny/comments/123/post/"
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("ERROR: [Reddit] 123: Unable to download webpage: HTTP Error 403: Blocked"))):
        info = await downloader_service.get_info(url)
        assert info is not None
        assert info.error_message == "AUTH_REQUIRED_REDDIT"

        media = await downloader_service.download_media(url)
        assert media is not None
        assert media.error_message == "AUTH_REQUIRED_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_reddit_fallback_ytdlp_success(tmp_path: Path):
    fake_auth_data = RedditData(
        post_id="",
        url="https://www.reddit.com/r/funny/comments/vid123/",
        title="Требуется авторизация",
        uploader="Reddit",
        subreddit="",
        media_type="photo",
        error_message="AUTH_REQUIRED_REDDIT",
    )
    fake_video = tmp_path / "dummytas_video.mp4"
    fake_video.write_bytes(b"video bytes")
    fake_ydl_info = {
        "title": "Funny video",
        "duration": 15,
        "uploader": "comedian",
        "age_limit": 18,
    }

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_auth_data)), \
         patch.object(downloader_service, "download_dir", tmp_path), \
         patch("bot.services.downloader.uuid.uuid4", return_value="dummytask123"), \
         patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        media_info = await downloader_service.download_media("https://www.reddit.com/r/funny/comments/vid123/")
        assert media_info is not None
        assert media_info.platform == Platform.REDDIT
        assert media_info.media_type == MediaType.VIDEO
        assert media_info.title == "Funny video"
        assert media_info.url == "https://www.reddit.com/r/funny/comments/vid123/"


@pytest.mark.asyncio
async def test_downloader_service_reddit_no_media_ytdlp():
    fake_auth_data = RedditData(
        post_id="",
        url="https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/",
        title="Требуется авторизация",
        uploader="Reddit",
        subreddit="",
        media_type="photo",
        error_message="AUTH_REQUIRED_REDDIT",
    )
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_auth_data)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("ERROR: [Reddit] 1xkm6cw: No media found"))):
        media = await downloader_service.download_media("https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/")
        assert media is not None
        assert media.error_message == "NO_MEDIA_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_reddit_no_media_getinfo_ytdlp():
    fake_auth_data = RedditData(
        post_id="",
        url="https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/",
        title="Требуется авторизация",
        uploader="Reddit",
        subreddit="",
        media_type="photo",
        error_message="AUTH_REQUIRED_REDDIT",
    )
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_auth_data)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("ERROR: [Reddit] 1xkm6cw: No media found"))):
        info = await downloader_service.get_info("https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/")
        assert info is not None
        assert info.error_message == "NO_MEDIA_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_reddit_getinfo_auth_required_fallback():
    fake_auth_data = RedditData(
        post_id="",
        url="https://www.reddit.com/r/funny/comments/vid123/",
        title="Требуется авторизация",
        uploader="Reddit",
        subreddit="",
        media_type="photo",
        error_message="AUTH_REQUIRED_REDDIT",
    )
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=fake_auth_data)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("Some general network error"))):
        info = await downloader_service.get_info("https://www.reddit.com/r/funny/comments/vid123/")
        assert info is not None
        assert info.error_message == "AUTH_REQUIRED_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_reddit_shortlink_returns_canonical_url(tmp_path: Path):
    fake_video = tmp_path / "dummytas_video.mp4"
    fake_video.write_bytes(b"video content")
    fake_ydl_info = {
        "title": "Shortlink Video",
        "duration": 10,
        "uploader": "testuser",
        "age_limit": 0,
    }

    with patch("bot.services.downloader.resolve_reddit_url", AsyncMock(return_value="https://www.reddit.com/r/funny/comments/canonical123/")), \
         patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch.object(downloader_service, "download_dir", tmp_path), \
         patch("bot.services.downloader.uuid.uuid4", return_value="dummytask123"), \
         patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        media = await downloader_service.download_media("https://www.reddit.com/r/funny/s/shortlink123")
        assert media is not None
        assert media.url == "https://www.reddit.com/r/funny/comments/canonical123/"


@pytest.mark.asyncio
async def test_extract_from_html_direct_image():
    html_content = """
    <html>
    <head><title>ок клод : r/RuProgrammers</title></head>
    <body>
    <shreddit-post
        post-title="ок клод"
        author="A_G_96"
        subreddit-prefixed-name="r/RuProgrammers"
        post-type="image"
        content-href="https://i.redd.it/3dbywsi9hvyh1.png"
        id="t3_1xkm6cw">
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/"
    mock_resp.text = AsyncMock(return_value=html_content)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/")
        assert data is not None
        assert data.post_id == "1xkm6cw"
        assert data.title == "ок клод"
        assert data.uploader == "A_G_96"
        assert data.subreddit == "RuProgrammers"
        assert data.media_type == "photo"
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/3dbywsi9hvyh1.png"
        assert data.error_message is None


@pytest.mark.asyncio
async def test_extract_from_html_gallery():
    html_content = """
    <html>
    <body>
    <shreddit-post
        post-title="Photo Gallery"
        author="nature_fan"
        subreddit-prefixed-name="r/nature"
        post-type="gallery"
        id="t3_gal999">
        <gallery-carousel>
            <figure><img src="https://preview.redd.it/img1.jpg?width=640" /></figure>
            <figure><img src="https://i.redd.it/img2.png" /></figure>
        </gallery-carousel>
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://www.reddit.com/r/nature/comments/gal999/"
    mock_resp.text = AsyncMock(return_value=html_content)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/nature/comments/gal999/")
        assert data is not None
        assert data.media_type == "album"
        assert len(data.items) == 2
        assert data.items[0].url == "https://i.redd.it/img1.jpg"
        assert data.items[1].url == "https://i.redd.it/img2.png"


@pytest.mark.asyncio
async def test_extract_from_html_video_shreddit_player():
    html_content = """
    <html>
    <body>
    <shreddit-post
        post-title="Epic Video"
        author="gamer"
        subreddit-prefixed-name="r/gaming"
        post-type="video"
        content-href="https://v.redd.it/vid123"
        id="t3_vid123">
        <shreddit-player src="https://v.redd.it/vid123/DASH_720.mp4" stream-url="https://v.redd.it/vid123/HLS.m3u8"></shreddit-player>
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://www.reddit.com/r/gaming/comments/vid123/"
    mock_resp.text = AsyncMock(return_value=html_content)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/gaming/comments/vid123/")
        assert data is not None
        assert data.is_video is True
        assert data.media_type == "video"
        assert data.video_url == "https://v.redd.it/vid123/DASH_720.mp4"
        assert len(data.items) == 1
        assert data.items[0].url == "https://v.redd.it/vid123/DASH_720.mp4"


@pytest.mark.asyncio
async def test_extract_from_html_packaged_media_json():
    html_content = """
    <html>
    <body>
    <shreddit-post
        post-title="Packaged Video"
        author="user_pkg"
        subreddit-prefixed-name="r/videos"
        post-type="video"
        content-href="https://v.redd.it/pkg123"
        id="t3_pkg123">
        <shreddit-player packaged-media-json="{&quot;playbackMp4Url&quot;: &quot;https://v.redd.it/pkg123/video.mp4&quot;}"></shreddit-player>
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://www.reddit.com/r/videos/comments/pkg123/"
    mock_resp.text = AsyncMock(return_value=html_content)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/videos/comments/pkg123/")
        assert data is not None
        assert data.is_video is True
        assert data.video_url == "https://v.redd.it/pkg123/video.mp4"


@pytest.mark.asyncio
async def test_extract_from_html_redgifs():
    html_content = """
    <html>
    <body>
    <shreddit-post
        post-title="RedGifs Post"
        author="nsfw_artist"
        subreddit-prefixed-name="r/art"
        content-href="https://www.redgifs.com/watch/cutepuppy"
        id="t3_rg123">
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://www.reddit.com/r/art/comments/rg123/"
    mock_resp.text = AsyncMock(return_value=html_content)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/art/comments/rg123/")
        assert data is not None
        assert data.external_url == "https://www.redgifs.com/watch/cutepuppy"
        assert data.media_type == "video"


@pytest.mark.asyncio
async def test_extract_from_html_text_self_post():
    html_content = """
    <html>
    <body>
    <shreddit-post
        post-title="What are your thoughts?"
        author="thinker"
        subreddit-prefixed-name="r/AskReddit"
        post-type="text"
        is-self="true"
        id="t3_ask123">
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://www.reddit.com/r/AskReddit/comments/ask123/"
    mock_resp.text = AsyncMock(return_value=html_content)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/AskReddit/comments/ask123/")
        assert data is not None
        assert data.is_self is True
        assert data.error_message == "NO_MEDIA_REDDIT"


@pytest.mark.asyncio
async def test_extract_from_html_js_challenge():
    challenge_html = """
    <html>
    <head>
        <script>
            const res = await (async e=>e+e)("abcXYZ");
        </script>
    </head>
    <body>
        <form action="/?js_challenge=1" method="POST">
            <input type="hidden" name="jsc_token" value="" />
        </form>
    </body>
    </html>
    """
    solved_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Solved Post"
        author="challenger"
        subreddit-prefixed-name="r/test"
        post-type="image"
        content-href="https://i.redd.it/solved123.jpg"
        id="t3_solved">
    </shreddit-post>
    </body>
    </html>
    """

    mock_resp_1 = MagicMock()
    mock_resp_1.url = "https://www.reddit.com/r/test/comments/solved/?js_challenge=1"
    mock_resp_1.text = AsyncMock(return_value=challenge_html)

    mock_cm_1 = MagicMock()
    mock_cm_1.__aenter__ = AsyncMock(return_value=mock_resp_1)
    mock_cm_1.__aexit__ = AsyncMock(return_value=None)

    mock_resp_post = MagicMock()
    mock_resp_post.text = AsyncMock(return_value=solved_html)

    mock_cm_post = MagicMock()
    mock_cm_post.__aenter__ = AsyncMock(return_value=mock_resp_post)
    mock_cm_post.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm_1
    mock_session.post.return_value = mock_cm_post

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/test/comments/solved/")
        assert data is not None
        assert data.title == "Solved Post"
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/solved123.jpg"

        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args[1]
        assert call_kwargs["data"]["jsc_token"] == "abcXYZabcXYZ"
        assert "Referer" in call_kwargs["headers"]


@pytest.mark.asyncio
async def test_extract_from_html_redirect_div():
    redirect_html = """
    <html>
    <body>
        <div id="shreddit-redirect" data-url="https://www.reddit.com/r/destination/comments/target123/"></div>
    </body>
    </html>
    """
    final_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Destination Post"
        author="dest_user"
        subreddit-prefixed-name="r/destination"
        post-type="image"
        content-href="https://i.redd.it/target.png"
        id="t3_target123">
    </shreddit-post>
    </body>
    </html>
    """

    mock_resp_1 = MagicMock()
    mock_resp_1.url = "https://www.reddit.com/r/source/comments/source123/"
    mock_resp_1.text = AsyncMock(return_value=redirect_html)

    mock_resp_2 = MagicMock()
    mock_resp_2.url = "https://www.reddit.com/r/destination/comments/target123/"
    mock_resp_2.text = AsyncMock(return_value=final_html)

    mock_session = MagicMock()
    mock_cm_1 = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_1), __aexit__=AsyncMock(return_value=None))
    mock_cm_2 = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_2), __aexit__=AsyncMock(return_value=None))
    mock_session.get.side_effect = [mock_cm_1, mock_cm_2]

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/source/comments/source123/")
        assert data is not None
        assert data.title == "Destination Post"
        assert data.subreddit == "destination"
        assert data.items[0].url == "https://i.redd.it/target.png"


@pytest.mark.asyncio
async def test_extract_from_html_redirect_canonical_updater():
    canonical_html = """
    <html>
    <body>
        <canonical-url-updater canonical-url="https://www.reddit.com/r/canonical/comments/can123/"></canonical-url-updater>
    </body>
    </html>
    """
    final_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Canonical Post"
        author="can_user"
        subreddit-prefixed-name="r/canonical"
        post-type="image"
        content-href="https://i.redd.it/can.png"
        id="t3_can123">
    </shreddit-post>
    </body>
    </html>
    """

    mock_resp_1 = MagicMock()
    mock_resp_1.url = "https://www.reddit.com/comments/can123/"
    mock_resp_1.text = AsyncMock(return_value=canonical_html)

    mock_resp_2 = MagicMock()
    mock_resp_2.url = "https://www.reddit.com/r/canonical/comments/can123/"
    mock_resp_2.text = AsyncMock(return_value=final_html)

    mock_session = MagicMock()
    mock_cm_1 = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_1), __aexit__=AsyncMock(return_value=None))
    mock_cm_2 = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_2), __aexit__=AsyncMock(return_value=None))
    mock_session.get.side_effect = [mock_cm_1, mock_cm_2]

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/comments/can123/")
        assert data is not None
        assert data.title == "Canonical Post"
        assert data.subreddit == "canonical"


@pytest.mark.asyncio
async def test_extract_from_html_blocked_network_security():
    blocked_html = """
    <html>
    <body>
        <div class="font-bold text-24 text-neutral-content-strong">You've been blocked by network security.</div>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://www.reddit.com/r/test/comments/blocked/"
    mock_resp.text = AsyncMock(return_value=blocked_html)

    mock_session = MagicMock()
    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/test/comments/blocked/")
        assert data is not None
        assert data.error_message == "IP_BLOCKED_REDDIT"


@pytest.mark.asyncio
async def test_extract_data_gallery_dl_403_falls_back_to_html():
    fake_gallery_dl_result = GalleryDlResult(
        auth_required=True,
        error_message="HTTP Error 403: Forbidden - You've been blocked by network security",
    )
    fake_html_data = RedditData(
        post_id="1xkm6cw",
        url="https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/",
        title="ок клод",
        uploader="A_G_96",
        subreddit="RuProgrammers",
        media_type="photo",
        items=[RedditItem(url="https://i.redd.it/3dbywsi9hvyh1.png", media_type="photo")],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=fake_gallery_dl_result)), \
         patch.object(reddit_service, "extract_from_html", AsyncMock(return_value=fake_html_data)) as mock_html_fallback:
        data = await reddit_service.extract_data("https://www.reddit.com/r/RuProgrammers/comments/1xkm6cw/")
        mock_html_fallback.assert_called_once()
        assert data is not None
        assert data.title == "ок клод"
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/3dbywsi9hvyh1.png"


def test_settings_download_proxy_aliases():
    import os

    from bot.config import Settings

    os.environ["DOWNLOAD_PROXY"] = "http://user:pass@proxy1:8080"
    s1 = Settings()
    assert s1.download_proxy == "http://user:pass@proxy1:8080"
    os.environ.pop("DOWNLOAD_PROXY")

    os.environ["PROXY_URL"] = "http://proxy2:8080"
    s2 = Settings()
    assert s2.download_proxy == "http://proxy2:8080"
    os.environ.pop("PROXY_URL")


def test_base_ydl_opts_proxy():
    from bot.config import settings
    with patch.object(settings, "download_proxy", "http://proxy.test:8080"):
        opts = downloader_service._get_base_ydl_opts(Platform.REDDIT)
        assert opts.get("proxy") == "http://proxy.test:8080"


def test_gallery_dl_proxy_flag():
    from bot.config import settings
    from bot.services.gallery_dl_utils import run_gallery_dl_json_sync

    with patch.object(settings, "download_proxy", "http://proxy.test:8080"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        run_gallery_dl_json_sync("https://reddit.com/r/test", use_cookies=False)
        cmd = mock_run.call_args[0][0]
        assert "--proxy" in cmd
        assert "http://proxy.test:8080" in cmd


@pytest.mark.asyncio
async def test_downloader_service_reddit_ip_blocked_api():
    url = "https://www.reddit.com/r/funny/comments/blocked1/"
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("bot.services.reddit.reddit_service.extract_from_html", AsyncMock(return_value=None)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("ERROR: [Reddit] blocked1: Your IP address is unable to access the Reddit API"))):
        info = await downloader_service.get_info(url)
        assert info is not None
        assert info.error_message == "IP_BLOCKED_REDDIT"

        media = await downloader_service.download_media(url)
        assert media is not None
        assert media.error_message == "IP_BLOCKED_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_reddit_ip_blocked_network_security():
    url = "https://www.reddit.com/r/funny/comments/blocked2/"
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("bot.services.reddit.reddit_service.extract_from_html", AsyncMock(return_value=None)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("HTTP Error 403: Blocked by network security"))):
        info = await downloader_service.get_info(url)
        assert info is not None
        assert info.error_message == "IP_BLOCKED_REDDIT"

        media = await downloader_service.download_media(url)
        assert media is not None
        assert media.error_message == "IP_BLOCKED_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_reddit_ip_blocked_with_cookies(tmp_path: Path):
    from bot.config import settings
    fake_cookie = tmp_path / "cookies.txt"
    fake_cookie.write_text("reddit_cookie_data")
    url = "https://www.reddit.com/r/funny/comments/blocked3/"
    with patch.object(settings, "cookies_file", fake_cookie), \
         patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("bot.services.reddit.reddit_service.extract_from_html", AsyncMock(return_value=None)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("HTTP Error 403: Forbidden"))):
        info = await downloader_service.get_info(url)
        assert info is not None
        assert info.error_message == "IP_BLOCKED_REDDIT"

        media = await downloader_service.download_media(url)
        assert media is not None
        assert media.error_message == "IP_BLOCKED_REDDIT"


@pytest.mark.asyncio
async def test_gallery_dl_raw_error_preservation_and_fallback():
    from bot.services.gallery_dl_utils import run_gallery_dl_json_sync

    mock_res = MagicMock(
        returncode=1,
        stderr="[reddit][error] HTTP Error 403: Forbidden - You've been blocked by network security",
        stdout="",
    )
    with patch("subprocess.run", return_value=mock_res):
        res = run_gallery_dl_json_sync("https://reddit.com/r/test")
        assert res.auth_required is True
        assert res.error_message == "AUTH_REQUIRED"
        assert res.raw_error == "[reddit][error] HTTP Error 403: Forbidden - You've been blocked by network security"

    fake_html_data = RedditData(
        post_id="post403",
        url="https://www.reddit.com/r/test/comments/post403/",
        title="Unblocked Scraped Post",
        uploader="rescuer",
        subreddit="test",
        media_type="photo",
        items=[RedditItem(url="https://i.redd.it/rescued.png", media_type="photo")],
    )

    with patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=res)), \
         patch.object(reddit_service, "extract_from_html", AsyncMock(return_value=fake_html_data)) as mock_html:
        data = await reddit_service.extract_data("https://www.reddit.com/r/test/comments/post403/")
        mock_html.assert_called_once()
        assert data is not None
        assert data.title == "Unblocked Scraped Post"
        assert data.items[0].url == "https://i.redd.it/rescued.png"


@pytest.mark.asyncio
async def test_extract_from_html_does_not_leak_sidebar_recommendations():
    html_with_recommendations = """
    <html>
    <body>
    <shreddit-post
        post-title="Main Image Only"
        author="artist"
        subreddit-prefixed-name="r/art"
        post-type="image"
        content-href="https://i.redd.it/main_artwork.jpg"
        id="t3_main123">
    </shreddit-post>
    <div class="sidebar-recommendations">
        <h3>More posts you may like</h3>
        <a href="/r/art/comments/rec1/"><img src="https://preview.redd.it/rec_art1.jpg?width=640" /></a>
        <a href="/r/art/comments/rec2/"><img src="https://preview.redd.it/rec_art2.png" /></a>
    </div>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.url = "https://www.reddit.com/r/art/comments/main123/"
    mock_resp.text = AsyncMock(return_value=html_with_recommendations)

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/art/comments/main123/")
        assert data is not None
        assert data.media_type == "photo"
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/main_artwork.jpg"


@pytest.mark.asyncio
async def test_extract_from_html_text_post_not_polluted_by_recommendations():
    text_post_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Deep Discussion"
        author="philosopher"
        subreddit-prefixed-name="r/AskReddit"
        post-type="text"
        is-self="true"
        id="t3_disc123">
    </shreddit-post>
    <div class="recommended-stream">
        <img src="https://preview.redd.it/side1.jpg" />
        <img src="https://preview.redd.it/side2.jpg" />
    </div>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.url = "https://www.reddit.com/r/AskReddit/comments/disc123/"
    mock_resp.text = AsyncMock(return_value=text_post_html)

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/AskReddit/comments/disc123/")
        assert data is not None
        assert data.is_self is True
        assert data.error_message == "NO_MEDIA_REDDIT"
        assert data.items == []


@pytest.mark.asyncio
async def test_extract_from_html_video_with_image_content_href():
    video_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Video with Poster"
        author="videomaker"
        subreddit-prefixed-name="r/videos"
        post-type="video"
        content-href="https://i.redd.it/poster.jpg"
        id="t3_vid999">
        <shreddit-player src="https://v.redd.it/vid999/video.mp4"></shreddit-player>
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.url = "https://www.reddit.com/r/videos/comments/vid999/"
    mock_resp.text = AsyncMock(return_value=video_html)

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/videos/comments/vid999/")
        assert data is not None
        assert data.is_video is True
        assert data.media_type == "video"
        assert data.video_url == "https://v.redd.it/vid999/video.mp4"
        assert len(data.items) == 1
        assert data.items[0].url == "https://v.redd.it/vid999/video.mp4"
        assert data.items[0].media_type == "video"


@pytest.mark.asyncio
async def test_extract_from_html_status_403_fastly():
    plain_403_html = """
    <html>
    <head><title>403 Forbidden</title></head>
    <body><h1>403 Forbidden</h1></body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status = 403
    mock_resp.url = "https://www.reddit.com/r/test/comments/fastly403/"
    mock_resp.text = AsyncMock(return_value=plain_403_html)

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/test/comments/fastly403/")
        assert data is not None
        assert data.error_message == "IP_BLOCKED_REDDIT"


@pytest.mark.asyncio
async def test_extract_from_html_js_challenge_arrow_parens():
    challenge_html = """
    <html>
    <head>
        <script>
            const token = await (async (e) => (e + e))("paramToken");
        </script>
    </head>
    <body>
        <form action="/submit?js_challenge=1" method="POST">
            <input type="hidden" name="jsc_token" value="" />
        </form>
    </body>
    </html>
    """
    solved_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Solved Arrow Post"
        author="arrow_solver"
        subreddit-prefixed-name="r/dev"
        post-type="image"
        content-href="https://i.redd.it/arrow_res.png"
        id="t3_arrow">
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp_1 = MagicMock()
    mock_resp_1.status = 200
    mock_resp_1.url = "https://www.reddit.com/r/dev/comments/arrow/?js_challenge=1"
    mock_resp_1.text = AsyncMock(return_value=challenge_html)

    mock_resp_post = MagicMock()
    mock_resp_post.status = 200
    mock_resp_post.text = AsyncMock(return_value=solved_html)

    mock_cm_1 = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_1), __aexit__=AsyncMock(return_value=None))
    mock_cm_post = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_post), __aexit__=AsyncMock(return_value=None))

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm_1
    mock_session.post.return_value = mock_cm_post

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/dev/comments/arrow/")
        assert data is not None
        assert data.title == "Solved Arrow Post"
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/arrow_res.png"
        mock_session.post.assert_called_once()
        assert mock_session.post.call_args[1]["data"]["jsc_token"] == "paramTokenparamToken"


@pytest.mark.asyncio
async def test_downloader_service_download_media_html_fallback_ip_blocked():
    url = "https://www.reddit.com/r/test/comments/blocked_dl/"
    blocked_html_data = RedditData(
        post_id="",
        url=url,
        title="IP заблокирован Reddit",
        uploader="Reddit",
        subreddit="",
        media_type="photo",
        error_message="IP_BLOCKED_REDDIT",
    )
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("bot.services.reddit.reddit_service.extract_from_html", AsyncMock(return_value=blocked_html_data)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("HTTP Error 403: Forbidden"))):
        media = await downloader_service.download_media(url)
        assert media is not None
        assert media.error_message == "IP_BLOCKED_REDDIT"


@pytest.mark.asyncio
async def test_downloader_service_prefers_video_url_for_ytdlp(tmp_path: Path):
    fake_video = tmp_path / "vid_task_video.mp4"
    fake_video.write_bytes(b"stream bytes")
    fake_ydl_info = {
        "title": "Streamed Reddit Video",
        "duration": 20,
        "uploader": "streamer",
        "age_limit": 0,
    }

    video_data = RedditData(
        post_id="vidstream",
        url="https://www.reddit.com/r/videos/comments/vidstream/",
        title="Streamed Reddit Video",
        uploader="streamer",
        subreddit="videos",
        media_type="video",
        is_video=True,
        video_url="https://v.redd.it/stream123",
    )

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=video_data)), \
         patch.object(downloader_service, "download_dir", tmp_path), \
         patch("bot.services.downloader.uuid.uuid4", return_value="vid_task"), \
         patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)) as mock_ytdl:
        media = await downloader_service.download_media("https://www.reddit.com/r/videos/comments/vidstream/")
        assert media is not None
        assert media.media_type == MediaType.VIDEO
        # Verify that yt-dlp was invoked on the direct video_url to bypass .json 403 block
        mock_ytdl.assert_called_once()


@pytest.mark.asyncio
async def test_extract_data_gallery_dl_403_returns_no_media_reddit():
    url = "https://www.reddit.com/r/AskReddit/comments/text403/"
    gallery_res = GalleryDlResult(
        auth_required=True,
        error_message="AUTH_REQUIRED",
        raw_error="[reddit][error] HTTP Error 403: Forbidden - You've been blocked by network security",
    )
    no_media_html_data = RedditData(
        post_id="text403",
        url=url,
        title="Discussion Post",
        uploader="asker",
        subreddit="AskReddit",
        media_type="photo",
        is_self=True,
        error_message="NO_MEDIA_REDDIT",
    )
    with patch("bot.services.reddit.resolve_reddit_url", AsyncMock(return_value=url)), \
         patch("bot.services.reddit.run_gallery_dl_json", AsyncMock(return_value=gallery_res)), \
         patch.object(reddit_service, "extract_from_html", AsyncMock(return_value=no_media_html_data)):
        data = await reddit_service.extract_data(url)
        assert data is not None
        assert data.error_message == "NO_MEDIA_REDDIT"
        assert data.is_self is True
        assert data.title == "Discussion Post"


@pytest.mark.asyncio
async def test_downloader_service_get_info_html_fallback_no_media():
    url = "https://www.reddit.com/r/AskReddit/comments/ask_no_media/"
    no_media_html_data = RedditData(
        post_id="ask_no_media",
        url=url,
        title="Ask Discussion Title",
        uploader="asker_user",
        subreddit="AskReddit",
        media_type="photo",
        is_self=True,
        error_message="NO_MEDIA_REDDIT",
    )
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("bot.services.reddit.reddit_service.extract_from_html", AsyncMock(return_value=no_media_html_data)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("HTTP Error 403: Forbidden"))):
        info = await downloader_service.get_info(url)
        assert info is not None
        assert info.error_message == "NO_MEDIA_REDDIT"
        assert info.title == "Ask Discussion Title"


@pytest.mark.asyncio
async def test_downloader_service_download_media_html_fallback_no_media():
    url = "https://www.reddit.com/r/AskReddit/comments/dl_no_media/"
    no_media_html_data = RedditData(
        post_id="dl_no_media",
        url=url,
        title="Text Only Discussion",
        uploader="author1",
        subreddit="AskReddit",
        media_type="photo",
        is_self=True,
        error_message="NO_MEDIA_REDDIT",
    )
    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("bot.services.reddit.reddit_service.extract_from_html", AsyncMock(return_value=no_media_html_data)), \
         patch("asyncio.to_thread", AsyncMock(side_effect=Exception("HTTP Error 403: Forbidden"))):
        media = await downloader_service.download_media(url)
        assert media is not None
        assert media.error_message == "NO_MEDIA_REDDIT"
        assert media.title == "Text Only Discussion"


@pytest.mark.asyncio
async def test_downloader_service_preserves_reddit_title_for_ytdlp_video(tmp_path: Path):
    fake_video = tmp_path / "vid_task_video.mp4"
    fake_video.write_bytes(b"stream bytes")
    # yt-dlp returns generic stream title
    fake_ydl_info = {
        "title": "HLSPlaylist",
        "duration": 15,
        "uploader": "generic_uploader",
        "age_limit": 0,
    }

    video_data = RedditData(
        post_id="vid_title",
        url="https://www.reddit.com/r/cats/comments/vid_title/",
        title="Hilarious Cat Jumping",
        uploader="catlover",
        subreddit="cats",
        media_type="video",
        is_video=True,
        video_url="https://v.redd.it/catjump123/HLSPlaylist.m3u8",
    )

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=video_data)), \
         patch.object(downloader_service, "download_dir", tmp_path), \
         patch("bot.services.downloader.uuid.uuid4", return_value="vid_task"), \
         patch("asyncio.to_thread", AsyncMock(return_value=fake_ydl_info)):
        media = await downloader_service.download_media("https://www.reddit.com/r/cats/comments/vid_title/")
        assert media is not None
        assert media.media_type == MediaType.VIDEO
        # Verify title is preserved from reddit_data rather than generic 'HLSPlaylist'
        assert media.title == "Hilarious Cat Jumping"
        assert media.uploader == "r/cats • u/catlover"


@pytest.mark.asyncio
async def test_extract_from_html_js_challenge_block_body_arrow():
    challenge_html = """
    <html>
    <head>
        <script>
            const token = await (async (e) => { return e + e; })("blockToken123");
        </script>
    </head>
    <body>
        <form action="/submit?js_challenge=1" method="POST">
            <input type="hidden" name="jsc_token" value="" />
        </form>
    </body>
    </html>
    """
    solved_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Solved Block Body"
        author="block_solver"
        subreddit-prefixed-name="r/coding"
        post-type="image"
        content-href="https://i.redd.it/solved_block.png"
        id="t3_block">
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp_1 = MagicMock()
    mock_resp_1.status = 200
    mock_resp_1.url = "https://www.reddit.com/r/coding/comments/block/?js_challenge=1"
    mock_resp_1.text = AsyncMock(return_value=challenge_html)

    mock_resp_post = MagicMock()
    mock_resp_post.status = 200
    mock_resp_post.text = AsyncMock(return_value=solved_html)

    mock_cm_1 = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_1), __aexit__=AsyncMock(return_value=None))
    mock_cm_post = MagicMock(__aenter__=AsyncMock(return_value=mock_resp_post), __aexit__=AsyncMock(return_value=None))

    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm_1
    mock_session.post.return_value = mock_cm_post

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/coding/comments/block/")
        assert data is not None
        assert data.title == "Solved Block Body"
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/solved_block.png"
        mock_session.post.assert_called_once()
        assert mock_session.post.call_args[1]["data"]["jsc_token"] == "blockToken123blockToken123"


@pytest.mark.asyncio
async def test_extract_from_html_preview_redd_it_content_href_cleaned():
    html_preview = """
    <html>
    <body>
    <shreddit-post
        post-title="High Res Image"
        author="photographer"
        subreddit-prefixed-name="r/pics"
        post-type="image"
        content-href="https://preview.redd.it/sample_pic.jpg?width=1080&crop=smart&auto=webp&s=token123"
        id="t3_pic1">
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.url = "https://www.reddit.com/r/pics/comments/pic1/"
    mock_resp.text = AsyncMock(return_value=html_preview)

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/pics/comments/pic1/")
        assert data is not None
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/sample_pic.jpg"


@pytest.mark.asyncio
async def test_extract_from_html_unclosed_shreddit_post_isolates_comments():
    html_unclosed = """
    <html>
    <body>
    <shreddit-post
        post-title="Main Post"
        author="op_user"
        subreddit-prefixed-name="r/AskReddit"
        post-type="text"
        is-self="true"
        id="t3_unclosed">
    <div>Some text body</div>
    <shreddit-comment-tree>
        <div class="comment">
            <img src="https://preview.redd.it/sidebar_leak.jpg" />
        </div>
    </shreddit-comment-tree>
    <div id="sidebar">
        <img src="https://preview.redd.it/recommendation_leak.jpg" />
    </div>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.url = "https://www.reddit.com/r/AskReddit/comments/unclosed/"
    mock_resp.text = AsyncMock(return_value=html_unclosed)

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/AskReddit/comments/unclosed/")
        assert data is not None
        assert data.error_message == "NO_MEDIA_REDDIT"
        assert data.items == []


@pytest.mark.asyncio
async def test_extract_html_redirect_shreddit_redirect_tag():
    html_custom_tag = """
    <html>
    <head><title>Redirecting</title></head>
    <body>
    <shreddit-redirect redirect-url="/r/ProgrammerHumor/comments/canon123/funny_joke/"></shreddit-redirect>
    </body>
    </html>
    """
    target = reddit_service._extract_html_redirect(html_custom_tag, "https://www.reddit.com/s/shortlink")
    assert target == "https://www.reddit.com/r/ProgrammerHumor/comments/canon123/funny_joke/"

    html_unquoted_div = """
    <html>
    <body>
    <div id=shreddit-redirect data-url="https://www.reddit.com/r/test/comments/div123/"></div>
    </body>
    </html>
    """
    target2 = reddit_service._extract_html_redirect(html_unquoted_div, "https://www.reddit.com/s/xyz")
    assert target2 == "https://www.reddit.com/r/test/comments/div123/"


@pytest.mark.asyncio
async def test_extract_from_html_redirect_after_fastly_403_challenge():
    challenge_html = """
    <html>
    <head>
        <script>
            const token = await (async e=>e+e)("redirToken");
        </script>
    </head>
    <body>
        <form action="/submit?js_challenge=1" method="POST">
            <input type="hidden" name="jsc_token" value="" />
        </form>
    </body>
    </html>
    """
    redirect_stub_html = """
    <html>
    <body>
    <shreddit-redirect redirect-url="https://www.reddit.com/r/pics/comments/pic_canon/beautiful_lake/"></shreddit-redirect>
    </body>
    </html>
    """
    final_post_html = """
    <html>
    <body>
    <shreddit-post
        post-title="Beautiful Lake"
        author="nature_lover"
        subreddit-prefixed-name="r/pics"
        post-type="image"
        content-href="https://i.redd.it/lake.jpg"
        id="t3_pic_canon">
    </shreddit-post>
    </body>
    </html>
    """
    # 1. Initial request returns 403 with JS challenge
    resp_challenge = MagicMock()
    resp_challenge.status = 403
    resp_challenge.url = "https://www.reddit.com/r/pics/comments/pic_canon/?js_challenge=1"
    resp_challenge.text = AsyncMock(return_value=challenge_html)

    # 2. Challenge submission returns redirect stub HTML
    resp_submit = MagicMock()
    resp_submit.status = 200
    resp_submit.url = "https://www.reddit.com/submit?js_challenge=1"
    resp_submit.text = AsyncMock(return_value=redirect_stub_html)

    # 3. Following redirect gets the canonical post page
    resp_canon = MagicMock()
    resp_canon.status = 200
    resp_canon.url = "https://www.reddit.com/r/pics/comments/pic_canon/beautiful_lake/"
    resp_canon.text = AsyncMock(return_value=final_post_html)

    cm_challenge = MagicMock(__aenter__=AsyncMock(return_value=resp_challenge), __aexit__=AsyncMock(return_value=None))
    cm_submit = MagicMock(__aenter__=AsyncMock(return_value=resp_submit), __aexit__=AsyncMock(return_value=None))
    cm_canon = MagicMock(__aenter__=AsyncMock(return_value=resp_canon), __aexit__=AsyncMock(return_value=None))

    mock_session = MagicMock()
    # session.get calls: 1st for initial URL, 2nd for following HTML redirect
    mock_session.get.side_effect = [cm_challenge, cm_canon]
    mock_session.post.return_value = cm_submit

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/pics/comments/pic_canon/")
        assert data is not None
        assert data.title == "Beautiful Lake"
        assert len(data.items) == 1
        assert data.items[0].url == "https://i.redd.it/lake.jpg"
        assert data.error_message is None


@pytest.mark.asyncio
async def test_solve_js_challenge_multiple_forms():
    html_multi_form = """
    <html>
    <body>
    <form action="/search" method="GET">
        <input type="text" name="q" value="cats" />
    </form>
    <form action=/submit?js_challenge=1 method=POST>
        <input type="hidden" name="csrf_token" value="sec123" />
        <input type="hidden" name="jsc_token" value="" />
    </form>
    <script>
        const t = await (async (x) => x + x)("multiToken");
    </script>
    </body>
    </html>
    """
    mock_post_resp = MagicMock()
    mock_post_resp.status = 200
    mock_post_resp.text = AsyncMock(return_value="<shreddit-post post-title=\"Done\"></shreddit-post>")

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_post_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.post.return_value = mock_cm

    result = await reddit_service._solve_js_challenge(
        session=mock_session,
        html=html_multi_form,
        current_url="https://www.reddit.com/r/test/comments/multi/",
    )
    assert result is not None
    assert "<shreddit-post" in result
    mock_session.post.assert_called_once()
    # Confirm action targeted submit form (not /search) and data included csrf_token and solved jsc_token
    called_action = mock_session.post.call_args[0][0]
    assert "/submit?js_challenge=1" in called_action
    called_data = mock_session.post.call_args[1]["data"]
    assert called_data["csrf_token"] == "sec123"
    assert called_data["jsc_token"] == "multiTokenmultiToken"


@pytest.mark.asyncio
async def test_parse_shreddit_html_nsfw_and_spoiler_no_false_positives():
    html_clean = """
    <html>
    <body>
    <shreddit-post
        post-title="Clean Programming Meme"
        author="nsfw_artist_fan"
        subreddit-prefixed-name="r/spoilers"
        post-type="image"
        content-href="https://i.redd.it/meme.png?width=960&crop=smart&auto=webp&s=token1"
        id="t3_clean1">
    </shreddit-post>
    </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.url = "https://www.reddit.com/r/spoilers/comments/clean1/"
    mock_resp.text = AsyncMock(return_value=html_clean)

    mock_cm = MagicMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=None))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("bot.services.reddit.http_client.get_session", AsyncMock(return_value=mock_session)):
        data = await reddit_service.extract_from_html("https://www.reddit.com/r/spoilers/comments/clean1/")
        assert data is not None
        assert data.over_18 is False
        assert data.is_spoiler is False
        # Verify query params on i.redd.it were stripped cleanly
        assert data.items[0].url == "https://i.redd.it/meme.png"


@pytest.mark.asyncio
async def test_download_media_query_params_extension_detection(tmp_path: Path):
    data = RedditData(
        post_id="png_test",
        url="https://www.reddit.com/r/pics/comments/png_test/",
        title="High Res PNG",
        uploader="photog",
        subreddit="pics",
        media_type="album",
        items=[
            RedditItem(url="https://i.redd.it/photo.png?width=1080&crop=smart&s=tok", media_type="photo"),
            RedditItem(url="https://preview.redd.it/anim.gif?width=400&s=tok", media_type="animation"),
        ],
    )
    with patch("bot.services.reddit.http_client.download_files_batch") as mock_dl:
        mock_dl.return_value = [tmp_path / "f1.png", tmp_path / "f2.gif"]
        files, mtype = await reddit_service.download_media(data, tmp_path)
        assert len(files) == 2
        assert mtype == "album"
        mock_dl.assert_called_once()
        tasks = mock_dl.call_args[0][0]
        # Verify that task file extensions were correctly identified as png and gif
        assert tasks[0][1].suffix == ".png"
        assert tasks[1][1].suffix == ".gif"


@pytest.mark.asyncio
async def test_downloader_service_fallback_video_uses_reddit_post_url_and_probes_metadata(tmp_path: Path):
    fake_video = tmp_path / "fb_task_video.mp4"
    fake_video.write_bytes(b"fallback video content")
    # yt-dlp info lacks duration, width, height
    fake_fallback_info = {
        "title": "Fallback Video Title",
        "uploader": "reddit_user",
    }
    fake_meta = {
        "duration": 42,
        "width": 1920,
        "height": 1080,
    }

    html_video_data = RedditData(
        post_id="post_vid",
        url="https://www.reddit.com/r/videos/comments/post_vid/cool_clip/",
        title="Cool Clip",
        uploader="filmmaker",
        subreddit="videos",
        media_type="video",
        is_video=True,
        video_url="https://v.redd.it/cool123/HLSPlaylist.m3u8",
    )

    with patch("bot.services.reddit.reddit_service.extract_data", AsyncMock(return_value=None)), \
         patch("bot.services.reddit.reddit_service.extract_from_html", AsyncMock(return_value=html_video_data)) as mock_html, \
         patch.object(downloader_service, "download_dir", tmp_path), \
         patch("bot.services.downloader.uuid.uuid4", return_value="fb_task"), \
         patch("bot.services.downloader.get_video_metadata", AsyncMock(return_value=fake_meta)), \
         patch("asyncio.to_thread") as mock_to_thread:

        def fake_to_thread_impl(func, *args, **kwargs):
            if getattr(func, "__name__", "") == "_download":
                raise RuntimeError("HTTP Error 403: Forbidden - Blocked by network security")
            elif getattr(func, "__name__", "") == "_download_fallback_video":
                fake_video.write_bytes(b"fallback video content")
                return fake_fallback_info
            return None

        mock_to_thread.side_effect = fake_to_thread_impl

        media = await downloader_service.download_media("https://www.reddit.com/r/videos/comments/post_vid/cool_clip/")
        assert media is not None
        assert media.media_type == MediaType.VIDEO
        assert media.title == "Cool Clip"
        # Probed metadata was applied
        assert media.duration == 42
        assert media.width == 1920
        assert media.height == 1080
        # HTML scraper was called with canonical post URL, not video URL
        mock_html.assert_called_with("https://www.reddit.com/r/videos/comments/post_vid/")






