import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

from bot.config import settings

logger = logging.getLogger(__name__)

AUTH_KEYWORDS = (
    "auth",
    "login",
    "cookie",
    "not found",
    "redirect to login",
    "checkpoint",
    "unreachable",
    "blocked by network security",
    "403: blocked",
    "account authentication is required",
    "http error 403",
    "http 403",
)


@dataclass
class GalleryDlResult:
    metadata: dict[str, Any] | None = None
    media_urls: list[str] = field(default_factory=list)
    auth_required: bool = False
    error_message: str | None = None
    all_metadata: list[dict[str, Any]] = field(default_factory=list)
    media_items: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    entries: list[list[Any]] = field(default_factory=list)


def run_gallery_dl_json_sync(
    url: str,
    use_cookies: bool = True,
    timeout_seconds: int = 25,
) -> GalleryDlResult:
    """
    Execute gallery-dl -j synchronously, parsing output entries and error statuses.
    """
    cmd = ["gallery-dl", "-j"]
    if use_cookies and settings.has_cookies:
        cmd.extend(["--cookies", str(settings.cookies_file)])
    cmd.extend(["--", url])

    try:
        res = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if res.returncode != 0:
            err_str = (res.stderr or "").lower()
            if any(k in err_str for k in AUTH_KEYWORDS):
                return GalleryDlResult(auth_required=True, error_message="AUTH_REQUIRED")
            logger.debug(f"gallery-dl returned code {res.returncode} for {url}: {res.stderr}")
            return GalleryDlResult(error_message=res.stderr or f"Exit code {res.returncode}")

        raw_stdout = res.stdout.strip() if res.stdout else ""
        if not raw_stdout:
            return GalleryDlResult()

        parsed = json.loads(raw_stdout)
        if not isinstance(parsed, list):
            return GalleryDlResult(error_message="Unexpected output format from gallery-dl")

        all_metadata: list[dict[str, Any]] = []
        media_urls: list[str] = []
        media_items: list[tuple[str, dict[str, Any]]] = []
        auth_required = False

        for item in parsed:
            if not isinstance(item, list) or len(item) < 2:
                continue
            code = item[0]
            payload = item[1]

            if code == -1 and isinstance(payload, dict):
                msg = payload.get("message", "").lower()
                err = str(payload.get("error", "")).lower()
                if any(k in msg for k in AUTH_KEYWORDS) or any(k in err for k in AUTH_KEYWORDS):
                    auth_required = True
            elif code == 2 and isinstance(payload, dict):
                all_metadata.append(payload)
            elif code == 3 and isinstance(payload, str):
                media_urls.append(payload)
                extra = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                media_items.append((payload, extra))

        primary_meta = all_metadata[0] if all_metadata else None
        return GalleryDlResult(
            metadata=primary_meta,
            media_urls=media_urls,
            auth_required=auth_required,
            all_metadata=all_metadata,
            media_items=media_items,
            entries=parsed,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"gallery-dl timed out after {timeout_seconds}s for {url}")
        return GalleryDlResult(error_message="Timeout")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gallery-dl execution error for {url}: {e}")
        return GalleryDlResult(error_message=str(e))


async def run_gallery_dl_json(
    url: str,
    use_cookies: bool = True,
    timeout_seconds: int = 25,
) -> GalleryDlResult:
    """
    Execute gallery-dl -j in a thread pool executor.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: run_gallery_dl_json_sync(
            url=url,
            use_cookies=use_cookies,
            timeout_seconds=timeout_seconds,
        ),
    )
