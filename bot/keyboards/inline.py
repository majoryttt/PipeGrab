import uuid
from typing import Dict
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# In-memory storage mapping short cache IDs to URL and original message info
# Keeps callback_data under Telegram's 64-byte limit
_URL_CACHE: Dict[str, dict] = {}
_MAX_CACHE_SIZE = 2000


def register_url(url: str, original_message_id: int | None = None) -> str:
    """Store URL and original message ID, returning an 8-character ID for callback queries."""
    if len(_URL_CACHE) > _MAX_CACHE_SIZE:
        # Prune oldest half of entries
        keys = list(_URL_CACHE.keys())[:len(_URL_CACHE) // 2]
        for k in keys:
            _URL_CACHE.pop(k, None)

    cache_id = uuid.uuid4().hex[:8]
    _URL_CACHE[cache_id] = {"url": url, "orig_msg_id": original_message_id}
    return cache_id


def get_cached_url(cache_id: str) -> str | None:
    entry = _URL_CACHE.get(cache_id)
    if isinstance(entry, dict):
        return entry.get("url")
    return entry


def get_cached_message_id(cache_id: str) -> int | None:
    entry = _URL_CACHE.get(cache_id)
    if isinstance(entry, dict):
        return entry.get("orig_msg_id")
    return None


def get_download_format_kb(url: str, original_message_id: int | None = None) -> InlineKeyboardMarkup:
    """
    Keyboard offering Video or Audio format selection.
    """
    cid = register_url(url, original_message_id=original_message_id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Скачать видео", callback_data=f"dl:video:{cid}"),
                InlineKeyboardButton(text="🎵 Скачать MP3", callback_data=f"dl:audio:{cid}"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="dl:cancel")
            ]
        ]
    )
    return keyboard


def get_playlist_kb(url: str, total_count: int, original_message_id: int | None = None) -> InlineKeyboardMarkup:
    """
    Keyboard offering options for YouTube playlists.
    """
    cid = register_url(url, original_message_id=original_message_id)
    buttons = []

    # Dynamic options based on playlist size
    row1 = []
    if total_count >= 5:
        row1.append(InlineKeyboardButton(text="📥 Первые 5", callback_data=f"pl:5:{cid}"))
    if total_count >= 10:
        row1.append(InlineKeyboardButton(text="📥 Первые 10", callback_data=f"pl:10:{cid}"))
    if total_count >= 20:
        row1.append(InlineKeyboardButton(text="📥 Первые 20", callback_data=f"pl:20:{cid}"))

    if not row1:
        row1.append(InlineKeyboardButton(text=f"📥 Скачать все ({total_count})", callback_data=f"pl:{total_count}:{cid}"))

    buttons.append(row1)

    # Audio row
    audio_count = min(10, total_count)
    buttons.append([
        InlineKeyboardButton(text=f"🎵 MP3 ({audio_count} треков)", callback_data=f"pl:a{audio_count}:{cid}")
    ])

    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="dl:cancel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
