import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Set

from bot.config import settings

logger = logging.getLogger(__name__)


class QueueManager:
    def __init__(self):
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)
        self._active_users: Set[int] = set()
        self._lock = asyncio.Lock()

    async def can_user_download(self, user_id: int) -> bool:
        async with self._lock:
            return user_id not in self._active_users

    @asynccontextmanager
    async def acquire(self, user_id: int):
        async with self._lock:
            if user_id in self._active_users:
                raise ValueError("User already has an active download task")
            self._active_users.add(user_id)

        try:
            # Wait for global semaphore slot
            async with self._semaphore:
                yield
        finally:
            async with self._lock:
                self._active_users.discard(user_id)

    async def cleanup_old_files(self, max_age_seconds: int = 1800):
        """
        Delete files in downloads directory that are older than max_age_seconds (default 30 mins).
        Ignores Telegram Bot API internal files (.binlog, .db, tqueue, webhooks, bot token folders).
        """
        download_dir = settings.downloads_dir
        if not download_dir.exists():
            return

        now = time.time()
        ignored_extensions = {".binlog", ".db", ".sqlite", ".lock"}
        ignored_prefixes = (".", "tqueue", "webhooks")

        for item in download_dir.iterdir():
            if item.name.startswith(ignored_prefixes):
                continue
            if item.suffix.lower() in ignored_extensions:
                continue
            if ":" in item.name:  # Bot token directories/files
                continue
            try:
                if item.is_file():
                    stat = item.stat()
                    if now - stat.st_mtime > max_age_seconds:
                        item.unlink(missing_ok=True)
                        logger.info(f"Cleaned up stale file: {item.name}")
            except Exception as e:
                logger.warning(f"Failed to delete stale file {item.name}: {e}")

    async def start_periodic_cleanup(self, interval_seconds: int = 600):
        """Background loop to periodically purge orphaned files."""
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self.cleanup_old_files()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")


queue_manager = QueueManager()
