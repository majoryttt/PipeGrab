import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot.config import settings
from bot.services.gallery_dl_utils import (
    GalleryDlResult,
    run_gallery_dl_json,
    run_gallery_dl_json_sync,
)
from bot.services.http_client import http_client


def test_run_gallery_dl_json_sync_success():
    fake_entries = [
        [2, {"id": "12345", "title": "Test Pin", "user": {"username": "creator"}}],
        [3, "https://cdn.example.com/media1.jpg", {"width": 1080, "height": 1920}],
        [3, "https://cdn.example.com/media2.jpg", {"width": 800, "height": 800}],
    ]
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = json.dumps(fake_entries)
    mock_res.stderr = ""

    with patch("subprocess.run", return_value=mock_res) as mock_run:
        res = run_gallery_dl_json_sync("https://example.com/pin/12345", use_cookies=False)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["gallery-dl", "-j", "--", "https://example.com/pin/12345"]
        assert isinstance(res, GalleryDlResult)
        assert res.metadata == {"id": "12345", "title": "Test Pin", "user": {"username": "creator"}}
        assert res.media_urls == [
            "https://cdn.example.com/media1.jpg",
            "https://cdn.example.com/media2.jpg",
        ]
        assert len(res.media_items) == 2
        assert res.media_items[0][0] == "https://cdn.example.com/media1.jpg"
        assert res.media_items[0][1] == {"width": 1080, "height": 1920}
        assert not res.auth_required
        assert res.error_message is None


def test_run_gallery_dl_json_sync_cookies_included(tmp_path: Path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("dummy cookie content")
    mock_res = MagicMock(returncode=0, stdout="[]", stderr="")
    with patch.object(settings, "cookies_file", cookies), \
         patch("subprocess.run", return_value=mock_res) as mock_run:
        run_gallery_dl_json_sync("https://instagram.com/p/abc", use_cookies=True)
        cmd = mock_run.call_args[0][0]
        assert "--cookies" in cmd
        assert str(cookies) in cmd
        assert cmd[-2:] == ["--", "https://instagram.com/p/abc"]


def test_run_gallery_dl_json_sync_cookies_omitted_when_flag_false(tmp_path: Path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("dummy cookie content")
    mock_res = MagicMock(returncode=0, stdout="[]", stderr="")
    with patch.object(settings, "cookies_file", cookies), \
         patch("subprocess.run", return_value=mock_res) as mock_run:
        run_gallery_dl_json_sync("https://pinterest.com/pin/abc", use_cookies=False)
        cmd = mock_run.call_args[0][0]
        assert "--cookies" not in cmd
        assert cmd == ["gallery-dl", "-j", "--", "https://pinterest.com/pin/abc"]


def test_run_gallery_dl_json_sync_auth_required_in_stderr():
    mock_res = MagicMock()
    mock_res.returncode = 1
    mock_res.stdout = ""
    mock_res.stderr = "[instagram][error] HTTP 401 Unauthorized: redirect to login"

    with patch("subprocess.run", return_value=mock_res):
        res = run_gallery_dl_json_sync("https://instagram.com/p/private")
        assert res.auth_required is True
        assert res.error_message == "AUTH_REQUIRED"


def test_run_gallery_dl_json_sync_auth_required_in_stdout_code_minus_one():
    fake_entries = [
        [-1, {"message": "Checkpoint required. Please log in with browser cookies."}],
    ]
    mock_res = MagicMock(returncode=0, stdout=json.dumps(fake_entries), stderr="")

    with patch("subprocess.run", return_value=mock_res):
        res = run_gallery_dl_json_sync("https://instagram.com/p/private")
        assert res.auth_required is True


def test_run_gallery_dl_json_sync_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["gallery-dl"], timeout=25)):
        res = run_gallery_dl_json_sync("https://example.com/slow")
        assert res.error_message == "Timeout"
        assert not res.media_urls


def test_run_gallery_dl_json_sync_generic_cli_error():
    mock_res = MagicMock(returncode=2, stdout="", stderr="Connection reset by peer")
    with patch("subprocess.run", return_value=mock_res):
        res = run_gallery_dl_json_sync("https://example.com/missing")
        assert not res.auth_required
        assert res.error_message == "Connection reset by peer"


def test_run_gallery_dl_json_sync_empty_or_invalid_json():
    # Empty output
    mock_res = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_res):
        res = run_gallery_dl_json_sync("https://example.com/empty")
        assert res.metadata is None
        assert res.media_urls == []

    # Non-list output
    mock_res = MagicMock(returncode=0, stdout='{"error": "not a list"}', stderr="")
    with patch("subprocess.run", return_value=mock_res):
        res = run_gallery_dl_json_sync("https://example.com/dict")
        assert res.error_message == "Unexpected output format from gallery-dl"


@pytest.mark.asyncio
async def test_run_gallery_dl_json_async():
    fake_entries = [
        [2, {"id": "async123", "title": "Async Title"}],
        [3, "https://cdn.example.com/async.jpg", {}],
    ]
    mock_res = MagicMock(returncode=0, stdout=json.dumps(fake_entries), stderr="")
    with patch("subprocess.run", return_value=mock_res):
        res = await run_gallery_dl_json("https://example.com/async")
        assert res.metadata == {"id": "async123", "title": "Async Title"}
        assert res.media_urls == ["https://cdn.example.com/async.jpg"]


@pytest.mark.asyncio
async def test_download_files_batch_success(tmp_path: Path):
    p1 = tmp_path / "img1.jpg"
    p2 = tmp_path / "img2.jpg"

    async def fake_dl(url, dest, headers=None):
        dest.write_bytes(b"content")
        return True

    with patch.object(http_client, "download_file", side_effect=fake_dl):
        tasks = [("https://cdn.com/1.jpg", p1), ("https://cdn.com/2.jpg", p2)]
        downloaded = await http_client.download_files_batch(tasks)
        assert len(downloaded) == 2
        assert p1 in downloaded
        assert p2 in downloaded


@pytest.mark.asyncio
async def test_download_files_batch_partial_failure(tmp_path: Path):
    p1 = tmp_path / "img1.jpg"
    p2 = tmp_path / "img2.jpg"

    async def fake_dl(url, dest, headers=None):
        if "1.jpg" in url:
            dest.write_bytes(b"ok")
            return True
        return False

    with patch.object(http_client, "download_file", side_effect=fake_dl):
        tasks = [("https://cdn.com/1.jpg", p1), ("https://cdn.com/2.jpg", p2)]
        downloaded = await http_client.download_files_batch(tasks)
        assert len(downloaded) == 1
        assert downloaded == [p1]


@pytest.mark.asyncio
async def test_download_files_batch_empty():
    downloaded = await http_client.download_files_batch([])
    assert downloaded == []
