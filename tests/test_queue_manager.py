import pytest
import asyncio
from bot.services.queue_manager import QueueManager


@pytest.mark.asyncio
async def test_queue_manager_user_concurrency():
    qm = QueueManager()
    user_id = 99999

    assert await qm.can_user_download(user_id) is True

    async with qm.acquire(user_id):
        assert await qm.can_user_download(user_id) is False

        # Attempting to acquire again for same user should raise ValueError
        with pytest.raises(ValueError):
            async with qm.acquire(user_id):
                pass

    # After exit, user should be free again
    assert await qm.can_user_download(user_id) is True
