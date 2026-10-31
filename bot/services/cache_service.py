import asyncio
import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bot.config import settings
from bot.services.instagram import clean_instagram_url

logger = logging.getLogger(__name__)

# Tracking query parameters to strip across all URLs
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "igsh", "si", "feature", "fbclid", "gclid", "ref", "stkn", "_r", "_t",
    "is_from_webapp", "sender_device", "invite_code",
}


def normalize_url(url: str) -> str:
    """
    Clean and canonicalize URLs so minor query params/variations share the same cache entry.
    """
    if not url:
        return ""

    raw_url = url.strip()

    # 1. Instagram normalization
    if "instagram.com" in raw_url:
        return clean_instagram_url(raw_url).rstrip("/")

    # Parse URL
    try:
        parsed = urlparse(raw_url)
    except (ValueError, TypeError, AttributeError) as e:
        logger.debug(f"urlparse failed for '{raw_url}': {e}")
        return raw_url.rstrip("/")

    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path
    query = parsed.query

    # 2. YouTube canonicalization
    if "youtu.be" in netloc:
        video_id = path.lstrip("/")
        return f"https://www.youtube.com/watch?v={video_id}"

    if "youtube.com" in netloc:
        if "/shorts/" in path:
            video_id = path.split("/shorts/")[1].split("/")[0].split("?")[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        if "/watch" in path:
            q_dict = dict(parse_qsl(query))
            v_id = q_dict.get("v")
            if v_id:
                return f"https://www.youtube.com/watch?v={v_id}"

    # 3. Strip tracking parameters for any other platform
    if query:
        filtered_params = [
            (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        query = urlencode(filtered_params)

    # Clean redundant trailing slashes from path
    path = re.sub(r"/+", "/", path).rstrip("/")

    return urlunparse((scheme, netloc, path, "", query, ""))


def hash_url(url: str) -> str:
    """Generate SHA256 hash for normalized URL."""
    norm = normalize_url(url)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass
class CachedMedia:
    url_hash: str
    url: str
    file_id: str
    file_unique_id: str | None
    platform: str
    media_type: str
    title: str
    file_size: int
    duration: int
    width: int
    height: int
    caption: str | None
    created_at: str | None = None


class CacheService:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.cache_db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite cache table and indexes."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=5000;")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS media_cache (
                    url_hash TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    url TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    duration INTEGER NOT NULL DEFAULT 0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    caption TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (url_hash, media_type)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_cache_created_at ON media_cache(created_at);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_cache_url ON media_cache(url);")
            conn.commit()

    async def get_cached(self, url: str, media_type: str | None = None) -> CachedMedia | None:
        """Retrieve cached media by URL and optional media_type."""
        u_hash = hash_url(url)

        def _sync_get() -> CachedMedia | None:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA busy_timeout=5000;")
                if media_type:
                    cursor.execute(
                        """
                        SELECT url_hash, url, file_id, file_unique_id, platform, media_type,
                               title, file_size, duration, width, height, caption, created_at
                        FROM media_cache
                        WHERE url_hash = ? AND media_type = ?
                        LIMIT 1
                        """,
                        (u_hash, media_type),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT url_hash, url, file_id, file_unique_id, platform, media_type,
                               title, file_size, duration, width, height, caption, created_at
                        FROM media_cache
                        WHERE url_hash = ?
                        ORDER BY CASE WHEN media_type = 'video' THEN 1 ELSE 2 END
                        LIMIT 1
                        """,
                        (u_hash,),
                    )
                row = cursor.fetchone()
                if row:
                    return CachedMedia(
                        url_hash=row[0],
                        url=row[1],
                        file_id=row[2],
                        file_unique_id=row[3],
                        platform=row[4],
                        media_type=row[5],
                        title=row[6],
                        file_size=row[7],
                        duration=row[8],
                        width=row[9],
                        height=row[10],
                        caption=row[11],
                        created_at=row[12],
                    )
                return None

        try:
            return await asyncio.to_thread(_sync_get)
        except sqlite3.Error as e:
            logger.error(f"Failed to read media cache for {url}: {e}")
            return None

    async def save_cached(
        self,
        url: str,
        file_id: str,
        platform: str,
        media_type: str,
        title: str,
        file_unique_id: str | None = None,
        file_size: int = 0,
        duration: int = 0,
        width: int = 0,
        height: int = 0,
        caption: str | None = None,
    ) -> None:
        """Store or update media cache entry."""
        u_hash = hash_url(url)
        norm_url = normalize_url(url)

        def _sync_save() -> None:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA busy_timeout=5000;")
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO media_cache (
                        url_hash, media_type, url, file_id, file_unique_id, platform,
                        title, file_size, duration, width, height, caption, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        u_hash,
                        media_type,
                        norm_url,
                        file_id,
                        file_unique_id,
                        platform,
                        title,
                        file_size,
                        duration,
                        width,
                        height,
                        caption,
                    ),
                )
                conn.commit()

        try:
            await asyncio.to_thread(_sync_save)
            logger.debug(f"Saved media cache for {norm_url} (type: {media_type}, id: {file_id})")
        except sqlite3.Error as e:
            logger.error(f"Failed to save media cache for {url}: {e}")

    async def cleanup_old(self, days: int = 60) -> int:
        """Delete media cache entries older than specified number of days."""
        def _sync_cleanup() -> int:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA busy_timeout=5000;")
                cursor.execute(
                    "DELETE FROM media_cache WHERE created_at < datetime('now', ?)",
                    (f"-{days} days",),
                )
                deleted = cursor.rowcount
                conn.commit()
                return deleted

        try:
            return await asyncio.to_thread(_sync_cleanup)
        except sqlite3.Error as e:
            logger.error(f"Failed to cleanup old media cache: {e}")
            return 0


cache_service = CacheService()
