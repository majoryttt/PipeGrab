from unittest.mock import AsyncMock
import pytest
from aiogram import Bot
from bot.main import set_bot_commands


@pytest.mark.asyncio
async def test_set_bot_commands_success():
    bot = AsyncMock(spec=Bot)
    await set_bot_commands(bot)

    bot.set_my_commands.assert_called_once()
    args, kwargs = bot.set_my_commands.call_args
    commands = args[0]

    command_names = [cmd.command for cmd in commands]
    assert "start" in command_names
    assert "help" in command_names
    assert "circle" in command_names
    assert "cookies" in command_names
    assert "status" in command_names


@pytest.mark.asyncio
async def test_set_bot_commands_handles_error():
    bot = AsyncMock(spec=Bot)
    bot.set_my_commands.side_effect = RuntimeError("Telegram API error")

    # Should not raise exception
    await set_bot_commands(bot)
    bot.set_my_commands.assert_called_once()
