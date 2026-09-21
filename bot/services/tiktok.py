import asyncio
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

from bot.services.http_client import http_client

logger = logging.getLogger(__name__)

TIKTOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def is_tiktok_url(url: str) -> bool:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    return "tiktok.com" in netloc or netloc.endswith("tiktok.com")


async def resolve_tiktok_url(url: str, timeout_seconds: int = 10) -> str:
    """Follow redirects to get canonical TikTok URL (e.g. from vt.tiktok.com, vm.tiktok.com, or tiktok.com/t/...)."""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if not (netloc.startswith("vt.") or netloc.startswith("vm.") or "/t/" in parsed.path or "tiktok.com/t/" in url):
        return url
    try:
        session = await http_client.get_session()
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with session.get(url, headers=TIKTOK_HEADERS, allow_redirects=True, timeout=timeout) as resp:
            final_url = str(resp.url)
            logger.info(f"Resolved TikTok redirect '{url}' -> '{final_url}'")
            return final_url
    except Exception as e:
        logger.warning(f"Failed to resolve TikTok redirect for {url}: {e}")
        return url


@dataclass
class TikTokData:
    url: str
    item_id: str
    title: str
    uploader: str
    is_photo: bool
    image_urls: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    music_url: Optional[str] = None
    music_title: Optional[str] = None
    duration: int = 0


class TikTokService:
    def __init__(self):
        self.headers = TIKTOK_HEADERS

    async def _extract_via_gallery_dl(self, url: str) -> Optional[TikTokData]:
        """Extract TikTok photo slideshow using gallery-dl."""
        import subprocess
        import json

        def _run():
            try:
                res = subprocess.run(["gallery-dl", "-j", url], capture_output=True, text=True, timeout=25)
                if res.returncode != 0:
                    return None
                entries = json.loads(res.stdout)
                title = "TikTok Photo"
                uploader = "TikTok User"
                item_id = ""
                images: List[str] = []
                music_url = None
                for item in entries:
                    code = item[0]
                    if code == 2 and isinstance(item[1], dict):
                        d = item[1]
                        title = d.get("desc") or d.get("description") or title
                        author = d.get("author", {}) if isinstance(d.get("author"), dict) else {}
                        uploader = author.get("nickname") or author.get("unique_id") or uploader
                        item_id = str(d.get("id") or d.get("aweme_id") or "")
                    elif code == 3 and isinstance(item[1], str):
                        u = item[1]
                        if ".mp3" in u or "audio" in u or "mime_type=audio" in u:
                            music_url = u
                        else:
                            images.append(u)
                if images:
                    return TikTokData(
                        url=url,
                        item_id=item_id,
                        title=title,
                        uploader=uploader,
                        is_photo=True,
                        image_urls=images,
                        music_url=music_url
                    )
            except Exception as e:
                logger.warning(f"gallery-dl extraction error for TikTok {url}: {e}")
            return None

        return await asyncio.to_thread(_run)

    async def extract_data(self, url: str) -> Optional[TikTokData]:
        """
        Extract media metadata from TikTok URL (supports photos, slideshows and videos).
        """
        resolved_url = await resolve_tiktok_url(url)
        is_photo_url = "/photo/" in resolved_url

        # 1. Primary extractor for photo slideshows: gallery-dl
        if is_photo_url:
            g_data = await self._extract_via_gallery_dl(resolved_url)
            if g_data:
                return g_data

        # 2. TikWM API (fallback)
        api_url = f"https://www.tikwm.com/api/?url={resolved_url}"
        try:
            session = await http_client.get_session()
            timeout = aiohttp.ClientTimeout(total=15)
            async with session.get(api_url, headers=self.headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data and data.get("code") == 0:
                        d = data.get("data", {})
                        item_id = str(d.get("id", ""))
                        title = d.get("title") or "TikTok Media"
                        author = d.get("author", {})
                        uploader = author.get("nickname") or author.get("unique_id") or "TikTok User"
                        images = d.get("images", [])
                        music = d.get("music") or d.get("music_info", {}).get("play")
                        music_title = d.get("music_info", {}).get("title")
                        video_url = d.get("play") if not images else None
                        duration = int(d.get("duration") or 0)

                        is_photo = bool(images) or is_photo_url

                        return TikTokData(
                            url=resolved_url,
                            item_id=item_id,
                            title=title,
                            uploader=uploader,
                            is_photo=is_photo,
                            image_urls=images,
                            video_url=video_url,
                            music_url=music,
                            music_title=music_title,
                            duration=duration
                        )
                    else:
                        logger.warning(f"TikWM returned error code {data.get('code') if data else 'None'}")
        except Exception as e:
            logger.warning(f"TikWM extraction failed for {resolved_url}: {e}")

        # 3. Fallback: if it's explicitly a photo post, parse directly from page HTML
        if is_photo_url:
            try:
                data = await self._extract_from_html(resolved_url)
                if data:
                    return data
            except Exception as e:
                logger.error(f"HTML fallback extraction failed for {resolved_url}: {e}")

        return None

    async def _extract_from_html(self, url: str) -> Optional[TikTokData]:
        """Fallback extraction directly from TikTok web page HTML."""
        import json
        try:
            session = await http_client.get_session()
            timeout = aiohttp.ClientTimeout(total=12)
            async with session.get(url, headers=self.headers, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

                m = re.search(
                    r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                    html,
                    re.DOTALL
                )
                if m:
                    raw_json = json.loads(m.group(1))
                    detail = raw_json.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
                    item = detail.get("itemInfo", {}).get("itemStruct", {})
                    if item:
                        item_id = str(item.get("id", ""))
                        title = item.get("desc") or "TikTok Photo"
                        author = item.get("author", {})
                        uploader = author.get("nickname") or author.get("uniqueId") or "TikTok User"
                        raw_images = item.get("imagePost", {}).get("images", [])
                        image_urls = []
                        for img in raw_images:
                            url_list = img.get("imageURL", {}).get("urlList") or img.get("displayImage", {}).get("urlList")
                            if url_list:
                                image_urls.append(url_list[0])

                        music_info = item.get("music", {})
                        music_url = music_info.get("playUrl")
                        music_title = music_info.get("title")

                        if image_urls:
                            return TikTokData(
                                url=url,
                                item_id=item_id,
                                title=title,
                                uploader=uploader,
                                is_photo=True,
                                image_urls=image_urls,
                                music_url=music_url,
                                music_title=music_title
                            )
        except Exception as e:
            logger.debug(f"_extract_from_html error for {url}: {e}")
        return None

    async def download_media(
        self,
        data: TikTokData,
        download_dir: Path,
        progress_callback: Optional[Callable] = None
    ) -> Tuple[List[Path], Optional[Path], str]:
        """
        Download TikTok media.
        Fast-path:
          - If video: downloads direct MP4 stream via http_client in ~0.5s without yt-dlp/ffmpeg.
          - If photo slideshow: downloads all images and audio concurrently using asyncio.gather.
        Returns:
            Tuple of (files_list, optional_audio_path, media_type_str: 'photo' | 'album' | 'video')
        """
        task_id = str(uuid.uuid4())[:8]
        download_dir.mkdir(parents=True, exist_ok=True)

        # 1. Fast direct video download
        if not data.is_photo and data.video_url:
            file_path = download_dir / f"{task_id}_tiktok.mp4"
            logger.info(f"Fast-downloading TikTok video stream directly for {data.url}")
            ok = await http_client.download_file(data.video_url, file_path, headers=self.headers)
            if ok and file_path.exists() and file_path.stat().st_size > 0:
                return [file_path], None, "video"
            logger.warning(f"Fast video download failed for {data.video_url}, will fallback")

        # 2. Parallel photo slideshow download
        if data.is_photo and data.image_urls:
            download_tasks = []
            file_paths = []
            for idx, img_url in enumerate(data.image_urls, 1):
                f_path = download_dir / f"{task_id}_slide_{idx}.jpg"
                file_paths.append(f_path)
                download_tasks.append(http_client.download_file(img_url, f_path, headers=self.headers))

            candidate_audio = None
            if data.music_url:
                candidate_audio = download_dir / f"{task_id}_music.mp3"
                download_tasks.append(http_client.download_file(data.music_url, candidate_audio, headers=self.headers))

            results = await asyncio.gather(*download_tasks, return_exceptions=True)

            if candidate_audio:
                img_results = results[:-1]
                music_res = results[-1]
            else:
                img_results = results
                music_res = False

            downloaded_images = [
                fp for fp, res in zip(file_paths, img_results)
                if res is True and fp.exists() and fp.stat().st_size > 0
            ]

            audio_path = (
                candidate_audio
                if (music_res is True and candidate_audio and candidate_audio.exists() and candidate_audio.stat().st_size > 0)
                else None
            )

            media_type = "photo" if len(downloaded_images) == 1 else "album"
            return downloaded_images, audio_path, media_type

        return [], None, "video"


tiktok_service = TikTokService()
