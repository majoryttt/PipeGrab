from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram import types
from bot.handlers.base import download_telegram_document
from bot.services.path_wrapper import LocalFilesPathWrapper


@pytest.mark.asyncio
async def test_download_telegram_document_standard(tmp_path):
    dest = tmp_path / "temp.txt"

    bot = AsyncMock()
    doc = MagicMock(spec=types.Document)
    doc.file_id = "test_id"

    # Standard download succeeds by creating the file
    async def mock_download(d, destination):
        destination.write_text("cookie_data")

    bot.download.side_effect = mock_download

    res = await download_telegram_document(bot, doc, destination=dest)
    assert res == dest
    assert dest.read_text() == "cookie_data"


@pytest.mark.asyncio
async def test_download_telegram_document_local_wrapper_fallback(tmp_path):
    dest = tmp_path / "temp.txt"

    # Simulate shared volume where server writes file
    server_dir = tmp_path / "server_files"
    local_dir = tmp_path / "local_files"
    server_dir.mkdir()
    local_dir.mkdir()

    token_folder = "123:token/documents"
    (local_dir / token_folder).mkdir(parents=True)
    real_file = local_dir / token_folder / "file_0.txt"
    real_file.write_text("fallback_cookie_content")

    bot = MagicMock()
    # bot.download fails like on user's server
    bot.download = AsyncMock(side_effect=FileNotFoundError("[Errno 2] No such file or directory: '/var/lib/telegram-bot-api/123:token/documents/file_0.txt'"))

    file_info = MagicMock()
    file_info.file_path = f"/var/lib/telegram-bot-api/{token_folder}/file_0.txt"
    bot.get_file = AsyncMock(return_value=file_info)

    bot.session = MagicMock()
    bot.session.api = MagicMock()
    bot.session.api.wrap_local_file = LocalFilesPathWrapper(
        server_path=Path("/var/lib/telegram-bot-api"),
        local_path=local_dir
    )

    res = await download_telegram_document(bot, MagicMock(file_id="123"), destination=dest)
    assert res == dest
    assert dest.read_text() == "fallback_cookie_content"


@pytest.mark.asyncio
async def test_handle_document_upload_success(tmp_path):
    from bot.handlers.base import handle_document_upload
    from bot.config import settings

    test_cookies_file = tmp_path / "cookies.txt"
    with patch.object(settings, "cookies_file", test_cookies_file), \
         patch.object(settings, "downloads_dir", tmp_path), \
         patch.object(settings, "admin_id", 12345):

        msg = AsyncMock(spec=types.Message)
        msg.document = MagicMock()
        msg.document.file_name = "cookies.txt"
        msg.document.file_size = 1000
        msg.from_user = MagicMock(id=12345)

        status_msg = AsyncMock(spec=types.Message)
        status_msg.edit_text = AsyncMock()
        msg.reply = AsyncMock(return_value=status_msg)

        cookie_content = (
            "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t1822562580\tsessionid\tsid_12345\n"
        )

        async def mock_download_doc(bot, doc, destination):
            destination.write_text(cookie_content)

        with patch("bot.handlers.base.download_telegram_document", side_effect=mock_download_doc):
            await handle_document_upload(msg)

        assert test_cookies_file.exists()
        assert "sid_12345" in test_cookies_file.read_text()
        status_msg.edit_text.assert_called_once()
        assert "успешно сохранён" in status_msg.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_document_upload_blocked_when_no_admin(tmp_path):
    from bot.handlers.base import handle_document_upload
    from bot.config import settings

    test_cookies_file = tmp_path / "cookies.txt"
    with patch.object(settings, "cookies_file", test_cookies_file), \
         patch.object(settings, "downloads_dir", tmp_path), \
         patch.object(settings, "admin_id", None):

        msg = AsyncMock(spec=types.Message)
        msg.document = MagicMock()
        msg.document.file_name = "cookies.txt"
        msg.document.file_size = 1000
        msg.from_user = MagicMock(id=12345)
        msg.reply = AsyncMock()

        await handle_document_upload(msg)

        assert not test_cookies_file.exists()
        msg.reply.assert_called_once()
        assert "Доступ ограничен" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_document_upload_blocked_for_non_admin(tmp_path):
    from bot.handlers.base import handle_document_upload
    from bot.config import settings

    test_cookies_file = tmp_path / "cookies.txt"
    with patch.object(settings, "cookies_file", test_cookies_file), \
         patch.object(settings, "downloads_dir", tmp_path), \
         patch.object(settings, "admin_id", 99999):

        msg = AsyncMock(spec=types.Message)
        msg.document = MagicMock()
        msg.document.file_name = "cookies.txt"
        msg.document.file_size = 1000
        msg.from_user = MagicMock(id=12345)
        msg.reply = AsyncMock()

        await handle_document_upload(msg)

        assert not test_cookies_file.exists()
        msg.reply.assert_called_once()
        assert "Доступ ограничен" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_cookies_admin_vs_regular_user(tmp_path):
    from bot.handlers.base import cmd_cookies
    from bot.config import settings

    # Case 1: Cookies not present
    test_cookies_file = tmp_path / "non_existent_cookies.txt"
    with patch.object(settings, "cookies_file", test_cookies_file), \
         patch.object(settings, "admin_id", 12345):

        # Admin user
        admin_msg = AsyncMock(spec=types.Message)
        admin_msg.from_user = MagicMock(id=12345)
        admin_msg.answer = AsyncMock()
        await cmd_cookies(admin_msg)
        admin_text = admin_msg.answer.call_args[0][0]
        assert "Как установить cookies:" in admin_text

        # Regular user
        user_msg = AsyncMock(spec=types.Message)
        user_msg.from_user = MagicMock(id=54321)
        user_msg.answer = AsyncMock()
        await cmd_cookies(user_msg)
        user_text = user_msg.answer.call_args[0][0]
        assert "Как установить cookies:" not in user_text
        assert "доступны только администратору" in user_text


@pytest.mark.asyncio
async def test_auth_error_messages_admin_vs_user():
    from bot.handlers.download import handle_text_message, run_download_task
    from bot.config import settings
    from bot.services.downloader import MediaInfo, Platform

    # 1. Test YouTube AUTH_REQUIRED in handle_text_message (metadata phase)
    mock_info = MediaInfo(
        title="Restricted Video",
        duration=100,
        uploader="test",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.YOUTUBE,
        url="https://www.youtube.com/watch?v=restricted",
        error_message="AUTH_REQUIRED"
    )

    with patch.object(settings, "admin_id", 12345), \
         patch("bot.handlers.download.downloader_service.get_info", AsyncMock(return_value=mock_info)):

        # Admin user
        admin_status = AsyncMock()
        admin_msg = AsyncMock(spec=types.Message)
        admin_msg.message_id = 1
        admin_msg.chat = MagicMock(id=12345)
        admin_msg.text = "https://www.youtube.com/watch?v=restricted"
        admin_msg.from_user = MagicMock(id=12345)
        admin_msg.reply = AsyncMock(return_value=admin_status)

        with patch("bot.handlers.download.update_status_safely", AsyncMock()) as mock_update:
            await handle_text_message(admin_msg)
            mock_update.assert_called_once()
            called_text = mock_update.call_args[0][1]
            assert "Вы можете загрузить файл <code>cookies.txt</code>" in called_text

        # Regular user
        user_status = AsyncMock()
        user_msg = AsyncMock(spec=types.Message)
        user_msg.message_id = 2
        user_msg.chat = MagicMock(id=54321)
        user_msg.text = "https://www.youtube.com/watch?v=restricted"
        user_msg.from_user = MagicMock(id=54321)
        user_msg.reply = AsyncMock(return_value=user_status)

        with patch("bot.handlers.download.update_status_safely", AsyncMock()) as mock_update:
            await handle_text_message(user_msg)
            mock_update.assert_called_once()
            called_text = mock_update.call_args[0][1]
            assert "может загрузить только администратор" in called_text
            assert "Вы можете загрузить файл" not in called_text

    # 2. Test Instagram Story in run_download_task (download phase)
    story_error_media = MediaInfo(
        title="Instagram Story",
        duration=0,
        uploader="test",
        is_playlist=False,
        playlist_count=0,
        platform=Platform.INSTAGRAM,
        url="https://www.instagram.com/stories/test/123/",
        error_message="AUTH_REQUIRED_INSTAGRAM_STORY"
    )

    with patch.object(settings, "admin_id", 12345), \
         patch("bot.handlers.download.downloader_service.download_media", AsyncMock(return_value=story_error_media)):

        # Admin user
        status_msg_admin = AsyncMock()
        status_msg_admin.bot = AsyncMock()
        with patch("bot.handlers.download.update_status_safely", AsyncMock()) as mock_update:
            await run_download_task(
                chat_id=12345,
                user_id=12345,
                url="https://www.instagram.com/stories/test/123/",
                status_msg=status_msg_admin,
                audio_only=False
            )
            mock_update.assert_called_once()
            called_text = mock_update.call_args[0][1]
            assert "Отправьте файл <code>cookies.txt</code>" in called_text

        # Regular user
        status_msg_user = AsyncMock()
        status_msg_user.bot = AsyncMock()
        with patch("bot.handlers.download.update_status_safely", AsyncMock()) as mock_update:
            await run_download_task(
                chat_id=54321,
                user_id=54321,
                url="https://www.instagram.com/stories/test/123/",
                status_msg=status_msg_user,
                audio_only=False
            )
            mock_update.assert_called_once()
            called_text = mock_update.call_args[0][1]
            assert "администратор бота должен загрузить файл cookies" in called_text
            assert "Отправьте файл <code>cookies.txt</code>" not in called_text

