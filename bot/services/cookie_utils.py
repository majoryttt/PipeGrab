"""
Cookie utility functions for PipeGrab.
Supports parsing, validating, detecting services, and Netscape cookie sanitization.
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
    Read cookies_file, apply Netscape format sanitization if needed, and write back.
    Returns True if file was modified.
    """
    if not cookies_file or not cookies_file.is_file():
        return False

    try:
        original = cookies_file.read_text(encoding="utf-8", errors="ignore")
        sanitized = sanitize_netscape_content(original)
        if sanitized != original:
            cookies_file.write_text(sanitized, encoding="utf-8")
            logger.info(f"Sanitized cookies in {cookies_file}")
            return True
    except Exception as e:
        logger.warning(f"Failed to sanitize cookies file {cookies_file}: {e}")

    return False
