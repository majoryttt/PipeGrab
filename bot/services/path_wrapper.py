from pathlib import Path
from typing import Union
from aiogram.client.telegram import FilesPathWrapper


class LocalFilesPathWrapper(FilesPathWrapper):
    """
    Translates file paths between Local Telegram Bot API server filesystem
    (e.g., /var/lib/telegram-bot-api) and the bot container/filesystem
    (e.g., /app/downloads).
    """

    def __init__(self, server_path: Union[Path, str], local_path: Union[Path, str]) -> None:
        self.server_path = Path(server_path)
        self.local_path = Path(local_path).resolve()

    def to_local(self, path: Union[Path, str]) -> Path:
        p = Path(path)
        try:
            if p.is_relative_to(self.server_path):
                rel = p.relative_to(self.server_path)
                return self.local_path / rel
        except (ValueError, TypeError, AttributeError):
            pass
        if not p.is_absolute():
            return self.local_path / p
        return p

    def to_server(self, path: Union[Path, str]) -> Path:
        p = Path(path)
        try:
            if p.is_relative_to(self.local_path):
                rel = p.relative_to(self.local_path)
                return self.server_path / rel
        except (ValueError, TypeError, AttributeError):
            pass
        return p
