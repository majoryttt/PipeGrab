from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    bot_token: str = ""
    admin_id: Optional[int] = None
    local_bot_api_url: Optional[str] = None
    local_bot_api_server_path: str = "/var/lib/telegram-bot-api"
    telegram_proxy: Optional[str] = None
    max_file_size_mb: int = 50
    max_playlist_items: int = 20
    max_concurrent_downloads: int = 3
    cookies_file: Path = Path("data/cookies/cookies.txt")
    downloads_dir: Path = Path("downloads")
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

