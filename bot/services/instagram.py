import asyncio
import html
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any, Callable
from urllib.parse import urlparse

import aiofiles
import aiohttp
import yt_dlp

from bot.config import settings

logger = logging.getLogger(__name__)

INSTAGRAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def is_instagram_url(url: str) -> bool:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    return "instagram.com" in netloc or netloc.endswith("instagram.com")


def extract_shortcode(url: str) -> Optional[str]:
    match = re.search(r'/(?:p|reel|tv|stories|share)/([^/?#&]+)', url)
    return match.group(1) if match else None


def get_highest_res_thumbnail(thumbnails: List[Dict[str, Any]]) -> Optional[str]:
    """Find the thumbnail with highest width*height or last in list."""
    if not thumbnails:
        return None
    valid_thumbs = [t for t in thumbnails if t.get("url")]
    if not valid_thumbs:
        return None
    # Sort by resolution if dimensions available
    sorted_thumbs = sorted(
        valid_thumbs,
        key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
        reverse=True
    )
    return sorted_thumbs[0]["url"]


@dataclass
class InstagramItem:
    media_type: str  # 'photo' or 'video'
    url: str  # direct download URL
    width: int = 0
    height: int = 0


@dataclass
class InstagramData:
    url: str
    shortcode: str
    title: str
    uploader: str
    media_type: str  # 'photo', 'album', 'video'
    items: List[InstagramItem] = field(default_factory=list)
    duration: int = 0
    error_message: Optional[str] = None


class InstagramService:
    def __init__(self):
        self.headers = INSTAGRAM_HEADERS

    async def extract_data(self, url: str) -> Optional[InstagramData]:
        """
        Extract Instagram post/reel/story metadata supporting photos, albums, and videos.
        """
        shortcode = extract_shortcode(url) or "instagram_media"

        # Check for Instagram Stories without cookies
        if "/stories/" in url and not settings.has_cookies:
            return InstagramData(
                url=url,
                shortcode=shortcode,
                title="Instagram Story",
                uploader="Instagram",
                media_type="video",
                error_message="AUTH_REQUIRED_INSTAGRAM_STORY"
            )

        # 1. Primary extractor: yt-dlp with ignore_no_formats_error=True
        def _extract_ydl():
            opts: Dict[str, Any] = {
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "ignore_no_formats_error": True,
                "extract_flat": False,
                "user_agent": self.headers["User-Agent"],
            }
            if settings.has_cookies:
                opts["cookiefile"] = str(settings.cookies_file)

            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = None
        ydl_error = None
        try:
            info = await asyncio.to_thread(_extract_ydl)
        except Exception as e:
            ydl_error = e
            logger.warning(f"yt-dlp extraction failed for Instagram URL {url}: {e}")

        if info:
            title = info.get("title") or info.get("description") or "Instagram Media"
            if len(title) > 80:
                title = title[:77] + "..."
            uploader = info.get("uploader") or info.get("channel") or "Instagram User"

            # Check if it's a playlist / carousel
            is_playlist = info.get("_type") == "playlist" or "entries" in info
            if is_playlist and info.get("entries"):
                items: List[InstagramItem] = []
                for entry in info["entries"]:
                    if not entry:
                        continue
                    formats = entry.get("formats") or []
                    video_formats = [f for f in formats if f.get("vcodec") and f.get("vcodec") != "none"]
                    if video_formats:
                        # Video item in carousel
                        video_url = video_formats[-1].get("url")
                        if video_url:
                            items.append(InstagramItem(media_type="video", url=video_url))
                    else:
                        # Photo item in carousel
                        img_url = get_highest_res_thumbnail(entry.get("thumbnails", []))
                        if img_url:
                            items.append(InstagramItem(media_type="photo", url=img_url))

                if items:
                    media_type = "album" if len(items) > 1 else items[0].media_type
                    return InstagramData(
                        url=url,
                        shortcode=shortcode,
                        title=title,
                        uploader=uploader,
                        media_type=media_type,
                        items=items
                    )

            # Single item
            formats = info.get("formats") or []
            video_formats = [f for f in formats if f.get("vcodec") and f.get("vcodec") != "none"]
            if video_formats:
                best_video_url = video_formats[-1].get("url")
                duration = int(info.get("duration") or 0)
                return InstagramData(
                    url=url,
                    shortcode=shortcode,
                    title=title,
                    uploader=uploader,
                    media_type="video",
                    items=[InstagramItem(media_type="video", url=best_video_url)],
                    duration=duration
                )
            else:
                # Single photo post
                img_url = get_highest_res_thumbnail(info.get("thumbnails", []))
                if img_url:
                    return InstagramData(
                        url=url,
                        shortcode=shortcode,
                        title=title,
                        uploader=uploader,
                        media_type="photo",
                        items=[InstagramItem(media_type="photo", url=img_url)]
                    )

        # 2. Fallback: Instagram public embed extraction for posts without cookies
        if "/p/" in url or "/reel/" in url:
            try:
                embed_data = await self._extract_from_embed(url)
                if embed_data:
                    return embed_data
            except Exception as e:
                logger.warning(f"Instagram embed fallback failed for {url}: {e}")

        # Check for authentication errors
        if ydl_error:
            err_str = str(ydl_error).lower()
            if any(term in err_str for term in ["login required", "checkpoint_required", "confirm you are not a robot", "redirected to the login page"]):
                return InstagramData(
                    url=url,
                    shortcode=shortcode,
                    title="Требуется авторизация",
                    uploader="Instagram",
                    media_type="photo",
                    error_message="AUTH_REQUIRED_INSTAGRAM"
                )
            elif "private account" in err_str or "this account is private" in err_str:
                return InstagramData(
                    url=url,
                    shortcode=shortcode,
                    title="Приватный аккаунт",
                    uploader="Instagram",
                    media_type="photo",
                    error_message="PRIVATE_CONTENT"
                )

        return None

    async def _extract_from_embed(self, url: str) -> Optional[InstagramData]:
        """Extract public Instagram post media from the embed endpoint."""
        shortcode = extract_shortcode(url)
        if not shortcode:
            return None
        embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            async with session.get(embed_url) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()

                # Extract username
                uploader_match = re.search(r'class=["\']CaptionUsername["\'][^>]*>([^<]+)</a>', text)
                uploader = uploader_match.group(1) if uploader_match else "Instagram User"

                # Extract caption
                caption_match = re.search(r'class=["\']Caption["\'][^>]*>(.*?)</div>', text, re.DOTALL)
                title = "Instagram Post"
                if caption_match:
                    clean_caption = re.sub(r'<[^>]+>', '', caption_match.group(1)).strip()
                    title = html.unescape(clean_caption)
                    if len(title) > 80:
                        title = title[:77] + "..."

                # Extract video URL if video
                video_match = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', text)
                if video_match:
                    video_url = html.unescape(video_match.group(1))
                    return InstagramData(
                        url=url,
                        shortcode=shortcode,
                        title=title,
                        uploader=uploader,
                        media_type="video",
                        items=[InstagramItem(media_type="video", url=video_url)]
                    )

                # Extract image(s)
                image_matches = re.findall(r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+srcset=["\']([^"\']+)["\']', text)
                if not image_matches:
                    image_matches = re.findall(r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', text)

                if image_matches:
                    items: List[InstagramItem] = []
                    for raw in image_matches:
                        if "," in raw:
                            # Parse srcset and get highest resolution
                            parts = [p.strip().split(" ") for p in raw.split(",") if p.strip()]
                            parts.sort(
                                key=lambda x: int(x[1].replace("w", "")) if len(x) > 1 and x[1].replace("w", "").isdigit() else 0,
                                reverse=True
                            )
                            best_url = html.unescape(parts[0][0])
                        else:
                            best_url = html.unescape(raw)
                        items.append(InstagramItem(media_type="photo", url=best_url))

                    if items:
                        media_type = "album" if len(items) > 1 else "photo"
                        return InstagramData(
                            url=url,
                            shortcode=shortcode,
                            title=title,
                            uploader=uploader,
                            media_type=media_type,
                            items=items
                        )
        return None

    async def download_media(
        self,
        data: InstagramData,
        download_dir: Path
    ) -> Tuple[List[Path], str]:
        """
        Download extracted Instagram media items.
        Returns:
            Tuple of (files_list, media_type: 'photo' | 'album' | 'video')
        """
        task_id = str(uuid.uuid4())[:8]
        download_dir.mkdir(parents=True, exist_ok=True)
        downloaded_files: List[Path] = []

        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            for idx, item in enumerate(data.items, 1):
                ext = "mp4" if item.media_type == "video" else "jpg"
                file_path = download_dir / f"{task_id}_item_{idx}.{ext}"
                try:
                    async with session.get(item.url) as resp:
                        if resp.status == 200:
                            async with aiofiles.open(file_path, "wb") as f:
                                await f.write(await resp.read())
                            downloaded_files.append(file_path)
                except Exception as e:
                    logger.warning(f"Failed to download Instagram item {item.url}: {e}")

        final_media_type = data.media_type
        if len(downloaded_files) == 1 and final_media_type == "album":
            final_media_type = data.items[0].media_type

        return downloaded_files, final_media_type


instagram_service = InstagramService()
