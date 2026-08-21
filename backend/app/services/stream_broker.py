"""Per-conversation SSE event fan-out with a bounded replay buffer.

Decouples generation from the HTTP connection: a detached producer task
publishes events into a ``StreamBroker`` while any number of short-lived
subscribers (page loads / refreshes) replay the buffer and follow live
events. A client disconnect only removes its subscriber; the producer keeps
running to completion.

Subscribers receive the whole buffered tail (no cursor/sequence) — the
frontend dedupes by the ids already embedded in each event (``message_id``
/ ``tool_call_id``). The buffer is bounded; anything evicted before an
attach is recovered from the incremental DB mirror instead.
"""

from __future__ import annotations

import asyncio
from collections import deque

MAX_REPLAY_EVENTS = 500


class StreamBroker:
    """One per active conversation. Producer publishes, subscribers follow.

    ``publish`` appends to the replay buffer and fans out to every live
    subscriber queue. ``finish`` pushes a ``None`` sentinel to subscribers
    (events are always strings, so ``None`` is unambiguous); attaches after
    ``finish`` get the buffer — which already contains the ``done``/``error``
    event — with no live queue.
    """

    def __init__(self, max_replay_events: int = MAX_REPLAY_EVENTS) -> None:
        self._buffer: deque[str] = deque(maxlen=max_replay_events)
        self._subscribers: set[asyncio.Queue] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def publish(self, event: str) -> None:
        if self._closed:
            return
        self._buffer.append(event)
        for queue in tuple(self._subscribers):
            queue.put_nowait(event)

    def finish(self) -> None:
        """Producer done: close and push the end sentinel to live subscribers."""
        if self._closed:
            return
        self._closed = True
        for queue in tuple(self._subscribers):
            queue.put_nowait(None)

    def subscribe(self) -> tuple[list[str], asyncio.Queue]:
        """Return ``(replay, live_queue)``. The caller MUST ``unsubscribe``.

        ``live_queue`` receives subsequent publishes and finally ``None``.
        """
        replay = list(self._buffer)
        queue: asyncio.Queue = asyncio.Queue()
        if not self._closed:
            self._subscribers.add(queue)
        return replay, queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
