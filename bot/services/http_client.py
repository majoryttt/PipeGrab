import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import aiofiles
import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


class HTTPClient:
    """
    Singleton HTTP client providing a shared aiohttp.ClientSession with:
    - Connection pooling and keep-alive
    - DNS caching
    - High-speed streaming file downloads
    """
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    connector = aiohttp.TCPConnector(
                        limit=100,
                        limit_per_host=30,
                        ttl_dns_cache=300,
                        force_close=False,
                    )
                    timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
                    self._session = aiohttp.ClientSession(
                        connector=connector,
                        timeout=timeout,
                        headers=DEFAULT_HEADERS,
                    )
                    logger.debug("Initialized shared HTTPClient session with connection pooling.")
        return self._session

    async def download_file(
        self,
        url: str,
        dest_path: Path,
        headers: Optional[Dict[str, str]] = None,
        chunk_size: int = 128 * 1024,
    ) -> bool:
        """
        Stream download a file from url directly to dest_path using chunked writes.
        Returns True if successful, False otherwise.
        """
        session = await self.get_session()
        req_headers = {**DEFAULT_HEADERS, **(headers or {})}
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            async with session.get(url, headers=req_headers, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.warning(f"Download failed with status {resp.status} for {url}")
                    return False
                async with aiofiles.open(dest_path, "wb") as f:
                    try:
                        async for chunk in resp.content.iter_chunked(chunk_size):
                            await f.write(chunk)
                    except (TypeError, AttributeError):
                        content = await resp.read()
                        await f.write(content)
            return dest_path.exists() and dest_path.stat().st_size > 0
        except Exception as e:
            logger.warning(f"Error downloading file from {url} to {dest_path}: {e}")
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass
            return False

    async def get_json(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch and parse JSON from a given URL."""
        session = await self.get_session()
        req_headers = {**DEFAULT_HEADERS, **(headers or {})}
        timeout = aiohttp.ClientTimeout(total=timeout_seconds) if timeout_seconds else None
        try:
            async with session.get(url, headers=req_headers, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                logger.warning(f"HTTP GET {url} failed with status {resp.status}")
                return None
        except Exception as e:
            logger.warning(f"Error fetching JSON from {url}: {e}")
            return None

    async def get_text(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[str]:
        """Fetch raw text/HTML from a given URL."""
        session = await self.get_session()
        req_headers = {**DEFAULT_HEADERS, **(headers or {})}
        timeout = aiohttp.ClientTimeout(total=timeout_seconds) if timeout_seconds else None
        try:
            async with session.get(url, headers=req_headers, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.text()
                return None
        except Exception as e:
            logger.warning(f"Error fetching text from {url}: {e}")
            return None

    async def close(self):
        """Close the shared ClientSession."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("Closed shared HTTPClient session.")


http_client = HTTPClient()
