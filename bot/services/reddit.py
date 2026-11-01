import html as html_lib
import json
import logging
import re
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

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
        r"https?://(?:[a-zA-Z0-9_-]+\.)?reddit\.com/r/([^/]+)/comments/([a-zA-Z0-9_]+)",
        clean,
        re.IGNORECASE,
    )
    if m:
        return f"https://www.reddit.com/r/{m.group(1)}/comments/{m.group(2)}/"
    m_comments = re.match(
        r"https?://(?:[a-zA-Z0-9_-]+\.)?reddit\.com/comments/([a-zA-Z0-9_]+)",
        clean,
        re.IGNORECASE,
    )
    if m_comments:
        return f"https://www.reddit.com/comments/{m_comments.group(1)}/"
    return clean


async def resolve_reddit_url(url: str, timeout_seconds: int = 10, proxy: str | None = None) -> str:
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
    effective_proxy = proxy or settings.download_proxy

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
            proxy=effective_proxy,
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
            proxy=effective_proxy,
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

    async def extract_data(self, url: str, proxy: str | None = None) -> RedditData | None:
        """
        Extract Reddit post metadata and media items via gallery-dl,
        falling back to native HTML/Shreddit scraper on auth/IP block/no-media.
        """
        resolved_url = await resolve_reddit_url(url, proxy=proxy)
        result = await run_gallery_dl_json(resolved_url, use_cookies=settings.has_cookies, timeout_seconds=25, proxy=proxy)

        if result.auth_required or result.error_message or result.raw_error:
            combined_err = f"{result.error_message or ''} {result.raw_error or ''}".lower()
            is_403_or_ip_block = any(
                k in combined_err
                for k in [
                    "403",
                    "forbidden",
                    "blocked by network security",
                    "your ip address is unable to access",
                    "unable to access the reddit api",
                ]
            )
            fallback_data: RedditData | None = None
            if is_403_or_ip_block:
                logger.info(
                    f"gallery-dl encountered 403/IP block ({result.error_message or result.raw_error}) on {resolved_url}, "
                    "falling back to HTML scraper"
                )
                fallback_data = await self.extract_from_html(resolved_url, proxy=proxy)
                if fallback_data is not None:
                    return fallback_data

            if result.auth_required or is_403_or_ip_block:
                is_ip_block = (
                    settings.has_cookies
                    or is_403_or_ip_block
                    or (fallback_data is not None and fallback_data.error_message == "IP_BLOCKED_REDDIT")
                )
                err_code = "IP_BLOCKED_REDDIT" if is_ip_block else "AUTH_REQUIRED_REDDIT"
                return RedditData(
                    post_id="",
                    url=resolved_url,
                    title="IP заблокирован Reddit" if is_ip_block else "Требуется авторизация",
                    uploader="Reddit",
                    subreddit="",
                    media_type="photo",
                    error_message=err_code,
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
            fallback_data = await self.extract_from_html(resolved_url)
            if fallback_data and not fallback_data.error_message:
                return fallback_data
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

    async def extract_from_html(self, url: str, proxy: str | None = None) -> RedditData | None:
        """
        Native HTML / Shreddit fallback scraper.
        Handles Fastly/Reddit lightweight JS challenge, follows HTML redirects,
        and parses <shreddit-post>, <shreddit-player>, and direct media elements.
        """
        session = await http_client.get_session()
        cookies = get_cookies_for_domain("reddit.com") if settings.has_cookies else None
        effective_proxy = proxy or settings.download_proxy
        current_url = url

        for _redirect_depth in range(3):
            try:
                async with session.get(
                    current_url,
                    headers=self.headers,
                    cookies=cookies,
                    allow_redirects=True,
                    proxy=effective_proxy,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    resp_url_str = str(resp.url)
                    html_text = await resp.text()

                    # 1. Detect and solve JS challenge if present
                    if (
                        "js_challenge=1" in resp_url_str
                        or "js_challenge=1" in html_text
                        or "(async" in html_text
                        or "jsc_token" in html_text
                    ):
                        solved_html = await self._solve_js_challenge(
                            session=session,
                            html=html_text,
                            current_url=resp_url_str,
                            cookies=cookies,
                            proxy=effective_proxy,
                        )
                        if solved_html:
                            html_text = solved_html

                    # 2. Check for HTML redirects (<shreddit-redirect> or <canonical-url-updater> or <div id="shreddit-redirect">)
                    redirect_target = self._extract_html_redirect(html_text, current_url)
                    if redirect_target and redirect_target != current_url:
                        clean_redirect = normalize_reddit_url(redirect_target)
                        if clean_redirect != normalize_reddit_url(current_url):
                            logger.info(f"Following Shreddit HTML redirect: {current_url} -> {clean_redirect}")
                            current_url = clean_redirect
                            continue

                    # 3. Check for Fastly / IP block or auth requirement when <shreddit-post> is not in html
                    if "<shreddit-post" not in html_text:
                        low_html = html_text.lower()
                        is_auth = ("login" in low_html or "sign in" in low_html) and not settings.has_cookies
                        is_ip_block = (
                            "blocked by network security" in low_html
                            or "your ip address is unable to access the reddit api" in low_html
                            or "js_challenge=1" in low_html
                            or "jsc_token" in low_html
                            or resp.status == 403
                        )
                        if is_auth:
                            return RedditData(
                                post_id="",
                                url=current_url,
                                title="Требуется авторизация",
                                uploader="Reddit",
                                subreddit="",
                                media_type="photo",
                                error_message="AUTH_REQUIRED_REDDIT",
                            )
                        if is_ip_block:
                            return RedditData(
                                post_id="",
                                url=current_url,
                                title="IP-адрес заблокирован Reddit",
                                uploader="Reddit",
                                subreddit="",
                                media_type="photo",
                                error_message="IP_BLOCKED_REDDIT",
                            )

                    # 4. Parse Shreddit HTML
                    return self._parse_shreddit_html(html_text, current_url)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Error in HTML fallback scraper for {current_url}: {e}")
                return None

        return None

    async def _solve_js_challenge(
        self,
        session: aiohttp.ClientSession,
        html: str,
        current_url: str,
        cookies: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> str | None:
        """
        Solve Reddit/Fastly JS challenge:
        Extracts argument to await (async e=>e+e)("..."), concatenates string (val + val),
        and submits form or jsc_token with proper Referer to receive session cookies.
        """
        effective_proxy = proxy or settings.download_proxy
        m = re.search(
            r'(?:await\s*)?\(?\s*(?:async\s*)?(?:(?:\(?\s*[\w$]+\s*\)?\s*=>\s*(?:\{[^}]*return\s+[^}]+\}|\(?\s*[\w$]+\s*\+\s*[\w$]+\s*\)?))|function\s*[\w$]*\s*\(\s*[\w$]+\s*\)\s*\{[^}]+\})\s*\)?\s*\(\s*["\']([^"\']+)["\']\s*\)',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        token_arg = m.group(1) if m else None
        solved_token = (token_arg + token_arg) if token_arg else ""

        post_data: dict[str, str] = {}
        action_url = current_url
        method = "POST"

        # Search for the specific challenge form (matching jsc_token or js_challenge)
        selected_form: tuple[str, str] | None = None
        for form in re.finditer(r'<form\b([^>]*)>(.*?)</form>', html, re.DOTALL | re.IGNORECASE):
            attrs = form.group(1)
            body = form.group(2)
            if "jsc_token" in body or "js_challenge" in attrs or "js_challenge" in body:
                selected_form = (attrs, body)
                break

        if not selected_form:
            single_form = re.search(r'<form\b([^>]*)>(.*?)</form>', html, re.DOTALL | re.IGNORECASE)
            if single_form:
                selected_form = (single_form.group(1), single_form.group(2))

        if selected_form:
            form_attrs, form_body = selected_form
            act_m = re.search(r'\baction=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', form_attrs, re.IGNORECASE)
            if act_m:
                raw_act = act_m.group(1) if act_m.group(1) is not None else act_m.group(2)
                action_url = urljoin(current_url, html_lib.unescape(raw_act))
            meth_m = re.search(r'\bmethod=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', form_attrs, re.IGNORECASE)
            if meth_m:
                raw_meth = meth_m.group(1) if meth_m.group(1) is not None else meth_m.group(2)
                method = raw_meth.upper()

            for inp in re.finditer(r'<input\b[^>]*>', form_body, re.IGNORECASE):
                tag = inp.group(0)
                name_m = re.search(r'\bname=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', tag, re.IGNORECASE)
                val_m = re.search(r'\bvalue=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', tag, re.IGNORECASE)
                if name_m:
                    name = name_m.group(1) if name_m.group(1) is not None else name_m.group(2)
                    val = (val_m.group(1) if val_m.group(1) is not None else val_m.group(2)) if val_m else ""
                    if name:
                        post_data[name] = val

        if not post_data:
            jsc_inp_m = re.search(r'<input\b[^>]*\bname=["\']?jsc_token["\']?[^>]*>', html, re.IGNORECASE)
            if jsc_inp_m:
                val_m = re.search(r'\bvalue=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', jsc_inp_m.group(0), re.IGNORECASE)
                if val_m:
                    post_data["jsc_token"] = val_m.group(1) if val_m.group(1) is not None else (val_m.group(2) or "")

        if solved_token:
            post_data["jsc_token"] = solved_token
        elif "jsc_token" not in post_data:
            return None

        if "js_challenge=1" not in action_url:
            separator = "&" if "?" in action_url else "?"
            action_url = f"{action_url}{separator}js_challenge=1"

        submit_headers = {
            **self.headers,
            "Referer": current_url,
            "Origin": f"{urlparse(current_url).scheme}://{urlparse(current_url).netloc}",
        }

        try:
            if method == "GET":
                req_coro = session.get(
                    action_url,
                    params=post_data,
                    headers=submit_headers,
                    cookies=cookies,
                    allow_redirects=True,
                    proxy=effective_proxy,
                    timeout=aiohttp.ClientTimeout(total=15),
                )
            else:
                req_coro = session.post(
                    action_url,
                    data=post_data,
                    headers=submit_headers,
                    cookies=cookies,
                    allow_redirects=True,
                    proxy=effective_proxy,
                    timeout=aiohttp.ClientTimeout(total=15),
                )

            async with req_coro as resp:
                solved_text = await resp.text()
                if "<shreddit-post" in solved_text:
                    return solved_text

                clean_refetch_url = re.sub(r'([?&])js_challenge=1(&|$)', r'\1', current_url).rstrip('?&')
                async with session.get(
                    clean_refetch_url,
                    headers=self.headers,
                    cookies=cookies,
                    allow_redirects=True,
                    proxy=effective_proxy,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as refetch_resp:
                    return await refetch_resp.text()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to submit Reddit JS challenge: {e}")
            return None

    def _extract_html_redirect(self, html: str, current_url: str) -> str | None:
        """
        Extract redirect URL from <shreddit-redirect>, <canonical-url-updater>, or <div id="shreddit-redirect">.
        """
        redirect_tag_m = re.search(
            r"<(?:shreddit-redirect|canonical-url-updater|[a-zA-Z0-9_-]+\b[^>]*\bid=(?:[\"']?shreddit-redirect[\"']?))\b([^>]*)>",
            html,
            re.IGNORECASE,
        )
        if redirect_tag_m:
            tag_attrs = redirect_tag_m.group(1)
            attr_m = re.search(
                r'(?:data-url|redirect-url|canonical-url|href|url|value)=(?:["\']([^"\']*)["\']|([^"\'\s>]+))',
                tag_attrs,
                re.IGNORECASE,
            )
            if attr_m:
                raw_val = attr_m.group(1) if attr_m.group(1) is not None else attr_m.group(2)
                return urljoin(current_url, html_lib.unescape(raw_val))

        div_m = re.search(
            r'<div\b[^>]*\bid=(?:["\']?shreddit-redirect[\'"]?)[^>]*>(.*?)</div>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if div_m:
            a_m = re.search(
                r'<a\b[^>]*\bhref=(?:["\']([^"\']*)["\']|([^"\'\s>]+))',
                div_m.group(1),
                re.IGNORECASE,
            )
            if a_m:
                raw_val = a_m.group(1) if a_m.group(1) is not None else a_m.group(2)
                return urljoin(current_url, html_lib.unescape(raw_val))

        return None

    def _parse_shreddit_html(self, html: str, page_url: str) -> RedditData | None:
        """
        Parse <shreddit-post> attributes and media elements from HTML.
        """
        low_html = html.lower()
        if (
            "blocked by network security" in low_html
            or "your ip address is unable to access the reddit api" in low_html
        ):
            return RedditData(
                post_id="",
                url=page_url,
                title="IP-адрес заблокирован Reddit",
                uploader="Reddit",
                subreddit="",
                media_type="photo",
                error_message="IP_BLOCKED_REDDIT",
            )

        post_m = re.search(r'<shreddit-post\b([^>]*)>(.*?)</shreddit-post>', html, re.DOTALL | re.IGNORECASE)
        if post_m:
            post_attrs = post_m.group(1)
            post_inner_html = post_m.group(2)
        else:
            post_start_m = re.search(r'<shreddit-post\b([^>]*)>', html, re.DOTALL | re.IGNORECASE)
            if not post_start_m:
                if "login" in low_html and ("auth" in low_html or "sign in" in low_html or "403" in low_html):
                    return RedditData(
                        post_id="",
                        url=page_url,
                        title="Требуется авторизация",
                        uploader="Reddit",
                        subreddit="",
                        media_type="photo",
                        error_message="AUTH_REQUIRED_REDDIT",
                    )
                return None
            post_attrs = post_start_m.group(1)
            end_idx = len(html)
            for marker in ["<shreddit-comment-tree", "<aside", "<footer", 'id="sidebar', 'class="sidebar']:
                idx = html.find(marker, post_start_m.end())
                if idx != -1 and idx < end_idx:
                    end_idx = idx
            post_inner_html = html[post_start_m.end():end_idx]

        def get_attr(name: str) -> str:
            m = re.search(rf'\b{name}=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', post_attrs, re.IGNORECASE)
            return (m.group(1) if m.group(1) is not None else m.group(2)) if m else ""

        title = html_lib.unescape(get_attr("post-title") or "")
        if not title:
            t_m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
            if t_m:
                title = html_lib.unescape(t_m.group(1).split(" : ")[0].strip())
        if not title:
            title = "Reddit Post"

        author = get_attr("author") or "reddit_user"
        sub_prefixed = get_attr("subreddit-prefixed-name")
        subreddit = sub_prefixed.removeprefix("r/") if sub_prefixed else get_attr("subreddit-name").removeprefix("r/")
        if not subreddit:
            m_sub = re.search(r'/r/([^/]+)/', page_url)
            subreddit = m_sub.group(1) if m_sub else ""

        post_id = get_attr("id").removeprefix("t3_")
        if not post_id:
            m_id = re.search(r'/comments/([a-zA-Z0-9]+)', page_url)
            post_id = m_id.group(1) if m_id else ""

        def check_flag(attr_names: tuple[str, ...]) -> bool:
            pattern = rf'\b(?:{"|".join(attr_names)})=(?:["\']([^"\']*)["\']|([^"\'\s>]+))'
            m = re.search(pattern, post_attrs, re.IGNORECASE)
            if m:
                val = (m.group(1) if m.group(1) is not None else m.group(2)).lower()
                return val in ("true", "1", "yes")
            standalone = rf'(?:\s|^)(?:{"|".join(attr_names)})(?:\s|>|$)'
            return bool(re.search(standalone, post_attrs, re.IGNORECASE))

        post_type = get_attr("post-type").lower()
        content_href = html_lib.unescape(get_attr("content-href") or "")
        over_18 = check_flag(("nsfw", "over-18"))
        is_spoiler = check_flag(("spoiler", "is-spoiler"))
        is_self = post_type == "text" or check_flag(("is-self", "is_self"))

        is_video = False
        video_url: str | None = None
        external_url: str | None = None

        player_m = re.search(r'<shreddit-player\b([^>]*)>', html, re.DOTALL | re.IGNORECASE)
        if player_m:
            is_video = True
            player_attrs = player_m.group(1)
            pkg_m = re.search(r'\bpackaged-media-json=["\']([^"\']+)["\']', player_attrs, re.IGNORECASE)
            if pkg_m:
                with suppress(Exception):
                    pkg_data = json.loads(html_lib.unescape(pkg_m.group(1)))
                    candidate = (
                        pkg_data.get("playbackHlsUrl")
                        or pkg_data.get("playbackMp4Url")
                        or pkg_data.get("fallbackUrl")
                        or pkg_data.get("hlsUrl")
                    )
                    if isinstance(candidate, dict):
                        video_url = candidate.get("url") or candidate.get("fallbackUrl")
                    elif isinstance(candidate, str):
                        video_url = candidate
            if not video_url:
                src_m = re.search(r'\bsrc=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', player_attrs, re.IGNORECASE)
                stream_m = re.search(r'\bstream-url=(?:["\']([^"\']*)["\']|([^"\'\s>]+))', player_attrs, re.IGNORECASE)
                if src_m:
                    raw_src = src_m.group(1) if src_m.group(1) is not None else src_m.group(2)
                    video_url = html_lib.unescape(raw_src)
                elif stream_m:
                    raw_stream = stream_m.group(1) if stream_m.group(1) is not None else stream_m.group(2)
                    video_url = html_lib.unescape(raw_stream)

            if not video_url and "v.redd.it" in content_href:
                video_url = content_href

        if "v.redd.it" in content_href or content_href.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            is_video = True
            if not video_url:
                video_url = content_href

        low_href = content_href.lower()
        if any(h in low_href for h in ["redgifs.com", "imgur.com", "gfycat.com"]):
            external_url = content_href
        else:
            iframe_m = re.search(
                r'<iframe\b[^>]*\bsrc=(?:["\']([^"\']*)["\']|([^"\'\s>]+))',
                post_inner_html,
                re.IGNORECASE,
            )
            if iframe_m:
                src_val = iframe_m.group(1) if iframe_m.group(1) is not None else iframe_m.group(2)
                if any(h in src_val.lower() for h in ["redgifs.com", "imgur.com", "gfycat.com"]):
                    external_url = html_lib.unescape(src_val)

        items: list[RedditItem] = []

        if is_video:
            if video_url:
                items.append(RedditItem(url=video_url, media_type="video"))
        elif external_url or is_self:
            pass
        else:
            gallery_block_m = re.search(
                r'<gallery-carousel\b[^>]*>(.*?)</gallery-carousel>',
                post_inner_html,
                re.DOTALL | re.IGNORECASE,
            )
            if gallery_block_m:
                search_block = gallery_block_m.group(1)
                img_urls: list[str] = []
                for match in re.finditer(
                    r'(?:href|src|data-lazy-src)=(?:["\'](https?://(?:i|preview)\.redd\.it/[^"\']+)["\']|(https?://(?:i|preview)\.redd\.it/[^\s>]+))',
                    search_block,
                    re.IGNORECASE,
                ):
                    raw_u = match.group(1) if match.group(1) is not None else match.group(2)
                    raw_img = html_lib.unescape(raw_u)
                    clean_img = raw_img.split("?")[0].split("#")[0].replace("preview.redd.it", "i.redd.it")
                    if clean_img not in img_urls:
                        img_urls.append(clean_img)
                for u in img_urls:
                    items.append(RedditItem(url=u, media_type="animation" if u.lower().endswith(".gif") else "photo"))
            elif post_type == "gallery":
                img_urls = []
                for match in re.finditer(
                    r'(?:href|src|data-lazy-src)=(?:["\'](https?://(?:i|preview)\.redd\.it/[^"\']+)["\']|(https?://(?:i|preview)\.redd\.it/[^\s>]+))',
                    post_inner_html,
                    re.IGNORECASE,
                ):
                    raw_u = match.group(1) if match.group(1) is not None else match.group(2)
                    raw_img = html_lib.unescape(raw_u)
                    clean_img = raw_img.split("?")[0].split("#")[0].replace("preview.redd.it", "i.redd.it")
                    if clean_img not in img_urls:
                        img_urls.append(clean_img)
                for u in img_urls:
                    items.append(RedditItem(url=u, media_type="animation" if u.lower().endswith(".gif") else "photo"))
            elif ("i.redd.it" in content_href) or ("preview.redd.it" in content_href) or any(
                content_href.lower().split("?")[0].split("#")[0].endswith(ext)
                for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]
            ):
                clean_img = (
                    content_href.split("?")[0].split("#")[0].replace("preview.redd.it", "i.redd.it")
                    if ("preview.redd.it" in content_href or "i.redd.it" in content_href)
                    else content_href
                )
                m_type = "animation" if clean_img.lower().endswith(".gif") else "photo"
                items.append(RedditItem(url=clean_img, media_type=m_type))
            else:
                single_img_m = re.search(
                    r'<img\b[^>]*(?:src|data-lazy-src)=(?:["\'](https?://(?:i|preview)\.redd\.it/[^"\']+)["\']|(https?://(?:i|preview)\.redd\.it/[^\s>]+))',
                    post_inner_html,
                    re.IGNORECASE,
                )
                if single_img_m:
                    raw_src = single_img_m.group(1) if single_img_m.group(1) is not None else single_img_m.group(2)
                    clean_img = html_lib.unescape(raw_src).split("?")[0].split("#")[0].replace("preview.redd.it", "i.redd.it")
                    m_type = "animation" if clean_img.lower().endswith(".gif") else "photo"
                    items.append(RedditItem(url=clean_img, media_type=m_type))

        if not items and not is_video and not external_url:
            return RedditData(
                post_id=post_id,
                url=page_url,
                title=title,
                uploader=author,
                subreddit=subreddit,
                media_type="photo",
                is_self=is_self,
                over_18=over_18,
                is_spoiler=is_spoiler,
                error_message="NO_MEDIA_REDDIT",
            )

        if is_video or external_url:
            final_type = "video"
        elif len(items) > 1:
            final_type = "album"
        elif len(items) == 1:
            final_type = items[0].media_type
        else:
            final_type = "photo"

        return RedditData(
            post_id=post_id,
            url=page_url,
            title=title,
            uploader=author,
            subreddit=subreddit,
            media_type=final_type,
            items=items,
            is_video=is_video,
            video_url=video_url,
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
            clean_item_url = item.url.lower().split("?")[0].split("#")[0]
            ext = "jpg"
            if item.media_type == "animation" or clean_item_url.endswith(".gif"):
                ext = "gif"
            elif item.media_type == "video" or clean_item_url.endswith(".mp4"):
                ext = "mp4"
            elif clean_item_url.endswith(".png"):
                ext = "png"
            elif clean_item_url.endswith(".webp"):
                ext = "webp"
            batch_tasks.append((item.url, download_dir / f"{task_id}_reddit_{idx}.{ext}"))

        downloaded_files = await http_client.download_files_batch(
            batch_tasks,
            headers=self.headers,
            proxy=settings.download_proxy,
        )

        final_media_type = data.media_type
        if len(downloaded_files) == 1 and final_media_type == "album":
            final_media_type = data.items[0].media_type

        return downloaded_files, final_media_type


reddit_service = RedditService()
