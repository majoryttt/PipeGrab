import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_bin_dir = str(Path(sys.executable).parent)
_cur_path = os.environ.get("PATH", "")
_parts = _cur_path.split(os.pathsep) if _cur_path else []
if _bin_dir not in _parts:
    os.environ["PATH"] = os.pathsep.join([_bin_dir, *_parts]) if _parts else _bin_dir


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    bot_token: str = ""
    admin_id: int | None = None
    local_bot_api_url: str | None = None
    local_bot_api_server_path: str = "/var/lib/telegram-bot-api"
    telegram_proxy: str | None = None
    max_file_size_mb: int = 50
    max_playlist_items: int = 20
    max_concurrent_downloads: int = 3
    cookies_file: Path = Path("data/cookies/cookies.txt")
    downloads_dir: Path = Path("downloads")
    stats_db_path: Path = Path("data/stats.db")
    cache_db_path: Path = Path("data/cache.db")
    dump_channel_id: int | str | None = None
    inline_cache_time: int = 300
    delete_source_message: bool = True

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def has_cookies(self) -> bool:
        return bool(self.cookies_file and self.cookies_file.is_file() and self.cookies_file.stat().st_size > 0)


settings = Settings()
settings.downloads_dir.mkdir(parents=True, exist_ok=True)
settings.cookies_file.parent.mkdir(parents=True, exist_ok=True)
settings.stats_db_path.parent.mkdir(parents=True, exist_ok=True)
settings.cache_db_path.parent.mkdir(parents=True, exist_ok=True)

