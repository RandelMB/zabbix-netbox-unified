import asyncio


_interface_sync_locks: dict[int, asyncio.Lock] = {}


def interface_sync_lock(device_id: int) -> asyncio.Lock:
    lock = _interface_sync_locks.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        _interface_sync_locks[device_id] = lock
    return lock
