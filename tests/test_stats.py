import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types
from aiogram.exceptions import TelegramBadRequest

from bot.config import settings
from bot.handlers.base import cmd_stats, handle_stats_callback
from bot.keyboards.inline import get_stats_keyboard
from bot.services.stats_service import StatsService, format_size_ru


def test_format_size_ru():
    assert format_size_ru(500) == "500.0 Б"
    assert format_size_ru(1024) == "1.0 КБ"
    assert format_size_ru(1048576) == "1.0 МБ"
    assert format_size_ru(1073741824) == "1.0 ГБ"
    assert format_size_ru(1099511627776) == "1.0 ТБ"


@pytest.mark.asyncio
async def test_stats_service_record_and_get_stats(tmp_path: Path):
    db_file = tmp_path / "test_stats.db"
    service = StatsService(db_path=db_file)

    # Initially empty
    summary_empty = await service.get_stats("day")
    assert summary_empty.total_downloads == 0
    assert summary_empty.total_errors == 0
    assert summary_empty.unique_users == 0
    assert summary_empty.total_bytes == 0
    assert len(summary_empty.top_platforms) == 0

    # Record downloads
    await service.record_download(user_id=101, platform="TikTok", media_type="video", file_size=5000, status="success")
    await service.record_download(user_id=101, platform="TikTok", media_type="album", file_size=3000, status="success")
    await service.record_download(user_id=102, platform="Instagram", media_type="video", file_size=2000, status="success")
    await service.record_download(user_id=103, platform="YouTube", media_type="unknown", file_size=0, status="error")

    summary_day = await service.get_stats("day")
    assert summary_day.total_downloads == 3
    assert summary_day.total_errors == 1
    assert summary_day.unique_users == 2  # 101 and 102
    assert summary_day.total_bytes == 10000

    # Check top platforms
    assert len(summary_day.top_platforms) == 2
    assert summary_day.top_platforms[0].name == "TikTok"
    assert summary_day.top_platforms[0].count == 2
    assert pytest.approx(summary_day.top_platforms[0].percent, 0.1) == 66.7
    assert summary_day.top_platforms[1].name == "Instagram"
    assert summary_day.top_platforms[1].count == 1
    assert pytest.approx(summary_day.top_platforms[1].percent, 0.1) == 33.3

    # Check media types
    types_dict = dict(summary_day.top_media_types)
    assert types_dict.get("video") == 2
    assert types_dict.get("album") == 1


@pytest.mark.asyncio
async def test_stats_service_periods_filter(tmp_path: Path):
    db_file = tmp_path / "test_periods.db"
    service = StatsService(db_path=db_file)

    # Insert events at different time offsets manually
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        # Today
        c.execute("""
            INSERT INTO download_events (user_id, platform, media_type, file_size, status, created_at)
            VALUES (1, 'TikTok', 'video', 1000, 'success', datetime('now', '-2 hours'))
        """)
        # 3 days ago (in week, not in day)
        c.execute("""
            INSERT INTO download_events (user_id, platform, media_type, file_size, status, created_at)
            VALUES (2, 'Instagram', 'photo', 2000, 'success', datetime('now', '-3 days'))
        """)
        # 15 days ago (in month, not in week)
        c.execute("""
            INSERT INTO download_events (user_id, platform, media_type, file_size, status, created_at)
            VALUES (3, 'YouTube', 'video', 4000, 'success', datetime('now', '-15 days'))
        """)
        # 45 days ago (only in all)
        c.execute("""
            INSERT INTO download_events (user_id, platform, media_type, file_size, status, created_at)
            VALUES (4, 'Pinterest', 'photo', 8000, 'success', datetime('now', '-45 days'))
        """)
        conn.commit()

    stats_day = await service.get_stats("day")
    assert stats_day.total_downloads == 1
    assert stats_day.total_bytes == 1000

    stats_week = await service.get_stats("week")
    assert stats_week.total_downloads == 2
    assert stats_week.total_bytes == 3000

    stats_month = await service.get_stats("month")
    assert stats_month.total_downloads == 3
    assert stats_month.total_bytes == 7000

    stats_all = await service.get_stats("all")
    assert stats_all.total_downloads == 4
    assert stats_all.total_bytes == 15000


def test_format_stats_message(tmp_path: Path):
    db_file = tmp_path / "test_msg.db"
    service = StatsService(db_path=db_file)

    # Test empty message formatting
    empty_summary = service.get_stats.__wrapped__(service, "day") if hasattr(service.get_stats, "__wrapped__") else None
    # Use direct sync test helper
    import asyncio
    empty_summary = asyncio.run(service.get_stats("day"))
    msg = service.format_stats_message(empty_summary)
    assert "Панель статистики администратора" in msg
    assert "Успешно: <b>0</b>" in msg
    assert "Нет успешных скачиваний" in msg


def test_get_stats_keyboard():
    kb = get_stats_keyboard("week")
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    callback_data_list = [btn.callback_data for btn in all_buttons]

    assert "stats:day" in callback_data_list
    assert "stats:week" in callback_data_list
    assert "stats:month" in callback_data_list
    assert "stats:all" in callback_data_list
    assert "stats:refresh:week" in callback_data_list

    # Check that active button has the active marker
    week_btn = next(btn for btn in all_buttons if btn.callback_data == "stats:week")
    assert "•" in week_btn.text


@pytest.mark.asyncio
async def test_cmd_stats_access_control():
    # Non-admin user
    msg_user = MagicMock(spec=types.Message)
    msg_user.from_user = MagicMock(id=999)
    msg_user.answer = AsyncMock()

    with patch.object(settings, "admin_id", 12345):
        await cmd_stats(msg_user)
        msg_user.answer.assert_called_once_with("⛔ У вас нет доступа к этой команде.")

    # Admin user
    msg_admin = MagicMock(spec=types.Message)
    msg_admin.from_user = MagicMock(id=12345)
    msg_admin.answer = AsyncMock()

    with patch.object(settings, "admin_id", 12345):
        await cmd_stats(msg_admin)
        msg_admin.answer.assert_called_once()
        args, kwargs = msg_admin.answer.call_args
        assert "Панель статистики администратора" in args[0]
        assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_handle_stats_callback():
    # Non-admin callback
    cb_user = MagicMock(spec=types.CallbackQuery)
    cb_user.from_user = MagicMock(id=999)
    cb_user.data = "stats:week"
    cb_user.answer = AsyncMock()

    with patch.object(settings, "admin_id", 12345):
        await handle_stats_callback(cb_user)
        cb_user.answer.assert_called_once_with("⛔ У вас нет доступа к этой команде.", show_alert=True)

    # Admin callback
    msg = MagicMock(spec=types.Message)
    msg.edit_text = AsyncMock()

    cb_admin = MagicMock(spec=types.CallbackQuery)
    cb_admin.from_user = MagicMock(id=12345)
    cb_admin.data = "stats:month"
    cb_admin.message = msg
    cb_admin.answer = AsyncMock()

    with patch.object(settings, "admin_id", 12345):
        await handle_stats_callback(cb_admin)
        msg.edit_text.assert_called_once()
        args, _kwargs = msg.edit_text.call_args
        assert "Последние 30 дней" in args[0]

    # Admin callback with message not modified
    msg.edit_text.side_effect = TelegramBadRequest(method="editMessageText", message="Bad Request: message is not modified")
    cb_admin_refresh = MagicMock(spec=types.CallbackQuery)
    cb_admin_refresh.from_user = MagicMock(id=12345)
    cb_admin_refresh.data = "stats:refresh:month"
    cb_admin_refresh.message = msg
    cb_admin_refresh.answer = AsyncMock()

    with patch.object(settings, "admin_id", 12345):
        await handle_stats_callback(cb_admin_refresh)
        cb_admin_refresh.answer.assert_called_once_with("Статистика актуальна ✅")
