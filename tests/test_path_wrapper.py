from pathlib import Path
from bot.services.path_wrapper import LocalFilesPathWrapper


def test_local_files_path_wrapper():
    server_path = Path("/var/lib/telegram-bot-api")
    local_path = Path("/app/downloads")
    wrapper = LocalFilesPathWrapper(server_path=server_path, local_path=local_path)

    # 1. Translate server path to local path
    server_file = "/var/lib/telegram-bot-api/8926813381:AAH7JH7BJFL4V5Oe4ZTaADUZl2JvObzSIQg/documents/file_6.txt"
    local_file = wrapper.to_local(server_file)
    assert local_file == Path("/app/downloads/8926813381:AAH7JH7BJFL4V5Oe4ZTaADUZl2JvObzSIQg/documents/file_6.txt")

    # 2. Relative path fallback
    rel_file = "documents/file_6.txt"
    assert wrapper.to_local(rel_file) == Path("/app/downloads/documents/file_6.txt")

    # 3. Already local or unrelated path
    unrelated = "/etc/hosts"
    assert wrapper.to_local(unrelated) == Path("/etc/hosts")

    # 4. to_server translation
    bot_download = "/app/downloads/test_video.mp4"
    assert wrapper.to_server(bot_download) == Path("/var/lib/telegram-bot-api/test_video.mp4")

    # 5. to_server unrelated
    assert wrapper.to_server(unrelated) == Path("/etc/hosts")
