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
import time

from bot.config import settings
from bot.services.http_client import http_client
from bot.services.image_utils import sanitize_image
from bot.services.cookie_utils import extract_cookies_for_domains
from bot.services.ffmpeg_utils import generate_thumbnail

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
    return any(d in netloc for d in ["vk.com", "vk.ru", "vkvideo.ru", "vk.cc"])


async def resolve_vk_url(url: str, timeout_seconds: int = 10) -> str:
    """Follow redirects to get canonical VK URL (e.g. from vk.cc) and normalize vk.ru to vk.com."""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if "vk.cc" in netloc:
        try:
            session = await http_client.get_session()
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with session.get(url, headers=VK_HEADERS, allow_redirects=True, timeout=timeout) as resp:
                final_url = str(resp.url)
                logger.info(f"Resolved vk.cc redirect '{url}' -> '{final_url}'")
                url = final_url
                parsed = urlparse(url)
                netloc = parsed.netloc.lower()
        except Exception as e:
            logger.warning(f"Failed to resolve vk.cc redirect for {url}: {e}")

    # Normalize vk.ru domains to vk.com for maximum compatibility with yt-dlp and extractors
    if netloc == "vk.ru" or netloc.endswith(".vk.ru"):
        new_netloc = netloc.replace("vk.ru", "vk.com")
        url = url.replace(parsed.netloc, new_netloc, 1)

    return url


def clean_userapi_url(url: str) -> str:
    """
    Clean crop, blur and thumbnail query parameters from VK userapi image URLs
    to retrieve the maximum resolution image.
    """
    # Remove thumbnail crop param ?cs=... or &cs=...
    url = re.sub(r"[?&]cs=[^&]+", "", url)
    # Remove crop rectangle parameter ?crop=... or &crop=...
    url = re.sub(r"[?&]crop=[^&]+", "", url)
    # Remove blur parameter ?blur=... or &blur=...
    url = re.sub(r"[?&]blur=[^&]+", "", url)
    # If the query string was emptied leaving trailing '?' or '&', clean it up
    url = re.sub(r"\?&", "?", url)
    url = url.rstrip("?&")
    # If there are query parameters left but no '?', ensure the first '&' becomes '?'
    if "?" not in url and "&" in url:
        url = url.replace("&", "?", 1)
    return url


VK_SIZE_ORDER = {
    "s": 1,
    "m": 2,
    "x": 3,
    "o": 4,
    "p": 5,
    "q": 6,
    "r": 7,
    "y": 8,
    "z": 9,
    "w": 10,
}


def select_best_vk_photo(p: dict) -> Optional[str]:
    """
    Select the highest resolution unblurred image URL from a VK photo object.
    VK web responses often place a blurred low-res placeholder in `orig_photo` (with blur=...),
    while the crystal clear full-resolution versions are stored in `sizes`.
    """
    if not p:
        return None

    def _size_score(item: tuple[int, dict]) -> tuple:
        idx, s = item
        w = s.get("width") or 0
        h = s.get("height") or 0
        type_order = VK_SIZE_ORDER.get(s.get("type", ""), 0)
        return (w * h, w, type_order, idx)

    sizes = p.get("sizes", [])
    # 1. Prefer unblurred size from `sizes` with the largest resolution
    unblurred = [(idx, s) for idx, s in enumerate(sizes) if isinstance(s, dict) and "blur=" not in s.get("url", "")]
    if unblurred:
        best = max(unblurred, key=_size_score)[1]
        if best.get("url"):
            return clean_userapi_url(best["url"])

    # 2. Check orig_photo if not blurred
    orig = p.get("orig_photo", {}).get("url")
    if orig and "blur=" not in orig:
        return clean_userapi_url(orig)

    # 3. Fallback to any largest size in sizes
    if sizes:
        enumerated_sizes = list(enumerate(sizes))
        best_fallback = max(enumerated_sizes, key=_size_score)[1]
        if best_fallback.get("url"):
            return clean_userapi_url(best_fallback["url"])

    if orig:
        return clean_userapi_url(orig)

    return None


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


@dataclass
class VKDownloadProgress:
    status: str = "downloading"
    downloaded_bytes: int = 0
    total_bytes: int = 0
    percent: float = 0.0
    speed: float = 0.0
    eta: int = 0


@dataclass
class VKVideoData:
    url: str
    video_id: str
    title: str = "Видео ВКонтакте"
    duration: int = 0
    uploader: str = "ВКонтакте"
    thumbnail_url: Optional[str] = None
    stream_url: Optional[str] = None
    is_hls: bool = False
    quality: Optional[str] = None
    files: Dict[str, str] = field(default_factory=dict)
    error_message: Optional[str] = None


class VKService:
    def __init__(self):
        self.headers = VK_HEADERS

    def get_cookies(self) -> Dict[str, str]:
        """
        Parse cookies for VK domains from settings.cookies_file if present.
        """
        if not settings.has_cookies:
            return {}
        try:
            content = settings.cookies_file.read_text(encoding="utf-8", errors="ignore")
            return extract_cookies_for_domains(content, ["vk.com", "vk.ru", "vkvideo.ru"])
        except Exception as e:
            logger.debug(f"Failed to read VK cookies: {e}")
            return {}

    def parse_vk_url_type(self, url: str) -> Tuple[str, Optional[str]]:
        """
        Identify whether a VK URL points to a video, clip, wall post, or photo.
        Returns (type, id_str).
        """
        parsed = urlparse(url)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Check for ?w=wall-123_456, ?w=photo-123_456, ?w=video-123_456
        if "w" in query and query["w"]:
            w_val = query["w"][0]
            if w_val.startswith("wall"):
                return "wall", w_val.replace("wall", "")
            elif w_val.startswith("photo"):
                return "photo", w_val.replace("photo", "")
            elif w_val.startswith("video"):
                return "video", w_val.replace("video", "").split("_")[:2][0] + "_" + w_val.replace("video", "").split("_")[1] if len(w_val.replace("video", "").split("_")) >= 2 else w_val.replace("video", "")
            elif w_val.startswith("clip"):
                return "clip", w_val.replace("clip", "")

        # Check for ?z=photo-123_456%2Fwall-123_456 or ?z=video-123_456
        if "z" in query and query["z"]:
            z_val = unquote(query["z"][0])
            wall_m = re.search(r"wall(-?\d+_\d+)", z_val)
            if wall_m:
                return "wall", wall_m.group(1)
            photo_m = re.search(r"photo(-?\d+_\d+)", z_val)
            if photo_m:
                return "photo", photo_m.group(1)
            video_m = re.search(r"video(-?\d+_\d+)", z_val)
            if video_m:
                return "video", video_m.group(1)

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

    def parse_vk_video_target(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract video ID (owner_id_video_id) and optional access_key from a VK video/clip URL.
        Returns (video_id, access_key).
        """
        parsed = urlparse(url)
        path = parsed.path
        query = parse_qs(parsed.query)

        access_key = None
        if "access_key" in query and query["access_key"]:
            access_key = query["access_key"][0]

        target_str = ""
        if "w" in query and query["w"]:
            w_val = query["w"][0]
            if w_val.startswith("video"):
                target_str = "/" + w_val
        elif "z" in query and query["z"]:
            z_val = unquote(query["z"][0])
            m = re.search(r"video(-?\d+_\d+)(?:[/_]([a-zA-Z0-9]+))?", z_val)
            if m:
                vid = m.group(1)
                ak = m.group(2)
                return vid, (access_key or ak)

        if not target_str:
            target_str = path

        # Support /video-123_456, /video-123_456/abc, /video-123_456_abc, /clip-123_456
        m = re.search(r"/(?:video|clip)(-?\d+_\d+)(?:[/_]([a-zA-Z0-9]+))?", target_str)
        if m:
            vid = m.group(1)
            ak = m.group(2)
            return vid, (access_key or ak)

        vtype, vid = self.parse_vk_url_type(url)
        if vtype in ["video", "clip"] and vid:
            return vid, access_key

        return None, None

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

        cookies = self.get_cookies()

        try:
            session = await http_client.get_session()
            async with session.post(
                endpoint,
                data=data,
                headers=headers,
                cookies=cookies or None,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
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
                    clean_text = text.strip()
                    if clean_text.startswith("<!--"):
                        clean_text = clean_text[4:].strip()
                    res = json.loads(clean_text)
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
        Optional fetch via official VK API if active VK token is configured.
        """
        token = settings.active_vk_token
        if not token:
            return None

        endpoint = f"https://api.vk.com/method/wall.getById?posts={post_id}&extended=1&v=5.199&access_token={token}"
        try:
            session = await http_client.get_session()
            async with session.get(endpoint, headers=self.headers, proxy=settings.vk_proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
                            best_url = select_best_vk_photo(p)
                            if best_url:
                                image_urls.append(best_url)
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

        data_exec_matches = re.findall(r"data-exec=[\"'](.*?)[\"']", html_content)
        for raw_json in data_exec_matches:
            try:
                unescaped = html.unescape(raw_json)
                if "PostContentContainer" in unescaped:
                    unescaped_clean = unescaped.replace(r"\/", "/")
                    parsed = json.loads(unescaped_clean)
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
                            best_url = select_best_vk_photo(p)
                            if best_url and best_url not in image_urls:
                                image_urls.append(best_url)
                        elif att_type == "video" and not video_url:
                            v = att.get("video", {})
                            vid_owner = v.get("owner_id")
                            vid_id = v.get("id")
                            if vid_owner and vid_id:
                                video_url = f"https://vk.com/video{vid_owner}_{vid_id}"
                    break
            except Exception as e:
                logger.debug(f"Error parsing data-exec: {e}")

        # Also look up author name from HTML header if not resolved yet
        if uploader == "ВКонтакте":
            author_span_m = re.search(r'class="[^"]*PostHeaderTitle__authorName[^"]*"[^>]*>(.*?)</span>', html_content)
            if author_span_m:
                uploader = strip_html_tags(author_span_m.group(1)).strip() or uploader
            else:
                author_a_m = re.search(r'class="[^"]*author[^"]*"[^>]*>(.*?)</a>', html_content)
                if author_a_m:
                    uploader = strip_html_tags(author_a_m.group(1)).strip() or uploader

        # Extract post caption if still empty
        if not text:
            post_text_m = re.search(r'<div class="[^"]*wall_post_text[^"]*"[^>]*>(.*?)</div>', html_content, re.DOTALL)
            if post_text_m:
                text = strip_html_tags(post_text_m.group(1))

        # Extract photos from background-image of post container or grid (e.g. multi-photo albums)
        bg_matches = re.findall(r"background-image:\s*url\(['\"]?(https://[^'\"\)]+)['\"]?\)", html_content)
        for bg in bg_matches:
            if "userapi.com" in bg and "blur=" not in bg:
                clean = clean_userapi_url(bg)
                if clean not in image_urls:
                    image_urls.append(clean)

        # Extract video links if not already found
        if not video_url:
            vid_matches = re.findall(r'href=[\"\'](/video-?\d+_\d+[^\"\']*)[\"\']', html_content)
            if vid_matches:
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
        token = settings.active_vk_token
        if token:
            endpoint = f"https://api.vk.com/method/photos.getById?photos={photo_id}&extended=1&v=5.199&access_token={token}"
            try:
                session = await http_client.get_session()
                async with session.get(endpoint, headers=self.headers, proxy=settings.vk_proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("response", [])
                        if items:
                            item = items[0]
                            text = strip_html_tags(item.get("text", ""))
                            best_url = select_best_vk_photo(item)

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
        cookies = self.get_cookies()

        async def _download_single(img_url: str, idx: int) -> Optional[Path]:
            out_file = download_dir / f"{task_id}_vk_{idx}.jpg"
            try:
                async with session.get(
                    img_url,
                    headers=self.headers,
                    cookies=cookies or None,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
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

    async def extract_video(self, url: str) -> Optional[VKVideoData]:
        """
        Extract video information and stream URLs using official VK API (video.get).
        Requires settings.active_vk_token (user access token or service token).
        """
        token = settings.active_vk_token
        if not token:
            logger.debug("No VK token available for extract_video")
            return None

        url = await resolve_vk_url(url)
        video_id, access_key = self.parse_vk_video_target(url)
        if not video_id:
            logger.debug(f"Could not parse VK video ID from {url}")
            return None

        videos_param = f"{video_id}_{access_key}" if access_key else video_id
        endpoint = "https://api.vk.com/method/video.get"
        params = {
            "videos": videos_param,
            "extended": 1,
            "fields": "id,owner_id,title,description,duration,files,player,views,image",
            "access_token": token,
            "v": "5.199",
        }

        try:
            session = await http_client.get_session()
            timeout = aiohttp.ClientTimeout(total=15)
            proxy = settings.vk_proxy
            async with session.get(endpoint, params=params, headers=self.headers, proxy=proxy, timeout=timeout) as resp:
                if resp.status != 200:
                    logger.warning(f"VK video.get returned HTTP {resp.status} for {videos_param}")
                    return None

                data = await resp.json()
                if "error" in data:
                    err = data["error"]
                    logger.warning(f"VK video.get API error {err.get('error_code')}: {err.get('error_msg')}")
                    return None

                items = data.get("response", {}).get("items", [])
                if not items:
                    logger.info(f"VK video.get returned no items for {videos_param}")
                    return None

                item = items[0]
                title = strip_html_tags(item.get("title", "")) or "Видео ВКонтакте"
                duration = item.get("duration", 0)

                # Resolve author/uploader
                uploader = "ВКонтакте"
                owner_id = item.get("owner_id")
                if owner_id and owner_id < 0:
                    for g in data.get("response", {}).get("groups", []):
                        if g.get("id") == abs(owner_id):
                            uploader = g.get("name", uploader)
                            break
                elif owner_id:
                    for p in data.get("response", {}).get("profiles", []):
                        if p.get("id") == owner_id:
                            uploader = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or uploader
                            break

                # Resolve thumbnail
                thumbnail_url = None
                images = item.get("image", [])
                if images:
                    best_img = max(images, key=lambda x: (x.get("width", 0) * x.get("height", 0), x.get("width", 0)))
                    thumbnail_url = clean_userapi_url(best_img.get("url", ""))

                files = item.get("files", {})
                stream_url = None
                quality = None
                is_hls = False

                quality_preferences = [
                    ("mp4_2160", "2160p"),
                    ("mp4_1440", "1440p"),
                    ("mp4_1080", "1080p"),
                    ("mp4_720", "720p"),
                    ("mp4_480", "480p"),
                    ("mp4_360", "360p"),
                    ("mp4_240", "240p"),
                ]

                for q_key, q_label in quality_preferences:
                    if q_key in files and files[q_key]:
                        stream_url = files[q_key]
                        quality = q_label
                        break

                if not stream_url and "hls" in files and files["hls"]:
                    stream_url = files["hls"]
                    quality = "HLS"
                    is_hls = True

                if not stream_url and item.get("player"):
                    stream_url = item["player"]
                    quality = "player"
                    is_hls = False

                if not stream_url:
                    logger.warning(f"No streamable URL found in VK video.get for {videos_param}")
                    return None

                return VKVideoData(
                    url=url,
                    video_id=video_id,
                    title=title,
                    duration=duration,
                    uploader=uploader,
                    thumbnail_url=thumbnail_url,
                    stream_url=stream_url,
                    is_hls=is_hls,
                    quality=quality,
                    files=files,
                )
        except Exception as e:
            logger.error(f"Error querying VK API video.get for {url}: {e}")
            return None

    async def download_video(
        self,
        video_data: VKVideoData,
        download_dir: Path,
        progress_callback: Optional[Callable] = None
    ) -> Tuple[Optional[Path], Optional[Path], int]:
        """
        Download video stream (direct MP4 via aiohttp or HLS via yt-dlp).
        Returns (video_path, thumbnail_path, file_size).
        """
        download_dir.mkdir(parents=True, exist_ok=True)
        task_id = str(uuid.uuid4())[:8]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", video_data.title)[:80].strip() or "video"
        out_file = download_dir / f"{task_id}_{safe_title}.mp4"
        thumb_file = download_dir / f"{task_id}_thumb.jpg"
        thumb_path: Optional[Path] = None

        # 1. Download thumbnail if available
        if video_data.thumbnail_url:
            try:
                session = await http_client.get_session()
                t_timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(video_data.thumbnail_url, headers=self.headers, proxy=settings.vk_proxy, timeout=t_timeout) as t_resp:
                    if t_resp.status == 200:
                        async with aiofiles.open(thumb_file, "wb") as tf:
                            await tf.write(await t_resp.read())
                        if thumb_file.exists() and thumb_file.stat().st_size > 0:
                            thumb_path = await sanitize_image(thumb_file) or thumb_file
            except Exception as te:
                logger.debug(f"Failed to download VK video thumbnail: {te}")

        # 2. Download video stream
        if not video_data.stream_url:
            raise ValueError("No stream URL available in VK video data")

        if video_data.is_hls or video_data.stream_url.endswith(".m3u8"):
            # Download HLS via yt-dlp
            def _download_hls():
                import yt_dlp
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "nocheckcertificate": True,
                    "outtmpl": str(out_file),
                    "concurrent_fragment_downloads": 8,
                }
                if settings.vk_proxy:
                    ydl_opts["proxy"] = settings.vk_proxy

                loop = None
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass

                last_update = [0.0]

                if progress_callback:
                    def _hook(d: dict):
                        now = time.time()
                        if now - last_update[0] < 1.5 and d.get("status") != "finished":
                            return
                        last_update[0] = now
                        status = d.get("status", "downloading")
                        downloaded = d.get("downloaded_bytes", 0)
                        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                        percent = (downloaded / total * 100) if total > 0 else 0.0
                        speed = d.get("speed") or 0.0
                        eta = d.get("eta") or 0
                        p = VKDownloadProgress(
                            status=status,
                            downloaded_bytes=downloaded,
                            total_bytes=total,
                            percent=percent,
                            speed=speed,
                            eta=int(eta)
                        )
                        try:
                            if loop and asyncio.iscoroutinefunction(progress_callback):
                                asyncio.run_coroutine_threadsafe(progress_callback(p), loop)
                            else:
                                progress_callback(p)
                        except Exception:
                            pass

                    ydl_opts["progress_hooks"] = [_hook]

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_data.stream_url])

            await asyncio.to_thread(_download_hls)
        else:
            # Direct MP4 stream download via aiohttp
            session = await http_client.get_session()
            timeout = aiohttp.ClientTimeout(total=1800)
            proxy = settings.vk_proxy
            async with session.get(video_data.stream_url, headers=self.headers, proxy=proxy, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"VK CDN stream returned HTTP {resp.status}")

                total_bytes = int(resp.headers.get("Content-Length", 0))
                if total_bytes > settings.max_file_size_bytes:
                    raise ValueError(
                        f"Размер видео превышает допустимый лимит ({total_bytes / (1024*1024):.1f} МБ > {settings.max_file_size_mb} МБ)"
                    )

                downloaded_bytes = 0
                start_time = time.time()
                last_update = [start_time]

                async with aiofiles.open(out_file, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1048576):
                        await f.write(chunk)
                        downloaded_bytes += len(chunk)

                        if progress_callback:
                            now = time.time()
                            if now - last_update[0] >= 1.5 or (total_bytes and downloaded_bytes == total_bytes):
                                last_update[0] = now
                                elapsed = now - start_time
                                speed = (downloaded_bytes / elapsed) if elapsed > 0 else 0.0
                                eta = int((total_bytes - downloaded_bytes) / speed) if (speed > 0 and total_bytes > downloaded_bytes) else 0
                                percent = (downloaded_bytes / total_bytes * 100) if total_bytes > 0 else 0.0

                                p = VKDownloadProgress(
                                    status="downloading",
                                    downloaded_bytes=downloaded_bytes,
                                    total_bytes=total_bytes,
                                    percent=percent,
                                    speed=speed,
                                    eta=eta
                                )
                                try:
                                    if asyncio.iscoroutinefunction(progress_callback):
                                        await progress_callback(p)
                                    else:
                                        progress_callback(p)
                                except Exception:
                                    pass

        if not out_file.exists() or out_file.stat().st_size == 0:
            raise RuntimeError(f"Downloaded file {out_file} is empty or missing")

        file_size = out_file.stat().st_size

        # 3. Generate thumbnail if not downloaded from VK
        if not thumb_path or not thumb_path.exists():
            candidate_thumb = download_dir / f"{out_file.stem}_thumb.jpg"
            gen_thumb = await generate_thumbnail(out_file, candidate_thumb, timestamp=1.0)
            if gen_thumb and gen_thumb.exists():
                thumb_path = gen_thumb

        return out_file, thumb_path, file_size


vk_service = VKService()
