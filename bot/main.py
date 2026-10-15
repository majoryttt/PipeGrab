import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault

from bot.config import settings
from bot.handlers.base import router as base_router
from bot.handlers.download import router as download_router
from bot.services.queue_manager import queue_manager
from bot.services.http_client import http_client
from bot.services.path_wrapper import LocalFilesPathWrapper
from bot.services.cookie_utils import ensure_cookies_file_normalized

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("tg_downloader_bot")


def build_bot() -> Bot:
    if not settings.bot_token:
        logger.error("BOT_TOKEN is not set in environment or .env file!")
        sys.exit(1)

    default_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)

    if settings.local_bot_api_url:
        logger.info(f"Connecting to Local Telegram Bot API Server at: {settings.local_bot_api_url}")
        server = TelegramAPIServer.from_base(
            settings.local_bot_api_url,
            is_local=True,
            wrap_local_file=LocalFilesPathWrapper(
                server_path=settings.local_bot_api_server_path,
                local_path=settings.downloads_dir.resolve(),
            ),
        )
        session = AiohttpSession(api=server, proxy=settings.telegram_proxy)
        return Bot(token=settings.bot_token, session=session, default=default_properties)
    elif settings.telegram_proxy:
        logger.info(f"Connecting to Telegram Bot API via proxy: {settings.telegram_proxy}")
        session = AiohttpSession(proxy=settings.telegram_proxy)
        return Bot(token=settings.bot_token, session=session, default=default_properties)
    else:
        logger.info("Using standard Telegram Bot API server (api.telegram.org)")
        return Bot(token=settings.bot_token, default=default_properties)


async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Справка и возможности"),
        BotCommand(command="circle", description="Превратить видео в кружок"),
        BotCommand(command="cookies", description="Статус и настройка Cookies"),
        BotCommand(command="status", description="Состояние сервера и лимиты"),
    ]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        logger.info("Bot commands registered successfully.")
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")


async def main():
    logger.info("Starting Telegram Downloader Bot...")
    bot = build_bot()
    dp = Dispatcher()

    # Register routers
    dp.include_router(base_router)
    dp.include_router(download_router)

    # Normalize cookies if present
    if settings.has_cookies:
        ensure_cookies_file_normalized(settings.cookies_file)

    # Start background cleanup of orphaned files
    cleanup_task = asyncio.create_task(queue_manager.start_periodic_cleanup(interval_seconds=600))

    try:
        # Drop pending updates to prevent flooding on start
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logger.info(f"Bot authorized successfully as @{me.username} (ID: {me.id})")

        # Set commands menu for autocomplete on '/'
        await set_bot_commands(bot)

        logger.info("Ready to receive download requests!")

        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        await http_client.close()
        await bot.session.close()
        logger.info("Bot shut down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
