import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from bot.config import settings
from bot.services.cookie_utils import get_cookies_for_domain
from bot.services.gallery_dl_utils import run_gallery_dl_json
from bot.services.http_client import http_client

logger = logging.getLogger(__name__)

REDDIT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def is_reddit_url(url: str) -> bool:
    """Check if the given URL belongs to Reddit or its official domains."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().split(":")[0]
        return (
            netloc in ("reddit.com", "redd.it")
            or netloc.endswith((".reddit.com", ".redd.it"))
        )
    except Exception:  # noqa: BLE001
        return False


def normalize_reddit_url(url: str) -> str:
    """
    Clean Reddit URL by stripping tracking params, fragments, and reducing comments URLs to canonical form:
    https://www.reddit.com/r/{subreddit}/comments/{post_id}/
    or https://www.reddit.com/comments/{post_id}/
    This removes double-encoded slugs, tracking query parameters, and extraneous URL parts.
    """
    if not url:
        return ""
    clean = url.strip().split("#")[0].split("?")[0].rstrip("/")
    m = re.match(
        r"https?://(?:[a-zA-Z0-9_-]+\.)?reddit\.com/r/([^/]+)/comments/([a-zA-Z0-9]+)",
        clean,
        re.IGNORECASE,
    )
    if m:
        return f"https://www.reddit.com/r/{m.group(1)}/comments/{m.group(2)}/"
    m_comments = re.match(
        r"https?://(?:[a-zA-Z0-9_-]+\.)?reddit\.com/comments/([a-zA-Z0-9]+)",
        clean,
        re.IGNORECASE,
    )
    if m_comments:
        return f"https://www.reddit.com/comments/{m_comments.group(1)}/"
    return clean


async def resolve_reddit_url(url: str, timeout_seconds: int = 10) -> str:
    """
    Follow redirects for shortened Reddit links:
    - https://redd.it/{id}
    - https://www.reddit.com/r/{sub}/s/{share_id}
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().split(":")[0]
    except Exception:  # noqa: BLE001
        return url

    # Only follow redirects for short domains or share URLs (avoid v.redd.it / i.redd.it)
    if netloc not in ("redd.it", "www.redd.it") and "/s/" not in parsed.path:
        return normalize_reddit_url(url)

    cookies = get_cookies_for_domain("reddit.com") if settings.has_cookies else None

    try:
        session = await http_client.get_session()
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        # 1. First attempt: check 30x redirect header without downloading body
        # (fast and prevents getting blocked by Cloudflare HTML / JS challenges)
        async with session.get(
            url,
            allow_redirects=False,
            timeout=timeout,
            headers=REDDIT_HEADERS,
            cookies=cookies,
        ) as resp:
            status = getattr(resp, "status", None)
            if status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if location:
                    if location.startswith("/"):
                        location = f"https://www.reddit.com{location}"
                    clean_url = normalize_reddit_url(location)
                    logger.info(f"Resolved Reddit redirect '{url}' -> '{clean_url}' (HTTP {status})")
                    return clean_url
            elif status == 403:
                logger.warning(f"Reddit blocked redirect resolution for {url}: HTTP 403 Blocked")
                return normalize_reddit_url(url)
            elif status == 200:
                final_url = str(getattr(resp, "url", url))
                if "/s/" not in final_url and final_url != url:
                    clean_url = normalize_reddit_url(final_url)
                    logger.info(f"Resolved Reddit URL '{url}' -> '{clean_url}'")
                    return clean_url
            elif getattr(resp, "url", None) and str(resp.url) != url:
                clean_url = normalize_reddit_url(str(resp.url))
                logger.info(f"Resolved Reddit URL '{url}' -> '{clean_url}'")
                return clean_url

        # 2. Fallback: follow redirects if no Location header was returned
        async with session.get(
            url,
            allow_redirects=True,
            timeout=timeout,
            headers=REDDIT_HEADERS,
            cookies=cookies,
        ) as resp:
            if getattr(resp, "status", None) == 200:
                final_url = str(getattr(resp, "url", url))
                if "/s/" not in final_url:
                    clean_url = normalize_reddit_url(final_url)
                    logger.info(f"Resolved Reddit redirect via follow: '{url}' -> '{clean_url}'")
                    return clean_url
            elif getattr(resp, "status", None) == 403:
                logger.warning(f"Reddit returned 403 Blocked following redirects for {url}")
                return normalize_reddit_url(url)
            elif getattr(resp, "url", None) and str(resp.url) != url:
                clean_url = normalize_reddit_url(str(resp.url))
                logger.info(f"Resolved Reddit URL '{url}' -> '{clean_url}'")
                return clean_url
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to resolve Reddit redirect for {url}: {e}")
        return normalize_reddit_url(url)

    return normalize_reddit_url(url)


@dataclass
class RedditItem:
    url: str
    media_type: str = "photo"  # 'photo', 'video', 'animation'
    width: int = 0
    height: int = 0


@dataclass
class RedditData:
    post_id: str
    url: str
    title: str
    uploader: str
    subreddit: str
    media_type: str  # 'photo', 'album', 'video', 'animation'
    items: list[RedditItem] = field(default_factory=list)
    is_video: bool = False
    video_url: str | None = None
    external_url: str | None = None
    over_18: bool = False
    is_spoiler: bool = False
    is_self: bool = False
    error_message: str | None = None


class RedditService:
    def __init__(self):
        self.headers = REDDIT_HEADERS

    async def extract_data(self, url: str) -> RedditData | None:
        """
        Extract Reddit post metadata and media items via gallery-dl.
        """
        resolved_url = await resolve_reddit_url(url)
        result = await run_gallery_dl_json(resolved_url, use_cookies=settings.has_cookies, timeout_seconds=25)

        if result.auth_required:
            return RedditData(
                post_id="",
                url=resolved_url,
                title="Требуется авторизация",
                uploader="Reddit",
                subreddit="",
                media_type="photo",
                error_message="AUTH_REQUIRED_REDDIT",
            )

        metadata: dict[str, Any] = result.metadata or {}
        post_id = str(metadata.get("id") or "")
        title = metadata.get("title") or "Reddit Post"
        uploader = metadata.get("author") or "reddit_user"
        subreddit = metadata.get("subreddit") or ""
        over_18 = bool(metadata.get("over_18", False))
        is_spoiler = bool(metadata.get("spoiler", False))
        is_self = bool(metadata.get("is_self", False))
        is_video = bool(metadata.get("is_video", False))
        post_url = str(metadata.get("url") or "")

        # External URLs (e.g. RedGifs, Imgur)
        external_url: str | None = None
        if not is_self and post_url:
            low_post_url = post_url.lower()
            if any(host in low_post_url for host in ["redgifs.com", "imgur.com", "gfycat.com"]):
                external_url = post_url

        if any("ytdl:" in u for u in result.media_urls) or "v.redd.it" in post_url:
            is_video = True

        items: list[RedditItem] = []
        for media_url in result.media_urls:
            if media_url.startswith("ytdl:"):
                is_video = True
                continue
            low_url = media_url.lower()
            # Exclude web pages (such as redgifs watch/iframe links) from photo items
            if any(w in low_url for w in ["redgifs.com/watch", "redgifs.com/ifr", "imgur.com/a/"]):
                continue
            m_type = "photo"
            if low_url.endswith((".mp4", ".mov", ".mkv", ".webm")):
                m_type = "video"
                is_video = True
            elif low_url.endswith(".gif"):
                m_type = "animation"
            items.append(RedditItem(url=media_url, media_type=m_type))

        # Check if post has no downloadable media (text self-posts or links to news/blogs)
        if not items and not is_video and not external_url:
            if metadata:
                return RedditData(
                    post_id=post_id,
                    url=resolved_url,
                    title=title,
                    uploader=uploader,
                    subreddit=subreddit,
                    media_type="photo",
                    is_self=is_self,
                    over_18=over_18,
                    is_spoiler=is_spoiler,
                    error_message="NO_MEDIA_REDDIT",
                )
            if result.error_message:
                logger.debug(f"gallery-dl returned error for Reddit URL {resolved_url}: {result.error_message}")
            return None

        if is_video:
            final_type = "video"
        elif len(items) > 1:
            final_type = "album"
        elif len(items) == 1:
            final_type = items[0].media_type
        else:
            final_type = "video" if external_url else "photo"

        return RedditData(
            post_id=post_id,
            url=resolved_url,
            title=title,
            uploader=uploader,
            subreddit=subreddit,
            media_type=final_type,
            items=items,
            is_video=is_video,
            video_url=post_url if is_video else None,
            external_url=external_url,
            over_18=over_18,
            is_spoiler=is_spoiler,
            is_self=is_self,
        )

    async def download_media(
        self,
        data: RedditData,
        download_dir: Path,
    ) -> tuple[list[Path], str]:
        """
        Download extracted Reddit photos or album items concurrently.
        Returns:
            Tuple of (files_list, media_type: 'photo' | 'album' | 'animation' | 'video')
        """
        task_id = str(uuid.uuid4())[:8]
        download_dir.mkdir(parents=True, exist_ok=True)

        batch_tasks: list[tuple[str, Path]] = []
        for idx, item in enumerate(data.items, 1):
            ext = "jpg"
            if item.media_type == "animation" or item.url.lower().endswith(".gif"):
                ext = "gif"
            elif item.media_type == "video" or item.url.lower().endswith(".mp4"):
                ext = "mp4"
            elif item.url.lower().endswith(".png"):
                ext = "png"
            elif item.url.lower().endswith(".webp"):
                ext = "webp"
            batch_tasks.append((item.url, download_dir / f"{task_id}_reddit_{idx}.{ext}"))

        downloaded_files = await http_client.download_files_batch(batch_tasks, headers=self.headers)

        final_media_type = data.media_type
        if len(downloaded_files) == 1 and final_media_type == "album":
            final_media_type = data.items[0].media_type

        return downloaded_files, final_media_type


reddit_service = RedditService()
