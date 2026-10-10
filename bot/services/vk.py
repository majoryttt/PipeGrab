import asyncio
import html
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any, Callable
from urllib.parse import urlparse, parse_qs, unquote

import aiofiles
import aiohttp

from bot.config import settings
from bot.services.http_client import http_client
from bot.services.image_utils import sanitize_image

logger = logging.getLogger(__name__)

VK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


def is_vk_url(url: str) -> bool:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    return any(d in netloc for d in ["vk.com", "vkvideo.ru", "vk.cc"])


async def resolve_vk_url(url: str, timeout_seconds: int = 10) -> str:
    """Follow redirects to get canonical VK URL (e.g. from vk.cc)."""
    parsed = urlparse(url)
    if "vk.cc" not in parsed.netloc.lower():
        return url

    try:
        session = await http_client.get_session()
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with session.get(url, headers=VK_HEADERS, allow_redirects=True, timeout=timeout) as resp:
            final_url = str(resp.url)
            logger.info(f"Resolved vk.cc redirect '{url}' -> '{final_url}'")
            return final_url
    except Exception as e:
        logger.warning(f"Failed to resolve vk.cc redirect for {url}: {e}")
        return url


def clean_userapi_url(url: str) -> str:
    """
    Clean crop and thumbnail query parameters from VK userapi image URLs
    to retrieve the maximum resolution image.
    """
    # Remove thumbnail crop param ?cs=... or &cs=...
    url = re.sub(r"[?&]cs=[^&]+", "", url)
    # Remove crop rectangle parameter ?crop=... or &crop=...
    url = re.sub(r"[?&]crop=[^&]+", "", url)
    # If the query string was emptied leaving trailing '?' or '&', clean it up
    url = re.sub(r"\?&", "?", url)
    url = url.rstrip("?&")
    # If there are query parameters left but no '?', ensure the first '&' becomes '?'
    if "?" not in url and "&" in url:
        url = url.replace("&", "?", 1)
    return url


def strip_html_tags(text: str) -> str:
    """Strip HTML tags and unescape entities for clean Telegram captions."""
    if not text:
        return ""
    # Replace <br> and <br/> with newline
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities
    text = html.unescape(text)
    return text.strip()


@dataclass
class VKPostData:
    url: str
    post_id: str
    text: str = ""
    uploader: str = "ВКонтакте"
    media_type: str = "photo"  # 'photo', 'album', 'video'
    image_urls: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    error_message: Optional[str] = None


class VKService:
    def __init__(self):
        self.headers = VK_HEADERS

    def parse_vk_url_type(self, url: str) -> Tuple[str, Optional[str]]:
        """
        Identify whether a VK URL points to a video, clip, wall post, or photo.
        Returns (type, id_str).
        """
        parsed = urlparse(url)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Check for ?w=wall-123_456 or ?w=photo-123_456
        if "w" in query and query["w"]:
            w_val = query["w"][0]
            if w_val.startswith("wall"):
                return "wall", w_val.replace("wall", "")
            elif w_val.startswith("photo"):
                return "photo", w_val.replace("photo", "")

        # Check for ?z=photo-123_456%2Fwall-123_456 (photo in wall post context)
        if "z" in query and query["z"]:
            z_val = unquote(query["z"][0])
            wall_m = re.search(r"wall(-?\d+_\d+)", z_val)
            if wall_m:
                return "wall", wall_m.group(1)
            photo_m = re.search(r"photo(-?\d+_\d+)", z_val)
            if photo_m:
                return "photo", photo_m.group(1)

        # Path based patterns
        # Video: /video-123_456 or /video123_456
        video_m = re.search(r"/video(-?\d+_\d+)", path)
        if video_m:
            return "video", video_m.group(1)

        # Clip: /clip-123_456 or /clip123_456
        clip_m = re.search(r"/clip(-?\d+_\d+)", path)
        if clip_m:
            return "clip", clip_m.group(1)

        # Wall post: /wall-123_456 or /wall123_456
        wall_m = re.search(r"/wall(-?\d+_\d+)", path)
        if wall_m:
            return "wall", wall_m.group(1)

        # Photo: /photo-123_456 or /photo123_456
        photo_m = re.search(r"/photo(-?\d+_\d+)", path)
        if photo_m:
            return "photo", photo_m.group(1)

        return "unknown", None

    async def _fetch_wkview(self, post_id: str) -> Optional[str]:
        """
        Fetch wall post HTML from public gateway https://vk.com/wkview.php.
        Does not require authentication or API tokens.
        """
        endpoint = "https://vk.com/wkview.php"
        data = {
            "act": "show",
            "al": "1",
            "w": f"wall{post_id}",
        }
        headers = {
            **self.headers,
            "Referer": "https://vk.com/wkview.php",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://vk.com",
        }

        try:
            session = await http_client.get_session()
            async with session.post(endpoint, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning(f"wkview.php returned status {resp.status} for post {post_id}")
                    return None

                raw_bytes = await resp.read()

                # Try parsing with cp1251 then utf-8
                text = None
                for enc in ["cp1251", "utf-8", "windows-1251"]:
                    try:
                        text = raw_bytes.decode(enc)
                        break
                    except Exception:
                        pass

                if not text:
                    text = raw_bytes.decode("utf-8", errors="ignore")

                # Parse JSON payload
                try:
                    res = json.loads(text)
                    payload = res.get("payload", [])
                    if len(payload) > 1 and isinstance(payload[1], list) and len(payload[1]) > 1:
                        # payload[1][1] is HTML
                        return payload[1][1]
                    elif len(payload) > 1 and isinstance(payload[1], str):
                        return payload[1]
                except Exception as je:
                    logger.debug(f"Could not parse wkview JSON: {je}")

                return text
        except Exception as e:
            logger.error(f"Error fetching wkview for {post_id}: {e}")
            return None

    async def _fetch_via_api(self, post_id: str) -> Optional[VKPostData]:
        """
        Optional fetch via official VK API if VK_SERVICE_TOKEN is configured.
        """
        token = settings.vk_service_token
        if not token:
            return None

        endpoint = f"https://api.vk.com/method/wall.getById?posts={post_id}&extended=1&v=5.199&access_token={token}"
        try:
            session = await http_client.get_session()
            async with session.get(endpoint, headers=self.headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    response = data.get("response", {})
                    items = response.get("items", [])
                    if not items:
                        return None

                    item = items[0]
                    text = strip_html_tags(item.get("text", ""))

                    # Extract uploader name from profiles/groups
                    uploader = "ВКонтакте"
                    from_id = item.get("from_id")
                    if from_id and from_id < 0:
                        for g in response.get("groups", []):
                            if g.get("id") == abs(from_id):
                                uploader = g.get("name", uploader)
                                break
                    elif from_id:
                        for p in response.get("profiles", []):
                            if p.get("id") == from_id:
                                uploader = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or uploader
                                break

                    image_urls: List[str] = []
                    video_url: Optional[str] = None

                    # Check attachments
                    attachments = item.get("attachments", [])
                    if not attachments and "copy_history" in item and item["copy_history"]:
                        attachments = item["copy_history"][0].get("attachments", [])

                    for att in attachments:
                        att_type = att.get("type")
                        if att_type == "photo":
                            p = att.get("photo", {})
                            orig = p.get("orig_photo", {}).get("url")
                            if orig:
                                image_urls.append(clean_userapi_url(orig))
                            elif p.get("sizes"):
                                best = p["sizes"][-1].get("url")
                                if best:
                                    image_urls.append(clean_userapi_url(best))
                        elif att_type == "video" and not video_url:
                            v = att.get("video", {})
                            vid_owner = v.get("owner_id")
                            vid_id = v.get("id")
                            if vid_owner and vid_id:
                                video_url = f"https://vk.com/video{vid_owner}_{vid_id}"

                    if image_urls:
                        m_type = "photo" if len(image_urls) == 1 else "album"
                        return VKPostData(
                            url=f"https://vk.com/wall{post_id}",
                            post_id=post_id,
                            text=text,
                            uploader=uploader,
                            media_type=m_type,
                            image_urls=image_urls,
                            video_url=video_url
                        )
                    elif video_url:
                        return VKPostData(
                            url=f"https://vk.com/wall{post_id}",
                            post_id=post_id,
                            text=text,
                            uploader=uploader,
                            media_type="video",
                            video_url=video_url
                        )
        except Exception as e:
            logger.warning(f"Error fetching VK API for {post_id}: {e}")

        return None

    async def extract_wall_post(self, url: str) -> Optional[VKPostData]:
        """
        Extract wall post media (photos, carousel, video) and text.
        """
        url = await resolve_vk_url(url)
        url_type, post_id = self.parse_vk_url_type(url)
        if url_type != "wall" or not post_id:
            return None

        # 1. Try official VK API if token is configured
        if settings.vk_service_token:
            api_res = await self._fetch_via_api(post_id)
            if api_res:
                return api_res

        # 2. Fetch via wkview.php (fast public gateway)
        html_content = await self._fetch_wkview(post_id)
        if not html_content:
            return None

        # Try extracting PostContentContainer/init JSON from data-exec attribute
        text = ""
        uploader = "ВКонтакте"
        image_urls: List[str] = []
        video_url: Optional[str] = None

        data_exec_matches = re.findall(r"data-exec=[\"']({.*?})[\"']", html_content)
        for raw_json in data_exec_matches:
            try:
                unescaped = html.unescape(raw_json)
                if "PostContentContainer/init" in unescaped:
                    parsed = json.loads(unescaped)
                    init_data = parsed.get("PostContentContainer/init", {})
                    item = init_data.get("item", {})
                    text = strip_html_tags(item.get("text", ""))

                    # Look up author / uploader name
                    profiles = init_data.get("profiles", [])
                    groups = init_data.get("groups", [])
                    from_id = item.get("from_id")
                    if from_id and from_id < 0:
                        for g in groups:
                            if g.get("id") == abs(from_id):
                                uploader = g.get("name", uploader)
                                break
                    elif from_id:
                        for p in profiles:
                            if p.get("id") == from_id:
                                uploader = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or uploader
                                break

                    # Check attachments
                    attachments = item.get("attachments", [])
                    if not attachments and "copy_history" in item and item["copy_history"]:
                        attachments = item["copy_history"][0].get("attachments", [])

                    for att in attachments:
                        att_type = att.get("type")
                        if att_type == "photo":
                            p = att.get("photo", {})
                            orig = p.get("orig_photo", {}).get("url")
                            if orig:
                                image_urls.append(clean_userapi_url(orig))
                            elif p.get("sizes"):
                                best = p["sizes"][-1].get("url")
                                if best:
                                    image_urls.append(clean_userapi_url(best))
                        elif att_type == "video" and not video_url:
                            v = att.get("video", {})
                            vid_owner = v.get("owner_id")
                            vid_id = v.get("id")
                            if vid_owner and vid_id:
                                video_url = f"https://vk.com/video{vid_owner}_{vid_id}"
                    break
            except Exception as e:
                logger.debug(f"Error parsing data-exec: {e}")

        # 3. Fallback: Regex extraction if data-exec was not present or empty
        if not image_urls and not video_url:
            # Extract post caption
            post_text_m = re.search(r'<div class="[^"]*wall_post_text[^"]*"[^>]*>(.*?)</div>', html_content, re.DOTALL)
            if post_text_m:
                text = strip_html_tags(post_text_m.group(1))

            # Extract photos from background-image of post container or showPhoto
            bg_matches = re.findall(r"background-image:\s*url\((https://[^\)]+)\)", html_content)
            for bg in bg_matches:
                if "userapi.com" in bg:
                    clean = clean_userapi_url(bg)
                    if clean not in image_urls:
                        image_urls.append(clean)

            # Extract video links
            vid_matches = re.findall(r'href=[\"\'](/video-?\d+_\d+[^\"\']*)[\"\']', html_content)
            if vid_matches and not video_url:
                v_clean = vid_matches[0].split("?")[0]
                video_url = f"https://vk.com{v_clean}"

        # Determine media type
        if image_urls:
            media_type = "photo" if len(image_urls) == 1 else "album"
            return VKPostData(
                url=url,
                post_id=post_id,
                text=text,
                uploader=uploader,
                media_type=media_type,
                image_urls=image_urls,
                video_url=video_url
            )
        elif video_url:
            return VKPostData(
                url=url,
                post_id=post_id,
                text=text,
                uploader=uploader,
                media_type="video",
                video_url=video_url
            )

        return None

    async def extract_photo(self, url: str) -> Optional[VKPostData]:
        """
        Extract photo directly if a single photo URL is provided (/photo-123_456).
        """
        url = await resolve_vk_url(url)
        url_type, photo_id = self.parse_vk_url_type(url)
        if url_type != "photo" or not photo_id:
            return None

        # If official VK API token is present
        if settings.vk_service_token:
            endpoint = f"https://api.vk.com/method/photos.getById?photos={photo_id}&extended=1&v=5.199&access_token={settings.vk_service_token}"
            try:
                session = await http_client.get_session()
                async with session.get(endpoint, headers=self.headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("response", [])
                        if items:
                            item = items[0]
                            text = strip_html_tags(item.get("text", ""))
                            orig = item.get("orig_photo", {}).get("url")
                            best_url = None
                            if orig:
                                best_url = clean_userapi_url(orig)
                            elif item.get("sizes"):
                                best_url = clean_userapi_url(item["sizes"][-1].get("url", ""))

                            if best_url:
                                return VKPostData(
                                    url=url,
                                    post_id=photo_id,
                                    text=text,
                                    uploader="ВКонтакте",
                                    media_type="photo",
                                    image_urls=[best_url]
                                )
            except Exception as e:
                logger.warning(f"Error fetching photo via VK API for {photo_id}: {e}")

        return None

    async def download_post(
        self,
        post_data: VKPostData,
        download_dir: Path,
        progress_callback: Optional[Callable] = None
    ) -> Tuple[List[Path], str, str]:
        """
        Download images concurrently, sanitize them, and return (paths, caption, media_type).
        """
        download_dir.mkdir(parents=True, exist_ok=True)
        session = await http_client.get_session()
        task_id = str(uuid.uuid4())[:8]

        async def _download_single(img_url: str, idx: int) -> Optional[Path]:
            out_file = download_dir / f"{task_id}_vk_{idx}.jpg"
            try:
                async with session.get(img_url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        async with aiofiles.open(out_file, "wb") as f:
                            await f.write(await resp.read())

                        if out_file.exists() and out_file.stat().st_size > 0:
                            # Sanitize to prevent IMAGE_PROCESS_FAILED
                            sanitized = await sanitize_image(out_file)
                            return sanitized or out_file
            except Exception as e:
                logger.warning(f"Failed to download VK image {img_url}: {e}")

            return None

        # Download all images concurrently
        tasks = [_download_single(u, i) for i, u in enumerate(post_data.image_urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        downloaded_files: List[Path] = []
        for res in results:
            if isinstance(res, Path) and res.exists() and res.stat().st_size > 0:
                downloaded_files.append(res)

        final_type = "photo" if len(downloaded_files) == 1 else "album"
        return downloaded_files, post_data.text, final_type


vk_service = VKService()
