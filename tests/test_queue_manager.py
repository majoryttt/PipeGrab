import pytest
import asyncio
from bot.services.queue_manager import QueueManager


@pytest.mark.asyncio
async def test_queue_manager_user_concurrency():
    qm = QueueManager()
    user_id = 99999

    assert await qm.can_user_download(user_id) is True

    async with qm.acquire(user_id):
        assert await qm.can_user_download(user_id) is False

        # Attempting to acquire again for same user should raise ValueError
        with pytest.raises(ValueError):
            async with qm.acquire(user_id):
                pass

    # After exit, user should be free again
    assert await qm.can_user_download(user_id) is True


@pytest.mark.asyncio
async def test_queue_manager_cleanup_preserves_binlogs_and_system_files(tmp_path, monkeypatch):
    from bot.config import settings
    monkeypatch.setattr(settings, "downloads_dir", tmp_path)

    # Create stale files
    old_mtime = 1000.0

    stale_video = tmp_path / "stale_video.mp4"
    stale_video.write_text("dummy")

    stale_cookie = tmp_path / "temp_cookies_123.txt"
    stale_cookie.write_text("dummy")

    # Protected files
    gitkeep = tmp_path / ".gitkeep"
    gitkeep.write_text("")

    tqueue_binlog = tmp_path / "tqueue.binlog"
    tqueue_binlog.write_text("binlog")

    webhooks_binlog = tmp_path / "webhooks_db.binlog"
    webhooks_binlog.write_text("binlog")

    token_dir = tmp_path / "8926813381:AAH7JH7BJFL4V5Oe4ZTaADUZl2JvObzSIQg"
    token_dir.mkdir()
    (token_dir / "documents").mkdir()
    token_doc = token_dir / "documents" / "file_0.txt"
    token_doc.write_text("data")

    # Set old mtime on all files
    import os
    for p in [stale_video, stale_cookie, gitkeep, tqueue_binlog, webhooks_binlog, token_doc]:
        os.utime(p, (old_mtime, old_mtime))

    qm = QueueManager()
    await qm.cleanup_old_files(max_age_seconds=60)

    # Stale bot files should be deleted
    assert not stale_video.exists()
    assert not stale_cookie.exists()

    # System and protected files MUST be preserved
    assert gitkeep.exists()
    assert tqueue_binlog.exists()
    assert webhooks_binlog.exists()
    assert token_dir.exists()
    assert token_doc.exists()

