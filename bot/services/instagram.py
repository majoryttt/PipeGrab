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
from bot.services.http_client import http_client

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


def parse_instagram_story_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract username and optional story_id from an Instagram story URL.
    Returns (username, story_id). If highlight, username is 'highlights' and story_id is highlight_id.
    """
    match = re.search(r'instagram\.com/stories/(?:highlights/(\d+)|([^/?#&]+)(?:/(\d+))?)', url)
    if not match:
        return None, None
    highlight_id, user, story_id = match.groups()
    if highlight_id:
        return "highlights", highlight_id
    return user, story_id


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


def select_best_video_format(formats: List[Dict[str, Any]]) -> Optional[str]:
    """
    Select the best video format URL, prioritizing formats that include an audio track.
    Avoids selecting video-only DASH formats (where acodec is 'none') unless no other format exists.
    """
    if not formats:
        return None

    def _is_video(f: Dict[str, Any]) -> bool:
        if not f.get("url"):
            return False
        if f.get("vcodec") == "none":
            return False
        ext = (f.get("ext") or "").lower()
        video_ext = (f.get("video_ext") or "").lower()
        url = f.get("url") or ""
        return (
            ext in ["mp4", "mkv", "webm", "mov"]
            or video_ext in ["mp4", "mkv", "webm", "mov"]
            or ".mp4" in url
            or f.get("vcodec") is not None
        )

    # 1. Prefer formats with audio (acodec != 'none')
    formats_with_audio = [
        f for f in formats
        if _is_video(f) and f.get("acodec") != "none"
    ]
    if formats_with_audio:
        sorted_audio_fmts = sorted(
            formats_with_audio,
            key=lambda f: (
                (f.get("width") or 0) * (f.get("height") or 0),
                f.get("tbr") or 0,
                f.get("filesize") or 0
            ),
            reverse=True
        )
        return sorted_audio_fmts[0]["url"]

    # 2. Fallback to any video format if no audio-containing format exists
    video_formats = [f for f in formats if _is_video(f)]
    if video_formats:
        sorted_fmts = sorted(
            video_formats,
            key=lambda f: (
                (f.get("width") or 0) * (f.get("height") or 0),
                f.get("tbr") or 0,
                f.get("filesize") or 0
            ),
            reverse=True
        )
        return sorted_fmts[0]["url"]

    return None


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

    async def _extract_story_via_gallery_dl(self, url: str) -> Optional[InstagramData]:
        """Extract Instagram story using gallery-dl with cookies."""
        import subprocess
        import json

        user, target_story_id = parse_instagram_story_url(url)

        def _run():
            try:
                cmd = ["gallery-dl", "-j"]
                if settings.has_cookies:
                    cmd.extend(["--cookies", str(settings.cookies_file)])
                cmd.append(url)
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if res.returncode != 0:
                    err_str = (res.stderr or "").lower()
                    if any(t in err_str for t in ["auth", "login", "cookie", "not found"]):
                        return "AUTH_REQUIRED"
                    return None
                entries = json.loads(res.stdout) if res.stdout else []
                items: List[InstagramItem] = []
                uploader = user or "Instagram User"
                title = f"История @{uploader}" if not target_story_id else f"История @{uploader} ({target_story_id})"
                duration = 0

                for item in entries:
                    code = item[0]
                    if code == -1 and isinstance(item[1], dict):
                        msg = item[1].get("message", "").lower()
                        if any(t in msg for t in ["auth", "login", "cookie", "not found"]):
                            return "AUTH_REQUIRED"
                    elif code == 2 and isinstance(item[1], dict):
                        d = item[1]
                        uploader = d.get("user", {}).get("username") or d.get("username") or uploader
                    elif code == 3 and isinstance(item[1], str):
                        media_url = item[1]
                        meta = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                        ext = (meta.get("extension") or "").lower()
                        is_video = ext in ["mp4", "mov", "mkv"] or ".mp4" in media_url
                        m_type = "video" if is_video else "photo"
                        w = int(meta.get("width") or 0)
                        h = int(meta.get("height") or 0)
                        dur = int(meta.get("duration") or 0)
                        if dur > duration:
                            duration = dur
                        items.append(InstagramItem(media_type=m_type, url=media_url, width=w, height=h))

                if items:
                    final_media_type = "album" if len(items) > 1 else items[0].media_type
                    return InstagramData(
                        url=url,
                        shortcode=target_story_id or user or "story",
                        title=title,
                        uploader=uploader,
                        media_type=final_media_type,
                        items=items,
                        duration=duration
                    )
            except Exception as e:
                logger.warning(f"gallery-dl story extraction failed for {url}: {e}")
            return None

        result = await asyncio.to_thread(_run)
        if result == "AUTH_REQUIRED":
            return InstagramData(
                url=url,
                shortcode=target_story_id or user or "story",
                title="Instagram Story",
                uploader="Instagram",
                media_type="video",
                error_message="AUTH_REQUIRED_INSTAGRAM_STORY"
            )
        return result

    async def extract_data(self, url: str) -> Optional[InstagramData]:
        """
        Extract Instagram post/reel/story metadata supporting photos, albums, and videos.
        """
        shortcode = extract_shortcode(url) or "instagram_media"
        is_story = "/stories/" in url
        story_user, story_id = parse_instagram_story_url(url) if is_story else (None, None)

        # Check for Instagram Stories without cookies
        if is_story and not settings.has_cookies:
            return InstagramData(
                url=url,
                shortcode=story_id or story_user or shortcode,
                title="Instagram Story",
                uploader=story_user or "Instagram",
                media_type="video",
                error_message="AUTH_REQUIRED_INSTAGRAM_STORY"
            )

        # For Instagram Stories with cookies, try gallery-dl first
        if is_story:
            g_story = await self._extract_story_via_gallery_dl(url)
            if g_story:
                return g_story

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
            if is_story and story_id:
                opts["noplaylist"] = True
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
            uploader = info.get("uploader") or info.get("channel") or (story_user if is_story else "Instagram User")

            # Check if it's a playlist / carousel
            is_playlist = info.get("_type") == "playlist" or "entries" in info
            if is_playlist and info.get("entries"):
                items: List[InstagramItem] = []
                for entry in info["entries"]:
                    if not entry:
                        continue
                    formats = entry.get("formats") or []
                    video_url = select_best_video_format(formats)
                    if not video_url and entry.get("url") and (
                        (entry.get("ext") or "").lower() in ["mp4", "mov", "mkv", "webm"]
                        or (entry.get("video_ext") or "").lower() in ["mp4", "mov", "mkv", "webm"]
                    ):
                        video_url = entry.get("url")

                    if video_url:
                        # Video item in carousel
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
                        shortcode=story_id or story_user or shortcode,
                        title=title,
                        uploader=uploader,
                        media_type=media_type,
                        items=items
                    )

            # Single item
            formats = info.get("formats") or []
            best_video_url = select_best_video_format(formats)
            if not best_video_url and info.get("url") and (
                (info.get("ext") or "").lower() in ["mp4", "mov", "mkv", "webm"]
                or (info.get("video_ext") or "").lower() in ["mp4", "mov", "mkv", "webm"]
            ):
                best_video_url = info.get("url")

            if best_video_url:
                duration = int(info.get("duration") or 0)
                return InstagramData(
                    url=url,
                    shortcode=story_id or story_user or shortcode,
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
                        shortcode=story_id or story_user or shortcode,
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
            if any(term in err_str for term in [
                "login required",
                "checkpoint_required",
                "confirm you are not a robot",
                "redirected to the login page",
                "this content is unreachable",
                "you need to log in",
                "use --cookies"
            ]):
                err_code = "AUTH_REQUIRED_INSTAGRAM_STORY" if is_story else "AUTH_REQUIRED_INSTAGRAM"
                return InstagramData(
                    url=url,
                    shortcode=story_id or story_user or shortcode,
                    title="Требуется авторизация",
                    uploader="Instagram",
                    media_type="photo",
                    error_message=err_code
                )
            elif "private account" in err_str or "this account is private" in err_str:
                return InstagramData(
                    url=url,
                    shortcode=story_id or story_user or shortcode,
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
        try:
            session = await http_client.get_session()
            timeout = aiohttp.ClientTimeout(total=10)
            async with session.get(embed_url, headers=self.headers, timeout=timeout) as resp:
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
        except Exception as e:
            logger.debug(f"_extract_from_embed error for {url}: {e}")
        return None

    async def download_media(
        self,
        data: InstagramData,
        download_dir: Path
    ) -> Tuple[List[Path], str]:
        """
        Download extracted Instagram media items concurrently.
        Returns:
            Tuple of (files_list, media_type: 'photo' | 'album' | 'video')
        """
        task_id = str(uuid.uuid4())[:8]
        download_dir.mkdir(parents=True, exist_ok=True)

        download_tasks = []
        file_paths = []
        for idx, item in enumerate(data.items, 1):
            ext = "mp4" if item.media_type == "video" else "jpg"
            file_path = download_dir / f"{task_id}_item_{idx}.{ext}"
            file_paths.append(file_path)
            download_tasks.append(http_client.download_file(item.url, file_path, headers=self.headers))

        results = await asyncio.gather(*download_tasks, return_exceptions=True)

        downloaded_files = [
            fp for fp, res in zip(file_paths, results)
            if res is True and fp.exists() and fp.stat().st_size > 0
        ]

        final_media_type = data.media_type
        if len(downloaded_files) == 1 and final_media_type == "album":
            final_media_type = data.items[0].media_type

        return downloaded_files, final_media_type


instagram_service = InstagramService()
