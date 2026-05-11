import asyncio
import unittest

from app.services.sync_locks import interface_sync_lock


class InterfaceSyncLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_device_reuses_same_lock(self) -> None:
        self.assertIs(interface_sync_lock(101), interface_sync_lock(101))

    async def test_different_devices_receive_different_locks(self) -> None:
        self.assertIsNot(interface_sync_lock(101), interface_sync_lock(202))

    async def test_lock_blocks_second_worker_until_release(self) -> None:
        lock = interface_sync_lock(303)
        timeline: list[str] = []

        async def first() -> None:
            async with lock:
                timeline.append("first-acquired")
                await asyncio.sleep(0.05)
                timeline.append("first-released")

        async def second() -> None:
            await asyncio.sleep(0.01)
            async with lock:
                timeline.append("second-acquired")

        await asyncio.gather(first(), second())
        self.assertEqual(timeline, ["first-acquired", "first-released", "second-acquired"])
