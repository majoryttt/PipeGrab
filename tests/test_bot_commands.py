from unittest.mock import AsyncMock, patch
import pytest
from aiogram import Bot
from bot.config import settings
from bot.main import set_bot_commands


@pytest.mark.asyncio
async def test_set_bot_commands_success():
    bot = AsyncMock(spec=Bot)
    with patch.object(settings, "admin_id", None):
        await set_bot_commands(bot)

    bot.set_my_commands.assert_called_once()
    args, kwargs = bot.set_my_commands.call_args
    commands = args[0]

    command_names = [cmd.command for cmd in commands]
    assert command_names == ["start", "help", "circle"]


@pytest.mark.asyncio
async def test_set_bot_commands_with_admin():
    bot = AsyncMock(spec=Bot)
    with patch.object(settings, "admin_id", 12345):
        await set_bot_commands(bot)

    assert bot.set_my_commands.call_count == 2
    # Second call is for admin scope
    admin_args, admin_kwargs = bot.set_my_commands.call_args_list[1]
    admin_commands = admin_args[0]
    admin_command_names = [cmd.command for cmd in admin_commands]
    assert "admin" in admin_command_names
    assert "start" in admin_command_names


@pytest.mark.asyncio
async def test_set_bot_commands_handles_error():
    bot = AsyncMock(spec=Bot)
    bot.set_my_commands.side_effect = RuntimeError("Telegram API error")

    # Should not raise exception
    await set_bot_commands(bot)
    bot.set_my_commands.assert_called_once()
