import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any, Callable
from urllib.parse import urlparse

import aiofiles
import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

PINTEREST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def is_pinterest_url(url: str) -> bool:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    return "pinterest." in netloc or netloc == "pin.it"


async def resolve_pinterest_url(url: str, timeout_seconds: int = 10) -> str:
    """Follow redirects to get canonical Pinterest URL (e.g. from pin.it)."""
    if "pin.it" not in url:
        return url
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=PINTEREST_HEADERS) as session:
            async with session.get(url, allow_redirects=True) as resp:
                final_url = str(resp.url)
                logger.info(f"Resolved pin.it url '{url}' -> '{final_url}'")
                return final_url
    except Exception as e:
        logger.warning(f"Failed to resolve pin.it redirect for {url}: {e}")
        return url


def normalize_to_original_image_url(url: str) -> str:
    """
    Pinterest CDN uses sizes like /236x/, /474x/, /736x/.
    The highest quality image is in /originals/.
    """
    return re.sub(r'/(?:236x|474x|564x|736x)/', '/originals/', url)


def extract_image_signature(url: str) -> Optional[str]:
    """Extract the hex hash identifying the image file."""
    match = re.search(r'/([a-f0-9]{32})\.', url)
    if match:
        return match.group(1)
    parts = urlparse(url).path.rstrip('/').split('/')
    if parts:
        fname = parts[-1]
        return fname.split('.')[0] if '.' in fname else fname
    return None


@dataclass
class PinterestData:
    pin_id: str
    url: str
    title: str
    uploader: str
    media_type: str  # 'photo', 'album', 'animation', 'video', 'board'
    image_urls: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    duration: int = 0
    is_board: bool = False
    board_pin_urls: List[str] = field(default_factory=list)


class PinterestService:
    def __init__(self):
        self.headers = PINTEREST_HEADERS

    async def extract_data(self, url: str) -> Optional[PinterestData]:
        """
        Extract metadata from a Pinterest pin or board URL.
        """
        resolved_url = await resolve_pinterest_url(url)
        parsed = urlparse(resolved_url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]

        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
                async with session.get(resolved_url) as resp:
                    if resp.status != 200:
                        logger.error(f"Pinterest page returned HTTP {resp.status} for {resolved_url}")
                        return None
                    html = await resp.text()
        except Exception as e:
            logger.error(f"Failed to fetch Pinterest page {resolved_url}: {e}")
            return None

        # Check for board: path like /{username}/{boardname}/ without 'pin'
        is_board = False
        board_pin_urls = []
        if len(path_parts) >= 2 and path_parts[0] not in ["pin", "ideas", "search", "today", "settings"]:
            # Find pins within board page
            pin_ids = re.findall(r'/pin/(\d+)/', html)
            seen_ids = set()
            for pid in pin_ids:
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    board_pin_urls.append(f"https://www.pinterest.com/pin/{pid}/")
            if board_pin_urls:
                is_board = True

        pin_id = ""
        for part in path_parts:
            if part.isdigit() and len(part) >= 6:
                pin_id = part
                break
        if not pin_id:
            m = re.search(r'/pin/(\d+)', resolved_url)
            if m:
                pin_id = m.group(1)

        title = "Pinterest Media"
        uploader = "Pinterest"
        raw_images: List[str] = []
        video_candidates: List[Dict[str, Any]] = []

        # 1. Parse JSON-LD scripts
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                if isinstance(ld, dict):
                    t = ld.get("headline") or ld.get("name")
                    if t and isinstance(t, str):
                        title = t.strip()
                    author_obj = ld.get("author")
                    if isinstance(author_obj, dict):
                        uploader = author_obj.get("name") or author_obj.get("alternateName") or uploader
                    elif isinstance(author_obj, str):
                        uploader = author_obj

                    img = ld.get("image")
                    if isinstance(img, str) and img.startswith("http"):
                        raw_images.append(img)
                    elif isinstance(img, list):
                        raw_images.extend([i for i in img if isinstance(i, str) and i.startswith("http")])

                    if ld.get("@type") == "VideoObject":
                        v_url = ld.get("contentUrl")
                        if v_url and isinstance(v_url, str):
                            video_candidates.append({"url": v_url, "quality": 100})
            except Exception:
                pass

        # 2. Parse Relay / PWS data
        relay_patterns = [
            r'window\.__PWS_RELAY_REGISTER_COMPLETED_REQUEST__\([^,]+,\s*(\{.*?\})\);?\s*</script>',
            r'<script[^>]*id=["\']__PWS_DATA__["\'][^>]*>(.*?)</script>',
        ]
        for pattern in relay_patterns:
            for m in re.finditer(pattern, html, re.DOTALL):
                try:
                    payload = json.loads(m.group(1))
                    def walk(obj):
                        if isinstance(obj, dict):
                            data_node = obj.get("data") if isinstance(obj.get("data"), dict) else obj

                            t = data_node.get("title") or data_node.get("grid_title") or data_node.get("seoTitle")
                            if t and isinstance(t, str) and t.strip():
                                nonlocal title
                                if title == "Pinterest Media":
                                    title = t.strip()

                            pinner = data_node.get("pinner") or data_node.get("closeup_attribution")
                            if isinstance(pinner, dict):
                                nonlocal uploader
                                if uploader == "Pinterest":
                                    uploader = pinner.get("fullName") or pinner.get("username") or uploader

                            vids = data_node.get("videos")
                            if isinstance(vids, dict) and "video_list" in vids:
                                for vname, vobj in vids["video_list"].items():
                                    if isinstance(vobj, dict) and vobj.get("url"):
                                        q = 90 if "720" in vname else (80 if "480" in vname else 50)
                                        video_candidates.append({"url": vobj["url"], "quality": q, "duration": vobj.get("duration")})

                            spd = data_node.get("storyPinData")
                            if isinstance(spd, dict) and "pages" in spd:
                                for page in spd["pages"]:
                                    for block in page.get("blocks", []):
                                        v_block = block.get("video")
                                        if isinstance(v_block, dict) and "video_list" in v_block:
                                            for vname, vobj in v_block["video_list"].items():
                                                if isinstance(vobj, dict) and vobj.get("url"):
                                                    video_candidates.append({"url": vobj["url"], "quality": 90, "duration": vobj.get("duration")})

                                        for img_key in ["images_originals", "images_750x", "images_736x", "images_474x", "images_236x"]:
                                            if img_key in block and isinstance(block[img_key], dict) and block[img_key].get("url"):
                                                raw_images.append(block[img_key]["url"])
                                                break

                            imgs = data_node.get("images")
                            if isinstance(imgs, dict):
                                for s in ["orig", "736x", "564x", "474x", "236x"]:
                                    if s in imgs and isinstance(imgs[s], dict) and imgs[s].get("url"):
                                        raw_images.append(imgs[s]["url"])
                                        break

                            for k, v in obj.items():
                                walk(v)
                        elif isinstance(obj, list):
                            for item in obj:
                                walk(item)

                    walk(payload)
                except Exception:
                    pass

        # If it's a board with pins
        if is_board and board_pin_urls:
            return PinterestData(
                pin_id=pin_id or "board",
                url=resolved_url,
                title=title if title != "Pinterest Media" else f"Доска {uploader}",
                uploader=uploader,
                media_type="board",
                is_board=True,
                board_pin_urls=board_pin_urls
            )

        # Process image URLs preserving original extension if present
        signature_map: Dict[str, str] = {}
        ordered_signatures: List[str] = []

        for img_url in raw_images:
            sig = extract_image_signature(img_url)
            if not sig:
                sig = img_url

            if sig not in signature_map:
                ordered_signatures.append(sig)
                signature_map[sig] = img_url
            else:
                # If current URL is /originals/, prioritize it over thumbnails
                if "/originals/" in img_url:
                    signature_map[sig] = img_url

        final_images = [signature_map[s] for s in ordered_signatures if signature_map[s]]

        # Best video selection
        best_video_url = None
        duration = 0
        if video_candidates:
            mp4_vids = [v for v in video_candidates if ".mp4" in v["url"]]
            if mp4_vids:
                mp4_vids.sort(key=lambda x: x.get("quality", 0), reverse=True)
                best_video_url = mp4_vids[0]["url"]
                duration = int(float(mp4_vids[0].get("duration") or 0) / 1000)
            else:
                video_candidates.sort(key=lambda x: x.get("quality", 0), reverse=True)
                best_video_url = video_candidates[0]["url"]
                duration = int(float(video_candidates[0].get("duration") or 0) / 1000)

        # Media type determination
        if best_video_url:
            media_type = "video"
        elif len(final_images) > 1:
            media_type = "album"
        elif len(final_images) == 1:
            single_url = final_images[0]
            if single_url.lower().endswith(".gif"):
                media_type = "animation"
            else:
                media_type = "photo"
        else:
            return None

        return PinterestData(
            pin_id=pin_id or "pin",
            url=resolved_url,
            title=title,
            uploader=uploader,
            media_type=media_type,
            image_urls=final_images,
            video_url=best_video_url,
            duration=duration,
            is_board=False
        )

    async def download_file_with_fallback(
        self,
        session: aiohttp.ClientSession,
        url: str,
        dest_path: Path
    ) -> Optional[Path]:
        """
        Download image or video file. If /originals/ returns 403/404, fallback to .png/.jpg or 736x.
        """
        candidates = [url]
        # Generate fallback candidates
        if "/originals/" in url:
            if url.endswith(".jpg"):
                candidates.append(url[:-4] + ".png")
            elif url.endswith(".png"):
                candidates.append(url[:-4] + ".jpg")
            # Fallback to 736x if originals is blocked
            candidates.append(re.sub(r'/originals/', '/736x/', url))
        else:
            # Try originals if given a 736x
            orig = normalize_to_original_image_url(url)
            if orig != url:
                candidates.insert(0, orig)

        for candidate_url in candidates:
            try:
                async with session.get(candidate_url, headers=self.headers) as resp:
                    if resp.status == 200:
                        content_type = resp.headers.get("Content-Type", "").lower()
                        # Update suffix if needed based on Content-Type
                        actual_ext = dest_path.suffix
                        if "png" in content_type:
                            actual_ext = ".png"
                        elif "jpeg" in content_type or "jpg" in content_type:
                            actual_ext = ".jpg"
                        elif "gif" in content_type:
                            actual_ext = ".gif"
                        elif "mp4" in content_type:
                            actual_ext = ".mp4"

                        final_dest = dest_path.with_suffix(actual_ext)
                        async with aiofiles.open(final_dest, "wb") as f:
                            while True:
                                chunk = await resp.content.read(64 * 1024)
                                if not chunk:
                                    break
                                await f.write(chunk)
                        return final_dest
                    elif resp.status in [403, 404]:
                        continue
            except Exception as e:
                logger.debug(f"Candidate {candidate_url} failed: {e}")
                continue

        logger.error(f"All candidates failed for download of {url}")
        return None

    async def download_pin(
        self,
        pin_data: PinterestData,
        download_dir: Path,
        progress_callback: Optional[Callable[[Any], Any]] = None
    ) -> Tuple[List[Path], str]:
        """
        Download pin media files (photos, GIFs, albums, or videos).
        Returns list of downloaded file paths and final media type.
        """
        task_id = str(uuid.uuid4())[:8]
        downloaded_paths: List[Path] = []
        final_type = pin_data.media_type

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Video pin
            if pin_data.media_type == "video" and pin_data.video_url:
                dest = download_dir / f"{task_id}_pinterest_video.mp4"
                res = await self.download_file_with_fallback(session, pin_data.video_url, dest)
                if res and res.exists():
                    downloaded_paths.append(res)
                    return downloaded_paths, "video"

            # 2. Image / Album / GIF pin
            total = len(pin_data.image_urls)
            for idx, img_url in enumerate(pin_data.image_urls, 1):
                ext = ".gif" if img_url.lower().endswith(".gif") else ".jpg"
                dest = download_dir / f"{task_id}_pin_{idx}{ext}"
                res = await self.download_file_with_fallback(session, img_url, dest)
                if res and res.exists():
                    downloaded_paths.append(res)

        if not downloaded_paths:
            return [], final_type

        if len(downloaded_paths) > 1:
            final_type = "album"
        elif len(downloaded_paths) == 1:
            if downloaded_paths[0].suffix.lower() == ".gif":
                final_type = "animation"
            else:
                final_type = "photo"

        return downloaded_paths, final_type


pinterest_service = PinterestService()
