"""Redis-backed stream coordination (single implementation, Redis required).

All chat-streaming coordination goes through this store so the endpoints
and the detached producers work identically from any uvicorn worker.
There is deliberately no in-memory fallback: Redis is mandatory (fail fast
at startup via ``ping``), which keeps this one implementation exercised on
every run instead of two drifting implementations.

Redis layout (prefix ``stream``):

- ``stream:lock:{conv}``    SET NX EX 120 — single-flight reservation, value
                            = user id; the producer's heartbeat refreshes it.
- ``stream:events:{conv}``  Stream, MAXLEN ~500 — event log; each publish
                            refreshes a 600s EXPIRE. Last event is always
                            ``STREAM_END`` when the producer finished.
- ``stream:stopreq:{conv}`` SET EX 60 — covers a /stop racing producer start.
- ``stream:stop:{conv}``    pub/sub channel — live stop signal.
- ``stream:user:{uid}``     ZSET score=epoch member=conv — per-user active
                            count; check/prune/add runs in one Lua script.
- ``stream:cleanup:lock``   SET NX EX 300 — only one worker sweeps orphan
                            sandboxes at startup.
- ``stream:pool:workers``   ZSET score=epoch member=worker_id — pool
                            liveness leases. A worker registers at startup
                            and refreshes every 30s; the orphan sweep runs
                            only when no live worker exists (prune window
                            60s), so staggered/rolling starts never destroy
                            a running worker's pre-warmed sandboxes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

STREAM_END = "__stream_end__"

KEEPALIVE_SECONDS = 15.0
KEEPALIVE_MS = int(KEEPALIVE_SECONDS * 1000)

LOCK_TTL_SECONDS = 120       # single-flight reservation; producer heartbeats refresh it
EVENTS_TTL_SECONDS = 600     # stale event logs after producer end/crash
USER_ACTIVE_TTL_SECONDS = 7200
USER_ACTIVE_MAX_AGE_SECONDS = 7200
MAX_REPLAY_EVENTS = 500

# Atomic per-user cap: prune stale entries, count, admit or reject.
_USER_ACQUIRE_LUA = """
local key = KEYS[1]
redis.call('ZREMRANGEBYSCORE', key, 0, tonumber(ARGV[1]) - tonumber(ARGV[2]))
if redis.call('ZCARD', key) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', key, ARGV[1], ARGV[4])
redis.call('EXPIRE', key, ARGV[5])
return 1
"""


class StreamStore:
    """Redis-backed store — safe across multiple uvicorn workers.

    All methods are coroutines except ``follow``, which is an async
    generator yielding SSE strings (with embedded keepalives).
    """

    def __init__(self, redis_url: str, prefix: str = "stream") -> None:
        self._url = redis_url
        self._prefix = prefix
        self._r = aioredis.Redis.from_url(redis_url)
        self._acquire_script = self._r.register_script(_USER_ACQUIRE_LUA)

    def _k(self, kind: str, key: str) -> str:
        return f"{self._prefix}:{kind}:{key}"

    async def ping(self) -> None:
        """Fail fast at startup on misconfigured Redis."""
        await self._r.ping()

    async def close(self) -> None:
        await self._r.aclose()

    # -- lifecycle -----------------------------------------------------

    async def reserve(self, conv_key: str, user_key: str) -> bool:
        """Single-flight reservation. ``False`` → another producer owns it."""
        return bool(await self._r.set(
            self._k("lock", conv_key), user_key, nx=True, ex=LOCK_TTL_SECONDS,
        ))

    async def release(self, conv_key: str) -> None:
        await self._r.delete(self._k("lock", conv_key))

    async def heartbeat(self, conv_key: str) -> None:
        """Refresh the reservation TTL and the per-user liveness entry."""
        lock_key = self._k("lock", conv_key)
        user_key = await self._r.get(lock_key)
        if user_key is None:
            return  # producer already released
        user_key = user_key.decode()
        now = time.time()
        await self._r.expire(lock_key, LOCK_TTL_SECONDS)
        user_key_zset = self._k("user", user_key)
        await self._r.zadd(user_key_zset, {conv_key: now})
        await self._r.expire(user_key_zset, USER_ACTIVE_TTL_SECONDS)

    # -- events --------------------------------------------------------

    async def publish(self, conv_key: str, event: str) -> None:
        key = self._k("events", conv_key)
        pipe = self._r.pipeline()
        pipe.xadd(key, {"e": event.encode()}, maxlen=MAX_REPLAY_EVENTS, approximate=True)
        pipe.expire(key, EVENTS_TTL_SECONDS)
        await pipe.execute()

    async def follow(self, conv_key: str) -> AsyncIterator[str]:
        """Replay the stream from the beginning, then follow live events.

        Yields SSE strings; on producer death (lock expiry) the iterator
        ends quietly. Uses a dedicated Redis connection so cancelling the
        generator (client disconnect) closes a socket we don't share.
        """
        key = self._k("events", conv_key)
        client = aioredis.Redis.from_url(self._url)
        cursor: bytes | str = b"0-0"
        try:
            res = await client.xread({key: b"0-0"}, count=MAX_REPLAY_EVENTS)
            for _name, entries in res or []:
                for msg_id, fields in entries:
                    raw = fields.get(b"e")
                    if raw is None:
                        continue
                    cursor = msg_id
                    if raw == STREAM_END.encode():
                        return
                    yield raw.decode("utf-8")
            while True:
                res = await client.xread({key: cursor}, count=100, block=KEEPALIVE_MS)
                if not res:
                    yield ": keepalive\n\n"
                    if not await self.is_active(conv_key):
                        # Producer gone (crash) without an end marker: stop.
                        return
                    continue
                for _name, entries in res:
                    for msg_id, fields in entries:
                        raw = fields.get(b"e")
                        if raw is None:
                            continue
                        cursor = msg_id
                        if raw == STREAM_END.encode():
                            return
                        yield raw.decode("utf-8")
        finally:
            await client.aclose()

    async def state(self, conv_key: str) -> str:
        """``'active'`` (event log exists) | ``'pending'`` (reserved) | ``'inactive'``."""
        if await self._r.exists(self._k("events", conv_key)):
            return "active"
        if await self._r.exists(self._k("lock", conv_key)):
            return "pending"
        return "inactive"

    async def is_active(self, conv_key: str) -> bool:
        return bool(await self._r.exists(self._k("lock", conv_key)))

    # -- control -------------------------------------------------------

    async def request_stop(self, conv_key: str) -> None:
        await self._r.publish(self._k("stop", conv_key), "stop")
        # Covers a stop that races the producer's subscribe.
        await self._r.set(self._k("stopreq", conv_key), "1", ex=60)

    async def wait_stop(self, conv_key: str) -> None:
        # Subscribe first so a publish between the stopreq check and the
        # subscribe is still seen.
        pubsub = self._r.pubsub()
        await pubsub.subscribe(self._k("stop", conv_key))
        try:
            if await self._r.delete(self._k("stopreq", conv_key)):
                return  # stop requested before we subscribed
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is not None and msg.get("type") == "message":
                    return
        finally:
            await pubsub.aclose()

    # -- per-user concurrency ------------------------------------------

    async def user_acquire(self, user_key: str, conv_key: str, cap: int) -> bool:
        ok = await self._acquire_script(
            keys=[self._k("user", user_key)],
            args=[
                time.time(),
                USER_ACTIVE_MAX_AGE_SECONDS * 1000,
                cap,
                conv_key,
                USER_ACTIVE_TTL_SECONDS,
            ],
        )
        return bool(ok)

    async def user_release(self, user_key: str, conv_key: str) -> None:
        await self._r.zrem(self._k("user", user_key), conv_key)

    # -- startup coordination ------------------------------------------

    async def try_acquire_cleanup_lock(self) -> bool:
        return bool(await self._r.set(self._k("cleanup", "lock"), "1", nx=True, ex=300))

    async def release_cleanup_lock(self) -> None:
        await self._r.delete(self._k("cleanup", "lock"))

    async def register_pool_worker(self, worker_id: str) -> None:
        """Register/refresh this worker's pool liveness lease (score=epoch)."""
        key = self._k("pool", "workers")
        await self._r.zadd(key, {worker_id: time.time()})
        await self._r.expire(key, USER_ACTIVE_TTL_SECONDS)

    async def unregister_pool_worker(self, worker_id: str) -> None:
        await self._r.zrem(self._k("pool", "workers"), worker_id)

    async def pool_workers_alive(self) -> bool:
        """True when another worker's pool lease is fresh (<=60s old).

        Prunes stale entries first, so a crashed worker's lease expires
        naturally and a restarting worker sweeps the true orphans.
        """
        key = self._k("pool", "workers")
        await self._r.zremrangebyscore(key, 0, time.time() - 60)
        return bool(await self._r.zcard(key))


async def build_stream_store(settings) -> StreamStore:
    """Connect to Redis and fail fast if it is unreachable."""
    store = StreamStore(settings.redis_url)
    await store.ping()
    return store
