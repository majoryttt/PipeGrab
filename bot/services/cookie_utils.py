"""
Cookie utility functions for PipeGrab.
Supports parsing, validating, detecting services, and cross-domain VK cookie normalization.
"""
import logging
from pathlib import Path
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


def validate_netscape_cookies(content: str) -> bool:
    """
    Validate whether the provided string follows the Netscape cookies.txt format.
    """
    if not content or not content.strip():
        return False

    if "# Netscape HTTP Cookie File" in content or "# HTTP Cookie File" in content:
        return True

    lines = content.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) >= 7:
            return True

    return False


def parse_cookie_services(content: str) -> List[str]:
    """
    Detect configured services and authentication status in cookie content.
    """
    services: List[str] = []
    if not content:
        return services

    # Instagram
    if "instagram.com" in content:
        has_session = "sessionid" in content
        services.append(f"Instagram ({'найден sessionid ✅' if has_session else 'нет sessionid ⚠️'})")

    # VK (ВКонтакте)
    if any(d in content for d in ["vk.com", "vk.ru", "vkvideo.ru"]):
        has_vk_auth = any(k in content for k in ["remixsid", "remixnsid", "remixdsid"])
        services.append(f"ВКонтакте ({'найден remixsid ✅' if has_vk_auth else 'нет remixsid ⚠️'})")

    # YouTube / Google
    if "youtube.com" in content or "google.com" in content:
        services.append("YouTube / Google ✅")

    # TikTok
    if "tiktok.com" in content:
        services.append("TikTok ✅")

    # Pinterest
    if "pinterest.com" in content:
        services.append("Pinterest ✅")

    return services


def sanitize_netscape_content(content: str) -> str:
    """
    Ensure all lines in Netscape cookies file strictly adhere to Netscape specification:
    Column 2 (flag) must be 'TRUE' if the domain begins with a dot (subdomain matching allowed),
    and 'FALSE' if host-only.
    Python's http.cookiejar asserts `assert domain_specified == initial_dot` and
    fails with an AssertionError / LoadError if this invariant is broken.
    """
    if not content:
        return content

    cleaned = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            cleaned.append(line)
            continue
        parts = stripped.split("\t")
        if len(parts) >= 7:
            domain = parts[0]
            # Enforce domain_specified == initial_dot
            parts[1] = "TRUE" if domain.startswith(".") else "FALSE"
            cleaned.append("\t".join(parts))
        else:
            cleaned.append(line)

    return "\n".join(cleaned) + ("\n" if content.endswith("\n") else "")


def normalize_vk_cookies_content(content: str) -> str:
    """
    Ensure essential VK authentication cookies (remixsid, remixnsid, remixdsid)
    are cleaned, deduplicated, and synchronized across .vk.com, .vk.ru, and .vkvideo.ru.

    If the file contains multiple or conflicting sessions (e.g. an old session on vk.com
    and a fresh session on vk.ru, or duplicate host-only vs domain-level cookies),
    this extracts the freshest session (highest expires timestamp or latest in file)
    and enforces a single consistent auth cookie per domain, removing all stale duplicates.
    """
    if not content:
        return content

    target_vk_domains = [".vk.com", ".vk.ru", ".vkvideo.ru"]
    auth_cookie_names = {"remixsid", "remixnsid", "remixdsid"}

    freshest_auth: Dict[str, Tuple[int, int, str, str, str]] = {}
    preserved_lines: List[str] = []

    for idx, line in enumerate(content.splitlines()):
        stripped = line.strip()
        if stripped == "# PipeGrab Normalized VK Authentication":
            continue
        if not stripped or stripped.startswith("#"):
            preserved_lines.append(line)
            continue
        parts = stripped.split("\t")
        if len(parts) >= 7:
            domain, flag, path, secure, expires_str, name, value = parts[:7]
            domain_clean = domain.lower()

            # Remove browser redirect cookies that can break yt-dlp on vk.com
            if name == "REDIRECT_TO_VK_RU":
                continue

            if any(vk_d in domain_clean for vk_d in ["vk.com", "vk.ru", "vkvideo.ru"]):
                if name in auth_cookie_names and value and value != '""':
                    try:
                        exp = int(expires_str)
                    except ValueError:
                        exp = 0
                    if (
                        name not in freshest_auth
                        or exp > freshest_auth[name][0]
                        or (exp == freshest_auth[name][0] and idx >= freshest_auth[name][1])
                    ):
                        freshest_auth[name] = (exp, idx, path, secure, value)
                    # Filter out old/conflicting auth lines - clean ones will be injected
                    continue

            # Ensure domain_specified == initial_dot for all preserved lines
            parts[1] = "TRUE" if domain.startswith(".") else "FALSE"
            preserved_lines.append("\t".join(parts))
        else:
            preserved_lines.append(line)

    new_auth_lines: List[str] = []
    for name in sorted(freshest_auth.keys()):
        exp, _, path, secure, value = freshest_auth[name]
        for dom in target_vk_domains:
            new_auth_lines.append(f"{dom}\tTRUE\t{path}\t{secure}\t{exp}\t{name}\t{value}")

    while preserved_lines and not preserved_lines[-1].strip():
        preserved_lines.pop()

    result = "\n".join(preserved_lines)
    if new_auth_lines:
        prefix = "\n\n" if result else ""
        result = result + prefix + "# PipeGrab Normalized VK Authentication\n" + "\n".join(new_auth_lines) + "\n"

    return result


def extract_cookies_for_domains(content: str, target_domains: List[str]) -> Dict[str, str]:
    """
    Extract a dictionary of {cookie_name: cookie_value} for given domain suffixes.
    """
    cookies: Dict[str, str] = {}
    if not content:
        return cookies

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) >= 7:
            domain, _, _, _, _, name, value = parts[:7]
            domain_clean = domain.lower()
            if any(target in domain_clean for target in target_domains):
                cookies[name] = value

    return cookies


def ensure_cookies_file_normalized(cookies_file: Path) -> bool:
    """
    Read cookies_file, apply VK normalization if needed, and write back.
    Returns True if file was modified.
    """
    if not cookies_file or not cookies_file.is_file():
        return False

    try:
        original = cookies_file.read_text(encoding="utf-8", errors="ignore")
        normalized = normalize_vk_cookies_content(original)
        if normalized != original:
            cookies_file.write_text(normalized, encoding="utf-8")
            logger.info(f"Normalized VK cookies in {cookies_file}")
            return True
    except Exception as e:
        logger.warning(f"Failed to normalize cookies file {cookies_file}: {e}")

    return False
