import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, List, Dict, Any, Tuple

import yt_dlp

from bot.config import settings
from bot.services.ffmpeg_utils import get_video_metadata, generate_thumbnail
from bot.services.pinterest import pinterest_service, is_pinterest_url
from bot.services.tiktok import tiktok_service, is_tiktok_url, resolve_tiktok_url
from bot.services.instagram import instagram_service, is_instagram_url
from bot.services.http_client import http_client

logger = logging.getLogger(__name__)


class Platform(str, Enum):
    YOUTUBE = "YouTube"
    TIKTOK = "TikTok"
    INSTAGRAM = "Instagram"
    TWITTER = "Twitter/X"
    PINTEREST = "Pinterest"
    UNKNOWN = "Unknown"


class MediaType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    PHOTO = "photo"
    ANIMATION = "animation"
    ALBUM = "album"


URL_REGEX = re.compile(
    r'(https?://(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|'
    r'https?://[a-zA-Z0-9]+\.[^\s]{2,})',
    re.IGNORECASE
)

PLATFORM_PATTERNS = {
    Platform.YOUTUBE: [
        r'(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=|embed/|v/|shorts/|live/|playlist\?list=)?([a-zA-Z0-9_-]+)'
    ],
    Platform.TIKTOK: [
        r'(?:https?://)?(?:www\.|vm\.|vt\.)?tiktok\.com/'
    ],
    Platform.INSTAGRAM: [
        r'(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv|stories|share)/'
    ],
    Platform.TWITTER: [
        r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/'
    ],
    Platform.PINTEREST: [
        r'(?:https?://)?(?:www\.|[a-z]{2}\.)?pinterest\.[a-z.]+/pin/',
        r'(?:https?://)?(?:www\.|[a-z]{2}\.)?pinterest\.[a-z.]+/[^/\s]+/[^/\s]+',
        r'(?:https?://)?pin\.it/'
    ],
}


def extract_first_url(text: str) -> Optional[str]:
    """Extract the first HTTP/HTTPS URL from a text message."""
    match = URL_REGEX.search(text)
    if match:
        url = match.group(0)
        # Clean trailing punctuation
        return url.rstrip('.,)>]\"\'')
    return None


def detect_platform(url: str) -> Platform:
    """Detect the platform of the given URL."""
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return platform
    if is_pinterest_url(url):
        return Platform.PINTEREST
    return Platform.UNKNOWN


@dataclass
class DownloadProgress:
    status: str  # 'downloading', 'finished', 'processing'
    downloaded_bytes: int = 0
    total_bytes: int = 0
    percent: float = 0.0
    speed: float = 0.0  # bytes/sec
    eta: int = 0  # seconds


@dataclass
class MediaInfo:
    title: str
    duration: int
    uploader: str
    is_playlist: bool
    playlist_count: int
    platform: Platform
    url: str
    media_type: MediaType = MediaType.VIDEO
    file_path: Optional[Path] = None
    file_paths: List[Path] = field(default_factory=list)
    thumbnail_path: Optional[Path] = None
    width: int = 0
    height: int = 0
    file_size: int = 0
    error_message: Optional[str] = None
    audio_path: Optional[Path] = None


class DownloaderService:
    def __init__(self):
        self.download_dir = settings.downloads_dir
        self._cache: Dict[str, Tuple[float, MediaInfo]] = {}

    def get_cached_info(self, url: str) -> Optional[MediaInfo]:
        if url in self._cache:
            ts, info = self._cache[url]
            if time.time() - ts < 300:  # 5 minutes TTL
                return info
            del self._cache[url]
        return None

    def set_cached_info(self, url: str, info: MediaInfo):
        if not info.error_message:
            self._cache[url] = (time.time(), info)

    def _get_base_ydl_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "ignore_no_formats_error": True,
            "logtostderr": False,
            "source_address": "0.0.0.0",
            # Speed optimizations
            "concurrent_fragment_downloads": 8,
            "buffersize": 1048576,
            "http_chunk_size": 10485760,
            # Useful user-agent to reduce 403s on YouTube / TikTok / Instagram
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        }
        if settings.has_cookies:
            opts["cookiefile"] = str(settings.cookies_file)
        return opts

    async def get_info(self, url: str) -> Optional[MediaInfo]:
        """
        Extract metadata without downloading media.
        """
        cached = self.get_cached_info(url)
        if cached:
            return cached

        platform = detect_platform(url)

        # 1. Specialized Pinterest handler (handles images, albums, gifs, videos, and boards)
        if platform == Platform.PINTEREST:
            try:
                pin_data = await pinterest_service.extract_data(url)
                if pin_data:
                    if pin_data.is_board:
                        return MediaInfo(
                            title=pin_data.title,
                            duration=0,
                            uploader=pin_data.uploader,
                            is_playlist=True,
                            playlist_count=len(pin_data.board_pin_urls),
                            platform=Platform.PINTEREST,
                            url=pin_data.url
                        )
                    return MediaInfo(
                        title=pin_data.title,
                        duration=pin_data.duration,
                        uploader=pin_data.uploader,
                        is_playlist=False,
                        playlist_count=0,
                        platform=Platform.PINTEREST,
                        url=pin_data.url,
                        media_type=MediaType(pin_data.media_type)
                    )
            except Exception as pe:
                logger.warning(f"Pinterest extractor failed for {url}, falling back to yt-dlp: {pe}")

        # 2. Specialized TikTok handler (photo slideshows, photo mode, and redirect resolution)
        if platform == Platform.TIKTOK:
            url = await resolve_tiktok_url(url)
            try:
                tk_data = await tiktok_service.extract_data(url)
                if tk_data and tk_data.is_photo:
                    media_type = MediaType.PHOTO if len(tk_data.image_urls) == 1 else MediaType.ALBUM
                    return MediaInfo(
                        title=tk_data.title,
                        duration=tk_data.duration,
                        uploader=tk_data.uploader,
                        is_playlist=False,
                        playlist_count=0,
                        platform=Platform.TIKTOK,
                        url=tk_data.url,
                        media_type=media_type
                    )
            except Exception as te:
                logger.warning(f"TikTok service extract failed for {url}: {te}")

        # 3. Specialized Instagram handler (handles photos, albums/carousels, reels, stories)
        if platform == Platform.INSTAGRAM:
            try:
                ig_data = await instagram_service.extract_data(url)
                if ig_data:
                    if ig_data.error_message:
                        return MediaInfo(
                            title=ig_data.title,
                            duration=0,
                            uploader=ig_data.uploader,
                            is_playlist=False,
                            playlist_count=0,
                            platform=Platform.INSTAGRAM,
                            url=url,
                            error_message=ig_data.error_message
                        )
                    is_album = ig_data.media_type == "album" or len(ig_data.items) > 1
                    media_type = MediaType.ALBUM if is_album else MediaType(ig_data.media_type)
                    return MediaInfo(
                        title=ig_data.title,
                        duration=ig_data.duration,
                        uploader=ig_data.uploader,
                        is_playlist=False,
                        playlist_count=0,
                        platform=Platform.INSTAGRAM,
                        url=ig_data.url,
                        media_type=media_type
                    )
            except Exception as ie:
                logger.warning(f"Instagram extractor failed for {url}, falling back to yt-dlp: {ie}")

        # 4. Instagram stories check: if no cookies, stories strictly require authentication
        if platform == Platform.INSTAGRAM and "/stories/" in url and not settings.has_cookies:
            return MediaInfo(
                title="Instagram Story",
                duration=0,
                uploader="Instagram",
                is_playlist=False,
                playlist_count=0,
                platform=Platform.INSTAGRAM,
                url=url,
                error_message="AUTH_REQUIRED_INSTAGRAM_STORY"
            )

        # 5. Generic yt-dlp extraction
        def _extract():
            ydl_opts = self._get_base_ydl_opts()
            ydl_opts["extract_flat"] = "in_playlist"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await asyncio.to_thread(_extract)
            if not info:
                return None

            is_playlist = info.get("_type") == "playlist" or "entries" in info
            playlist_count = 0
            if is_playlist and "entries" in info:
                entries = [e for e in info.get("entries", []) if e is not None]
                playlist_count = len(entries)

            title = info.get("title") or "Unknown Title"
            duration = int(info.get("duration") or 0)
            uploader = info.get("uploader") or info.get("channel") or "Unknown"

            return MediaInfo(
                title=title,
                duration=duration,
                uploader=uploader,
                is_playlist=is_playlist,
                playlist_count=playlist_count,
                platform=platform,
                url=url,
                media_type=MediaType.VIDEO
            )
        except Exception as e:
            err_str = str(e).lower()
            logger.error(f"Failed to get info for {url}: {e}")

            if any(term in err_str for term in [
                "you need to log in",
                "login required",
                "checkpoint_required",
                "confirm you are not a robot",
                "this content is unreachable",
                "use --cookies"
            ]):
                is_story = platform == Platform.INSTAGRAM and "/stories/" in url
                err_code = "AUTH_REQUIRED_INSTAGRAM_STORY" if is_story else ("AUTH_REQUIRED_INSTAGRAM" if platform == Platform.INSTAGRAM else "AUTH_REQUIRED")
                return MediaInfo(
                    title="Требуется авторизация",
                    duration=0,
                    uploader="Unknown",
                    is_playlist=False,
                    playlist_count=0,
                    platform=platform,
                    url=url,
                    error_message=err_code
                )
            elif "private account" in err_str or "this account is private" in err_str:
                return MediaInfo(
                    title="Приватный контент",
                    duration=0,
                    uploader="Unknown",
                    is_playlist=False,
                    playlist_count=0,
                    platform=platform,
                    url=url,
                    error_message="PRIVATE_CONTENT"
                )

            return None

    async def download_media(
        self,
        url: str,
        audio_only: bool = False,
        progress_callback: Optional[Callable[[DownloadProgress], Any]] = None
    ) -> Optional[MediaInfo]:
        """
        Download media (video, audio, photo, gif, album) and return MediaInfo with file path(s).
        """
        platform = detect_platform(url)

        # 1. Specialized Pinterest handler for photos, gifs, and albums
        if platform == Platform.PINTEREST:
            try:
                pin_data = await pinterest_service.extract_data(url)
                if pin_data and pin_data.media_type in ["photo", "album", "animation"]:
                    files, final_type = await pinterest_service.download_pin(
                        pin_data,
                        self.download_dir,
                        progress_callback=progress_callback
                    )
                    if files:
                        total_size = sum(f.stat().st_size for f in files if f.exists())
                        return MediaInfo(
                            title=pin_data.title,
                            duration=0,
                            uploader=pin_data.uploader,
                            is_playlist=False,
                            playlist_count=0,
                            platform=Platform.PINTEREST,
                            url=pin_data.url,
                            media_type=MediaType(final_type),
                            file_path=files[0],
                            file_paths=files,
                            file_size=total_size
                        )
            except Exception as pe:
                logger.warning(f"Pinterest direct download failed for {url}: {pe}")

        # 2. Specialized TikTok handler: fast direct download for videos, photo posts, and slideshows
        if platform == Platform.TIKTOK and not audio_only:
            url = await resolve_tiktok_url(url)
            try:
                tk_data = await tiktok_service.extract_data(url)
                if tk_data:
                    files, audio_path, final_type = await tiktok_service.download_media(
                        tk_data,
                        self.download_dir,
                        progress_callback=progress_callback
                    )
                    if files:
                        total_size = sum(f.stat().st_size for f in files if f.exists())
                        thumb_path = None
                        if final_type == "video" and files[0].exists():
                            candidate_thumb = self.download_dir / f"{files[0].stem}_thumb.jpg"
                            thumb_path = await generate_thumbnail(files[0], candidate_thumb, timestamp=0.5)

                        return MediaInfo(
                            title=tk_data.title,
                            duration=tk_data.duration,
                            uploader=tk_data.uploader,
                            is_playlist=False,
                            playlist_count=0,
                            platform=Platform.TIKTOK,
                            url=tk_data.url,
                            media_type=MediaType(final_type),
                            file_path=files[0],
                            file_paths=files,
                            file_size=total_size,
                            thumbnail_path=thumb_path,
                            audio_path=audio_path
                        )
            except Exception as te:
                logger.warning(f"TikTok direct download failed for {url}: {te}")

        # 3. Specialized Instagram handler for photos, albums, and stories
        if platform == Platform.INSTAGRAM and not audio_only:
            try:
                ig_data = await instagram_service.extract_data(url)
                if ig_data and ig_data.error_message:
                    return MediaInfo(
                        title=ig_data.title,
                        duration=0,
                        uploader=ig_data.uploader,
                        is_playlist=False,
                        playlist_count=0,
                        platform=Platform.INSTAGRAM,
                        url=url,
                        error_message=ig_data.error_message
                    )
                if ig_data and ig_data.items:
                    files, final_type = await instagram_service.download_media(ig_data, self.download_dir)
                    if files:
                        total_size = sum(f.stat().st_size for f in files if f.exists())
                        return MediaInfo(
                            title=ig_data.title,
                            duration=ig_data.duration,
                            uploader=ig_data.uploader,
                            is_playlist=False,
                            playlist_count=0,
                            platform=Platform.INSTAGRAM,
                            url=url,
                            media_type=MediaType(final_type),
                            file_path=files[0],
                            file_paths=files,
                            file_size=total_size
                        )
            except Exception as ie:
                logger.warning(f"Instagram direct download failed for {url}: {ie}")

        # 4. yt-dlp download (for YouTube, TikTok, Instagram, Twitter, and Pinterest videos)
        task_id = str(uuid.uuid4())[:8]
        out_template = str(self.download_dir / f"{task_id}_%(title).100B.%(ext)s")

        # Capture the running event loop from the caller thread so progress hook can dispatch thread-safely
        loop = asyncio.get_running_loop()
        last_update_time = [0.0]

        def ydl_progress_hook(d: dict):
            if not progress_callback:
                return
            now = time.time()
            if now - last_update_time[0] < 1.5 and d.get("status") != "finished":
                return
            last_update_time[0] = now

            status = d.get("status", "downloading")
            downloaded = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            percent = (downloaded / total * 100) if total > 0 else 0.0
            speed = d.get("speed") or 0.0
            eta = d.get("eta") or 0

            p = DownloadProgress(
                status=status,
                downloaded_bytes=downloaded,
                total_bytes=total,
                percent=percent,
                speed=speed,
                eta=eta
            )
            # Safely schedule the callback on the captured running loop
            try:
                if asyncio.iscoroutinefunction(progress_callback):
                    asyncio.run_coroutine_threadsafe(progress_callback(p), loop)
                else:
                    loop.call_soon_threadsafe(progress_callback, p)
            except Exception as cb_err:
                logger.debug(f"Progress hook dispatch error: {cb_err}")

        ydl_opts = self._get_base_ydl_opts()
        ydl_opts.update({
            "outtmpl": out_template,
            "progress_hooks": [ydl_progress_hook],
            "noplaylist": True,
        })

        if audio_only:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })
        else:
            # Prefer fast MP4 download without FFmpeg merge if single stream available
            ydl_opts.update({
                "format": "best[ext=mp4]/bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a][acodec^=mp4a]/bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        try:
            info = await asyncio.to_thread(_download)
            if not info:
                return None

            title = info.get("title", "media")
            duration = int(info.get("duration") or 0)
            uploader = info.get("uploader") or info.get("channel") or "Unknown"

            # Locate downloaded file in output directory
            files = list(self.download_dir.glob(f"{task_id}_*"))
            if not files:
                logger.error(f"No file found matching {task_id}_*")
                return None

            file_path = files[0]
            for f in files:
                if audio_only and f.suffix.lower() == ".mp3":
                    file_path = f
                    break
                elif not audio_only and f.suffix.lower() == ".mp4":
                    file_path = f
                    break

            file_size = file_path.stat().st_size
            width = info.get("width") or 0
            height = info.get("height") or 0

            thumb_path = None
            if not audio_only and file_path.suffix.lower() in [".mp4", ".mov", ".mkv", ".webm"]:
                candidate_thumb = self.download_dir / f"{task_id}_thumb.jpg"
                # 1. Fast thumbnail download from CDN if provided by extractor
                thumb_url = info.get("thumbnail")
                if not thumb_url and info.get("thumbnails"):
                    valid_thumbs = [t.get("url") for t in info["thumbnails"] if t.get("url")]
                    if valid_thumbs:
                        thumb_url = valid_thumbs[-1]
                if thumb_url:
                    try:
                        thumb_ok = await http_client.download_file(thumb_url, candidate_thumb)
                        if thumb_ok and candidate_thumb.exists() and candidate_thumb.stat().st_size > 0:
                            thumb_path = candidate_thumb
                    except Exception as t_err:
                        logger.debug(f"Failed to download thumbnail from {thumb_url}: {t_err}")

                # 2. Fallback to ffmpeg thumbnail generation only if needed
                if not thumb_path or not thumb_path.exists():
                    thumb_path = await generate_thumbnail(file_path, candidate_thumb, timestamp=1.0)

                # 3. Only invoke ffprobe if dimensions or duration were not in info
                if not (duration and width and height):
                    meta = await get_video_metadata(file_path)
                    if not duration and meta["duration"]:
                        duration = meta["duration"]
                    if not width and meta["width"]:
                        width = meta["width"]
                    if not height and meta["height"]:
                        height = meta["height"]

            media_type = MediaType.AUDIO if audio_only else MediaType.VIDEO

            return MediaInfo(
                title=title,
                duration=duration,
                uploader=uploader,
                is_playlist=False,
                playlist_count=0,
                platform=platform,
                url=url,
                media_type=media_type,
                file_path=file_path,
                file_paths=[file_path],
                thumbnail_path=thumb_path,
                width=width,
                height=height,
                file_size=file_size
            )
        except Exception as e:
            err_str = str(e).lower()
            logger.error(f"Download failed for {url}: {e}")

            # Clean up partial files
            for f in self.download_dir.glob(f"{task_id}_*"):
                try:
                    f.unlink()
                except Exception:
                    pass

            # Fallback for Pinterest video if yt-dlp failed
            if platform == Platform.PINTEREST:
                try:
                    pin_data = await pinterest_service.extract_data(url)
                    if pin_data:
                        files, final_type = await pinterest_service.download_pin(pin_data, self.download_dir)
                        if files:
                            return MediaInfo(
                                title=pin_data.title,
                                duration=pin_data.duration,
                                uploader=pin_data.uploader,
                                is_playlist=False,
                                playlist_count=0,
                                platform=Platform.PINTEREST,
                                url=pin_data.url,
                                media_type=MediaType(final_type),
                                file_path=files[0],
                                file_paths=files,
                                file_size=sum(f.stat().st_size for f in files)
                            )
                except Exception as pe:
                    logger.error(f"Pinterest fallback download failed: {pe}")

            # Fallback for Instagram if yt-dlp failed
            if platform == Platform.INSTAGRAM and not audio_only:
                try:
                    ig_data = await instagram_service.extract_data(url)
                    if ig_data and not ig_data.error_message and ig_data.items:
                        files, final_type = await instagram_service.download_media(ig_data, self.download_dir)
                        if files:
                            return MediaInfo(
                                title=ig_data.title,
                                duration=ig_data.duration,
                                uploader=ig_data.uploader,
                                is_playlist=False,
                                playlist_count=0,
                                platform=Platform.INSTAGRAM,
                                url=url,
                                media_type=MediaType(final_type),
                                file_path=files[0],
                                file_paths=files,
                                file_size=sum(f.stat().st_size for f in files)
                            )
                except Exception as ie:
                    logger.error(f"Instagram fallback download failed: {ie}")

            # Fallback for TikTok if yt-dlp failed
            if platform == Platform.TIKTOK and not audio_only:
                try:
                    tk_data = await tiktok_service.extract_data(url)
                    if tk_data and tk_data.image_urls:
                        files, audio_path, final_type = await tiktok_service.download_media(tk_data, self.download_dir)
                        if files:
                            return MediaInfo(
                                title=tk_data.title,
                                duration=tk_data.duration,
                                uploader=tk_data.uploader,
                                is_playlist=False,
                                playlist_count=0,
                                platform=Platform.TIKTOK,
                                url=tk_data.url,
                                media_type=MediaType(final_type),
                                file_path=files[0],
                                file_paths=files,
                                file_size=sum(f.stat().st_size for f in files),
                                audio_path=audio_path
                            )
                except Exception as te:
                    logger.error(f"TikTok fallback download failed: {te}")

            error_msg = None
            if any(term in err_str for term in ["you need to log in", "login required", "checkpoint_required"]):
                error_msg = "AUTH_REQUIRED_INSTAGRAM" if platform == Platform.INSTAGRAM else "AUTH_REQUIRED"

            return MediaInfo(
                title="Error",
                duration=0,
                uploader="Unknown",
                is_playlist=False,
                playlist_count=0,
                platform=platform,
                url=url,
                error_message=error_msg
            ) if error_msg else None

    async def get_playlist_items(self, url: str, max_items: int = 20) -> List[Dict[str, Any]]:
        """
        Extract entries from a playlist up to max_items.
        """
        # Pinterest board support
        if is_pinterest_url(url) or detect_platform(url) == Platform.PINTEREST:
            try:
                pin_data = await pinterest_service.extract_data(url)
                if pin_data and pin_data.is_board and pin_data.board_pin_urls:
                    return [
                        {"title": f"Пин {i}", "url": purl, "duration": 0}
                        for i, purl in enumerate(pin_data.board_pin_urls[:max_items], 1)
                    ]
            except Exception as pe:
                logger.warning(f"Failed to extract Pinterest board items: {pe}")

        ydl_opts = self._get_base_ydl_opts()
        ydl_opts.update({
            "extract_flat": True,
            "playlist_items": f"1-{max_items}",
        })

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await asyncio.to_thread(_extract)
            if not info or "entries" not in info:
                return []

            results = []
            for entry in info["entries"]:
                if not entry:
                    continue
                video_url = entry.get("url")
                if not video_url:
                    video_id = entry.get("id")
                    if video_id:
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                if video_url:
                    results.append({
                        "title": entry.get("title", "Video"),
                        "url": video_url,
                        "duration": int(entry.get("duration") or 0)
                    })
            return results
        except Exception as e:
            logger.error(f"Failed to extract playlist items: {e}")
            return []


downloader_service = DownloaderService()
