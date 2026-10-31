import asyncio
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bot.config import settings
from bot.services.queue_manager import queue_manager

logger = logging.getLogger(__name__)


def format_size_ru(bytes_num: float) -> str:
    """Format bytes into Russian units (Б, КБ, МБ, ГБ, ТБ)."""
    val = float(bytes_num)
    for unit in ["Б", "КБ", "МБ", "ГБ"]:
        if val < 1024.0:
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} ТБ"


@dataclass
class PlatformStat:
    name: str
    count: int
    percent: float


@dataclass
class StatsSummary:
    period: str
    period_title: str
    total_downloads: int
    total_errors: int
    unique_users: int
    total_bytes: int
    top_platforms: list[PlatformStat]
    top_media_types: list[tuple[str, int]]
    # System metrics
    free_disk_gb: int
    total_disk_gb: int
    active_downloads: int
    bot_api_mode: str
    has_cookies: bool


class StatsService:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.stats_db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite tables and indexes."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_events_created_at ON download_events(created_at);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_events_platform ON download_events(platform);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_events_user_id ON download_events(user_id);")
            conn.commit()

    async def record_download(
        self,
        user_id: int,
        platform: str,
        media_type: str,
        file_size: int = 0,
        status: str = "success"
    ) -> None:
        """Record a single download event asynchronously."""
        def _sync_insert() -> None:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO download_events (user_id, platform, media_type, file_size, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, platform, media_type, file_size, status)
                )
                conn.commit()

        try:
            await asyncio.to_thread(_sync_insert)
        except Exception as e:
            logger.error(f"Failed to record download stat: {e}")

    async def get_stats(self, period: str = "day") -> StatsSummary:
        """Calculate statistics for the requested period ('day', 'week', 'month', 'all')."""
        def _sync_query() -> StatsSummary:
            if period == "day":
                period_title = "📅 Последние 24 часа"
                where_time = "created_at >= datetime('now', '-1 day')"
            elif period == "week":
                period_title = "📆 Последние 7 дней"
                where_time = "created_at >= datetime('now', '-7 days')"
            elif period == "month":
                period_title = "🗓 Последние 30 дней"
                where_time = "created_at >= datetime('now', '-30 days')"
            else:  # all
                period_title = "♾ Всё время"
                where_time = "1=1"

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Total successful
                cursor.execute(f"SELECT COUNT(*) FROM download_events WHERE status = 'success' AND {where_time}")
                row = cursor.fetchone()
                total_downloads = row[0] if row else 0

                # Total errors
                cursor.execute(f"SELECT COUNT(*) FROM download_events WHERE status = 'error' AND {where_time}")
                row = cursor.fetchone()
                total_errors = row[0] if row else 0

                # Unique users
                cursor.execute(f"SELECT COUNT(DISTINCT user_id) FROM download_events WHERE status = 'success' AND {where_time}")
                row = cursor.fetchone()
                unique_users = row[0] if row else 0

                # Total bytes
                cursor.execute(f"SELECT COALESCE(SUM(file_size), 0) FROM download_events WHERE status = 'success' AND {where_time}")
                row = cursor.fetchone()
                total_bytes = row[0] if row else 0

                # Top platforms
                cursor.execute(f"""
                    SELECT platform, COUNT(*) as cnt
                    FROM download_events
                    WHERE status = 'success' AND {where_time}
                    GROUP BY platform
                    ORDER BY cnt DESC
                """)
                platform_rows = cursor.fetchall()
                top_platforms: list[PlatformStat] = []
                for p_name, count in platform_rows:
                    percent = (count / total_downloads * 100.0) if total_downloads > 0 else 0.0
                    top_platforms.append(PlatformStat(name=p_name, count=count, percent=percent))

                # Media types
                cursor.execute(f"""
                    SELECT media_type, COUNT(*) as cnt
                    FROM download_events
                    WHERE status = 'success' AND {where_time}
                    GROUP BY media_type
                    ORDER BY cnt DESC
                """)
                media_rows = cursor.fetchall()
                top_media_types = [(row[0], row[1]) for row in media_rows]

            # System metrics
            total_disk, _, free_disk = shutil.disk_usage(settings.downloads_dir)
            total_disk_gb = total_disk // (1024 * 1024 * 1024)
            free_disk_gb = free_disk // (1024 * 1024 * 1024)
            active_downloads = len(queue_manager._active_users)
            bot_api_mode = "Локальный (до 2 ГБ)" if settings.local_bot_api_url else "Стандартный Bot API (до 50 МБ)"

            return StatsSummary(
                period=period,
                period_title=period_title,
                total_downloads=total_downloads,
                total_errors=total_errors,
                unique_users=unique_users,
                total_bytes=total_bytes,
                top_platforms=top_platforms,
                top_media_types=top_media_types,
                free_disk_gb=free_disk_gb,
                total_disk_gb=total_disk_gb,
                active_downloads=active_downloads,
                bot_api_mode=bot_api_mode,
                has_cookies=settings.has_cookies,
            )

        return await asyncio.to_thread(_sync_query)

    def format_stats_message(self, summary: StatsSummary) -> str:
        """Format StatsSummary into an HTML-formatted message for Telegram."""
        platform_icons = {
            "TikTok": "🎵",
            "Instagram": "📸",
            "YouTube": "🎬",
            "Twitter/X": "🐦",
            "Pinterest": "📌",
            "Reddit": "🤖",
        }
        if summary.top_platforms:
            platform_lines = []
            for p in summary.top_platforms:
                icon = platform_icons.get(p.name, "🌐")
                platform_lines.append(f"• {icon} <b>{p.name}</b>: {p.count} ({p.percent:.1f}%)")
            platforms_text = "\n".join(platform_lines)
        else:
            platforms_text = "• <i>Нет успешных скачиваний</i>"

        media_labels = {
            "video": "📹 Видео",
            "audio": "🎵 Аудио",
            "photo": "🖼 Фото",
            "album": "📚 Альбом / Карусель",
            "animation": "🎞 GIF-анимация",
        }
        if summary.top_media_types:
            media_lines = []
            for m_type, count in summary.top_media_types:
                label = media_labels.get(m_type, f"📦 {m_type}")
                media_lines.append(f"• {label}: <b>{count}</b>")
            media_text = "\n".join(media_lines)
        else:
            media_text = "• <i>Нет данных</i>"

        cookies_status = "Подключены ✅" if summary.has_cookies else "Отсутствуют ⚠️"
        size_str = format_size_ru(summary.total_bytes)

        return (
            "📊 <b>Панель статистики администратора</b>\n"
            f"<b>Период:</b> {summary.period_title}\n\n"
            "📥 <b>Загрузки:</b>\n"
            f"• Успешно: <b>{summary.total_downloads}</b>\n"
            f"• Ошибок: <b>{summary.total_errors}</b>\n"
            f"• Уникальных пользователей: <b>{summary.unique_users}</b>\n"
            f"• Общий объем: <b>{size_str}</b>\n\n"
            "🏆 <b>Топ платформ:</b>\n"
            f"{platforms_text}\n\n"
            "📦 <b>По типам медиа:</b>\n"
            f"{media_text}\n\n"
            "🖥 <b>Состояние сервера:</b>\n"
            f"• <b>Bot API:</b> {summary.bot_api_mode}\n"
            f"• <b>Диск:</b> свободно {summary.free_disk_gb} ГБ / {summary.total_disk_gb} ГБ\n"
            f"• <b>Активных задач:</b> {summary.active_downloads}\n"
            f"• <b>Cookies:</b> {cookies_status}"
        )


stats_service = StatsService()
