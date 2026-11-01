import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

import aiofiles
import aiohttp

from bot.config import settings

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
        self._session: aiohttp.ClientSession | None = None
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
        headers: dict[str, str] | None = None,
        chunk_size: int = 128 * 1024,
        proxy: str | None = None,
    ) -> bool:
        """
        Stream download a file from url directly to dest_path using chunked writes.
        Returns True if successful, False otherwise.
        """
        session = await self.get_session()
        req_headers = {**DEFAULT_HEADERS, **(headers or {})}
        effective_proxy = proxy or settings.download_proxy
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            async with session.get(
                url,
                headers=req_headers,
                allow_redirects=True,
                proxy=effective_proxy,
            ) as resp:
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
            if dest_path.exists() and dest_path.stat().st_size > 0:
                return True
            if dest_path.exists():
                with suppress(Exception):
                    dest_path.unlink()
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error downloading file from {url} to {dest_path}: {e}")
            if dest_path.exists():
                with suppress(Exception):
                    dest_path.unlink()
            return False

    async def download_files_batch(
        self,
        tasks: Sequence[tuple[str, Path]],  # (url, dest_path)
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> list[Path]:
        """
        Download multiple files concurrently using download_file.
        Returns a list of successfully downloaded Path objects.
        """
        if proxy is not None:
            coros = [self.download_file(url, path, headers=headers, proxy=proxy) for url, path in tasks]
        else:
            coros = [self.download_file(url, path, headers=headers) for url, path in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)
        return [
            path for (url, path), res in zip(tasks, results)
            if res is True and path.exists() and path.stat().st_size > 0
        ]

    async def close(self):
        """Close the shared ClientSession."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("Closed shared HTTPClient session.")


http_client = HTTPClient()
