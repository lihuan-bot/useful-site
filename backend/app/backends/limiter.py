# Author: lihuan
# Date: 2026-08-16 22:34:32
# LastEditors: lihuan
# LastEditTime: 2026-08-17 14:17:22
# Email: 17719495105@163.com
"""Per-user concurrency limiter (in-memory, single-process).

One agent run per user at a time: sandbox acquisition is the expensive
resource, and the per-user limit also prevents lost-update races on the
``/files/`` object-storage area.

with multiple uvicorn workers each worker keeps its own
counters. Enforce at the process level by running one worker, or move to
``pg_try_advisory_lock(hashtext(user_id))`` for cross-worker strictness.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict

_MAX_TRACKED_USERS = 10_000


class UserLimiter:
    """LRU map of user id → asyncio.Semaphore(max_per_user)."""

    def __init__(self, max_per_user: int = 1) -> None:
        self._max = max_per_user
        self._sems: OrderedDict[str, asyncio.Semaphore] = OrderedDict()
        self._guard = asyncio.Lock()

    async def try_acquire(self, user_id: str) -> bool:
        # Everything under the guard: checking locked() and acquiring must be
        # atomic, otherwise a concurrent request would block on acquire()
        # instead of failing fast with 429.
        async with self._guard:
            sem = self._sems.get(user_id)
            if sem is None:
                sem = asyncio.Semaphore(self._max)
                self._sems[user_id] = sem
            self._sems.move_to_end(user_id)
            if len(self._sems) > _MAX_TRACKED_USERS:
                self._sems.popitem(last=False)
            if sem.locked():
                return False
            await sem.acquire()  # cannot block: we hold the guard and it is not locked
            return True

    def release(self, user_id: str) -> None:
        sem = self._sems.get(user_id)
        if sem is not None:
            sem.release()
