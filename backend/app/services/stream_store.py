"""Redis-backed stream coordination (single implementation, Redis required).

All chat-streaming coordination goes through this store so the endpoints
and the detached producers work identically from any uvicorn worker.
There is deliberately no in-memory fallback: Redis is mandatory (fail fast
at startup via ``ping``), which keeps this one implementation exercised on
every run instead of two drifting implementations.

Redis layout (prefix ``stream``):

- ``stream:lock:{conv}``    SET NX EX 120 — single-flight reservation, value
                            = user id; the producer's heartbeat refreshes it.
- ``stream:current:{conv}`` SET EX 600 — points at the CURRENT generation id;
                            overwritten by each new POST. Subscribers follow
                            the stream behind this pointer, so a new question
                            never replays the previous answer's events.
- ``stream:events:{conv}:{gen}`` Stream, MAXLEN ~500 — one event log per
                            generation; each publish refreshes a 600s EXPIRE.
                            Last event is always ``STREAM_END``.
- ``stream:status:{user}``  Stream, MAXLEN ~200 — conversation status
                            transitions (running/done/interrupted) so an open
                            page updates its list in real time via one SSE
                            subscription instead of polling the list.
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
import logging
import time
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

logger = logging.getLogger(__name__)

STREAM_END = "__stream_end__"

# redis-py 8 defaults socket_timeout to 5s — far too tight for the SSH tunnel
# to the remote Redis (mobile broadband → Tencent Cloud). Transient TCP stalls
# of a minute or more are routine; raise the timeout and retry transient
# errors (ConnectionError/TimeoutError) on a fresh connection. The built-in
# default retry (10× with 10ms→1s jittered backoff) only survives ~55s of
# stall alongside the 5s timeout; this survives ~2.5min.
REDIS_SOCKET_TIMEOUT = 30.0
REDIS_RETRY = Retry(ExponentialBackoff(cap=30.0, base=1.0), 4)

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
        self._r = self._make_client(redis_url)
        self._acquire_script = self._r.register_script(_USER_ACQUIRE_LUA)

    @staticmethod
    def _make_client(redis_url: str) -> aioredis.Redis:
        return aioredis.Redis.from_url(
            redis_url,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            retry=REDIS_RETRY,
        )

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

    async def begin_generation(self, conv_key: str) -> str:
        """Start a new event-log generation and point ``current`` at it.

        Called once per POST (single-flight reservation guarantees only one
        producer). Replay is generation-scoped: a new question never
        surfaces the previous answer's events or its END marker.
        """
        gen = uuid.uuid4().hex
        await self._r.set(self._k("current", conv_key), gen, ex=EVENTS_TTL_SECONDS)
        return gen

    async def publish(self, conv_key: str, event: str) -> None:
        gen = await self._r.get(self._k("current", conv_key))
        if gen is None:
            return  # generation pointer expired; nothing to publish into
        gen = gen.decode()
        key = self._k("events", f"{conv_key}:{gen}")
        pipe = self._r.pipeline()
        pipe.xadd(key, {"e": event.encode()}, maxlen=MAX_REPLAY_EVENTS, approximate=True)
        pipe.expire(key, EVENTS_TTL_SECONDS)
        pipe.expire(self._k("current", conv_key), EVENTS_TTL_SECONDS)
        await pipe.execute()

    async def follow(self, conv_key: str) -> AsyncIterator[str]:
        """Replay the CURRENT generation's stream, then follow live events.

        Yields SSE strings; on producer death (lock expiry) the iterator
        ends quietly. Uses a dedicated Redis connection so cancelling the
        generator (client disconnect) closes a socket we don't share.
        """
        gen = await self._r.get(self._k("current", conv_key))
        if gen is None:
            return  # nothing to replay; caller checked state, producer just ended
        key = self._k("events", f"{conv_key}:{gen.decode()}")
        async for event in self._follow_key(
            key, end_marker=STREAM_END, liveness_conv=conv_key
        ):
            yield event

    async def publish_status(self, user_key: str, event: str) -> None:
        """Append a conversation-status SSE event to the user's status stream."""
        key = self._k("status", user_key)
        pipe = self._r.pipeline()
        pipe.xadd(key, {"e": event.encode()}, maxlen=MAX_REPLAY_EVENTS, approximate=True)
        pipe.expire(key, EVENTS_TTL_SECONDS)
        await pipe.execute()

    def follow_status(self, user_key: str) -> AsyncIterator[str]:
        """Follow the user's conversation-status stream (replay, then live).

        No end marker and no liveness check: the status channel is a
        persistent subscription the page keeps open; only a disconnect or a
        server-side close ends it.
        """
        return self._follow_key(self._k("status", user_key))

    async def _follow_key(
        self,
        key: str,
        *,
        end_marker: str | None = None,
        liveness_conv: str | None = None,
    ) -> AsyncIterator[str]:
        """Shared replay+follow loop over one Redis Stream.

        Yields SSE strings (with keepalives on silence). When
        ``end_marker`` is seen the iterator returns; when ``liveness_conv``
        is set, the loop also returns if that conversation's producer lock
        disappears (crash without an end marker). Uses a dedicated Redis
        connection so cancelling the generator (client disconnect) closes a
        socket we don't share.
        """
        client = self._make_client(self._url)
        cursor: bytes | str = b"0-0"
        end_raw = end_marker.encode() if end_marker else None
        try:
            res = await client.xread({key: b"0-0"}, count=MAX_REPLAY_EVENTS)
            for _, entries in res or []:
                for msg_id, fields in entries:
                    raw = fields.get(b"e")
                    if raw is None:
                        continue
                    cursor = msg_id
                    if end_raw is not None and raw == end_raw:
                        return
                    yield raw.decode("utf-8")
            while True:
                res = await client.xread({key: cursor}, count=100, block=KEEPALIVE_MS)
                if not res:
                    yield ": keepalive\n\n"
                    if liveness_conv is not None and not await self.is_active(liveness_conv):
                        return
                    continue
                for _, entries in res:
                    for msg_id, fields in entries:
                        raw = fields.get(b"e")
                        if raw is None:
                            continue
                        cursor = msg_id
                        if end_raw is not None and raw == end_raw:
                            return
                        yield raw.decode("utf-8")
        finally:
            await client.aclose()

    async def state(self, conv_key: str) -> str:
        """``'active'`` (current generation's log exists) | ``'pending'``
        (reserved, not publishing yet) | ``'inactive'``."""
        gen = await self._r.get(self._k("current", conv_key))
        if gen is not None and await self._r.exists(
            self._k("events", f"{conv_key}:{gen.decode()}")
        ):
            return "active"
        if await self._r.exists(self._k("lock", conv_key)):
            return "pending"
        return "inactive"

    async def is_active(self, conv_key: str) -> bool:
        return bool(await self._r.exists(self._k("lock", conv_key)))

    async def active_locks(self, conv_keys: list[str]) -> set[str]:
        """Which of these conversations currently hold a producer reservation.

        Batched pipeline so the conversation list can annotate every row
        with one Redis round-trip.
        """
        if not conv_keys:
            return set()
        pipe = self._r.pipeline()
        for conv_key in conv_keys:
            pipe.exists(self._k("lock", conv_key))
        results = await pipe.execute()
        return {k for k, ok in zip(conv_keys, results) if ok}

    # -- control -------------------------------------------------------

    async def request_stop(self, conv_key: str) -> None:
        await self._r.publish(self._k("stop", conv_key), "stop")
        # Covers a stop that races the producer's subscribe.
        await self._r.set(self._k("stopreq", conv_key), "1", ex=60)

    async def wait_stop(self, conv_key: str) -> None:
        # A Redis blip must not surface as "stop requested": the producer
        # treats this task completing as a /stop signal and would cancel the
        # run. Retry the whole subscribe loop on transient errors instead.
        while True:
            # Subscribe first so a publish between the stopreq check and the
            # subscribe is still seen.
            pubsub = self._r.pubsub()
            try:
                await pubsub.subscribe(self._k("stop", conv_key))
                if await self._r.delete(self._k("stopreq", conv_key)):
                    return  # stop requested before we subscribed
                while True:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if msg is not None and msg.get("type") == "message":
                        return
            except aioredis.RedisError:
                logger.warning("wait_stop redis error, reconnecting: conv=%s", conv_key)
                await asyncio.sleep(2.0)
            finally:
                try:
                    await pubsub.aclose()
                except aioredis.RedisError:
                    pass

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
