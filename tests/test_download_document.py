from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
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
