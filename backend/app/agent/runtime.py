"""Agent execution helpers: invoke + SSE event mapping via ``astream_events`` v3.

Provides ``build_multimodal_input`` to convert user-uploaded images (stored in
RustFS ``/files/``) into base64 data URLs so a vision-capable LLM can see them.

All agent runs go through this module so lifecycle rules (sandbox cleanup,
mirror writes) live in one place.

Streaming uses LangGraph ``astream_events(version="v3")``, which returns an
``AsyncGraphRunStream`` with typed projections (caller-driven pump):

- ``run.messages``  — one ``AsyncChatModelStream`` per model call; its
  ``.text`` projection yields token deltas.
- ``run.tool_calls`` — one ``ToolCallStream`` per tool invocation, carrying
  the complete input args at start, ``output`` (ToolMessage) / ``error`` at
  completion, and an ``output_deltas`` channel that MUST be drained
  (unconsumed buffers apply backpressure to the pump).

Projections are single-consumer and must be consumed concurrently — each
cursor drives the shared pump. We run one consumer task per projection and
merge them through a queue; ordering is preserved because the graph cannot
start the next model call until the tools node finishes.

NOTE: v3 is marked experimental by LangGraph ("may change"); the API facts
above were verified against langgraph 1.2.11.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import posixpath
import uuid
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15.0


@dataclass
class AgentRunResult:
    """Outcome of one agent run."""

    text: str
    """Aggregated assistant text (may be empty when the run was interrupted)."""

    interrupted: bool = False


def extract_assistant_text(messages: list[BaseMessage]) -> str:
    """Join the string parts of the last assistant message's content."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            parts: list[str] = []
            for block in msg.content if isinstance(msg.content, list) else [msg.content]:
                if isinstance(block, str):
                    parts.append(block)
            return "\n".join(p for p in parts if p)
    return ""


async def build_multimodal_input(
    content: str,
    image_paths: list[str] | None,
    *,
    user_id: str,
    s3_client,
    bucket: str,
    supports_vision: bool = False,
) -> list | str:
    """Build the user message ``content`` payload for the agent.

    If ``image_paths`` is non-empty and ``supports_vision`` is True, each
    ``/files/...`` path is loaded from RustFS via S3, encoded to a base64
    ``data:image/...;base64,...`` URL, and included alongside the text in a
    list-of-blocks format compatible with LangChain's vision-enabled
    ``ChatOpenAI`` client.

    If the model does NOT support vision, image filenames are appended to the
    text as a plain-text note (the agent cannot see pixels but at least knows
    something was attached) instead of silently dropping them.

    When no images are provided the function returns the raw ``content``
    string, preserving the existing code path for non-vision models.
    """
    if not image_paths:
        return content

    valid_paths: list[str] = []
    for vpath in image_paths:
        if not isinstance(vpath, str) or not vpath.startswith("/files/"):
            continue
        clean = posixpath.normpath(vpath.lstrip("/"))
        if clean == ".." or clean.startswith("../"):
            continue
        _, ext = posixpath.splitext(clean)
        if ext.lower() not in ALLOWED_IMAGE_EXT:
            continue
        valid_paths.append((vpath, clean))

    if not valid_paths:
        return content

    if not supports_vision:
        # Graceful degradation: mention the images in text so the user knows
        # why the model didn't analyse them.
        names = ", ".join(posixpath.basename(p) for _, p in valid_paths)
        appendix = (
            f"\n\n[系统提示] 用户上传了 {len(valid_paths)} 张图片：{names}。"
            "但当前配置的 LLM_MODEL 未启用视觉能力 (LLM_SUPPORTS_VISION=false)，"
            "请在 .env 中切换到支持视觉的模型并设置 LLM_SUPPORTS_VISION=true。"
        )
        return content + appendix

    blocks: list = [{"type": "text", "text": content}]
    for vpath, clean in valid_paths:
        key = f"users/{user_id}/{clean}"
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=key)
            raw = obj["Body"].read()
            ctype = obj.get("ContentType") or mimetypes.guess_type(clean)[0] or "image/png"
        except Exception as exc:
            logger.warning("multimodal: failed to read %s: %s", vpath, exc)
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{ctype};base64,{b64}"},
        })
    return blocks if len(blocks) > 1 else content


async def run_agent(
    agent,
    *,
    content: list | str,
    config: dict,
) -> AgentRunResult:
    """Invoke the agent once (non-streaming) and return the assistant text."""
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": content}]},
        config=config,
    )
    messages: list[BaseMessage] = result.get("messages", [])
    return AgentRunResult(text=extract_assistant_text(messages))


# ----------------------------------------------------------------------
# SSE streaming
# ----------------------------------------------------------------------


def sse(event: str, data: dict) -> str:
    """Render one SSE event (data is JSON)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


class SSEEventMapper:
    """Stateful mapper from v3 projection payloads to SSE event strings.

    Tracks emitted tool-call ids so each tool_call/tool_result pair is
    sent once. Subagent events never reach this mapper: they live on the
    ``subgraphs`` projection, which we do not consume — the subagent's
    answer is surfaced to the parent as the ``task`` tool's ToolMessage.
    """

    def __init__(self, conversation_id: str, thread_id: str, base_url: str = "") -> None:
        self.conversation_id = conversation_id
        self.thread_id = thread_id
        self.base_url = base_url.rstrip("/")
        self.assistant_text_parts: list[str] = []
        self._emitted_tool_calls: set[str] = set()
        self._emitted_tool_results: set[str] = set()
        self._emitted_artifacts: set[str] = set()
        self.message_id = str(uuid.uuid4())

    # -- feeds ---------------------------------------------------------

    def feed_text(self, text: str) -> list[str | None]:
        if not text:
            return [None]
        self.assistant_text_parts.append(text)
        return [sse("message", {"message_id": self.message_id, "delta": text})]

    def feed_tool_call(self, ts) -> list[str | None]:
        """``ToolCallStream`` at tool start — complete input args."""
        call_id = ts.tool_call_id or ""
        if call_id and call_id in self._emitted_tool_calls:
            return [None]
        if call_id:
            self._emitted_tool_calls.add(call_id)
        logger.info("tool_call: id=%s name=%s args=%s", call_id, ts.tool_name, ts.input)
        return [sse(
            "tool_call",
            {
                "tool_call_id": call_id,
                "name": ts.tool_name or "unknown",
                "arguments": ts.input or {},
            },
        )]

    def feed_tool_result(self, ts) -> list[str | None]:
        """``ToolCallStream`` after its delta channel closed (success path).

        Returns a list of SSE event strings (may contain 0 or 2 items:
        the tool_result event and optionally an artifact event when
        ``write_file`` saved something under ``/files/``).
        """
        call_id = ts.tool_call_id or ""
        if call_id and call_id in self._emitted_tool_results:
            return [None]
        if call_id:
            self._emitted_tool_results.add(call_id)
        output = ts.output
        if isinstance(output, ToolMessage):
            content = str(output.content)
            is_error = output.status == "error"
        else:
            content = str(output)
            is_error = False
        logger.info(
            "tool_result: id=%s name=%s error=%s output_len=%d",
            call_id, ts.tool_name, is_error, len(content),
        )
        result = sse(
            "tool_result",
            {"tool_call_id": call_id, "output": content[:4000], "is_error": is_error},
        )
        events: list[str | None] = [result]

        # Detect write_file to /files/ — emit artifact event for download.
        if not is_error and ts.tool_name == "write_file":
            file_path = self._extract_file_path(ts.input)
            if file_path and file_path.startswith("/files/"):
                artifact = self._make_artifact_event(file_path)
                if artifact:
                    events.append(artifact)

        return events

    def feed_tool_error(self, ts) -> list[str | None]:
        call_id = ts.tool_call_id or ""
        if call_id and call_id in self._emitted_tool_results:
            return [None]
        if call_id:
            self._emitted_tool_results.add(call_id)
        logger.warning("tool_error: id=%s name=%s error=%s", call_id, ts.tool_name, ts.error)
        return [sse(
            "tool_result",
            {
                "tool_call_id": call_id,
                "output": f"Tool {ts.tool_name} raised: {ts.error}"[:4000],
                "is_error": True,
            },
        )]

    @staticmethod
    def _extract_file_path(tool_input: dict | None) -> str | None:
        """Pull ``file_path`` from a tool's input args (best-effort)."""
        if not isinstance(tool_input, dict):
            return None
        fp = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(fp, str):
            return fp
        return None

    def _make_artifact_event(self, file_path: str) -> str | None:
        """Build an ``artifact`` SSE event for a file under ``/files/``."""
        # Dedup: one artifact per file path.
        if file_path in self._emitted_artifacts:
            return None
        self._emitted_artifacts.add(file_path)
        filename = file_path[len("/files/"):]  # strip /files/ prefix
        download_url = f"{self.base_url}/api/v1/files/{filename}" if self.base_url else f"/api/v1/files/{filename}"
        return sse(
            "artifact",
            {
                "name": filename,
                "download_url": download_url,
                "tool_call_id": "",  # filled by caller when available
            },
        )

    # -- lifecycle -----------------------------------------------------

    @property
    def assistant_text(self) -> str:
        return "".join(self.assistant_text_parts).strip()

    def done_event(self) -> str:
        return sse(
            "done",
            {
                "conversation_id": self.conversation_id,
                "thread_id": self.thread_id,
                "message_id": self.message_id,
            },
        )

    def error_event(self, code: str, message: str) -> str:
        return sse("error", {"code": code, "message": message})


async def stream_agent(
    agent,
    mapper: SSEEventMapper,
    *,
    content: list | str,
    config: dict,
):
    """Async generator: SSE event strings for one agent run (astream_events v3).

    Yields keepalive comments while the agent is silent (long tool runs), a
    ``done`` event on success, and an ``error`` event on failure. The caller
    owns ``mapper`` (reads ``mapper.assistant_text`` afterwards) and backend
    cleanup (finally) — see the chat endpoint.
    """
    run = await agent.astream_events(
        {"messages": [{"role": "user", "content": content}]},
        config=config,
        version="v3",
        durability="exit",
    )

    queue: asyncio.Queue = asyncio.Queue()

    async def pump_messages() -> None:
        """Consume the (single-consumer) messages projection."""
        try:
            async for item in run.messages:
                async for text in item.text:
                    await queue.put(("text", text))
        finally:
            await queue.put(None)  # sentinel: this pump is done

    async def pump_tool_calls() -> None:
        """Consume tool-call streams; drain deltas to avoid pump backpressure."""
        try:
            async for ts in run.tool_calls:
                await queue.put(("tool_call", ts))
                async for _delta in ts:  # drain required — see module docstring
                    pass
                if ts.error is not None:
                    await queue.put(("tool_error", ts))
                else:
                    await queue.put(("tool_result", ts))
        finally:
            await queue.put(None)  # sentinel: this pump is done

    pending_pumps = 2
    pumps: list[asyncio.Task] = []

    async with run:
        pumps = [
            asyncio.create_task(pump_messages()),
            asyncio.create_task(pump_tool_calls()),
        ]
        try:
            while pending_pumps > 0:
                try:
                    item = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    pending_pumps -= 1
                    continue
                kind, payload = item
                if kind == "text":
                    outs = mapper.feed_text(payload)
                elif kind == "tool_call":
                    outs = mapper.feed_tool_call(payload)
                elif kind == "tool_result":
                    outs = mapper.feed_tool_result(payload)
                elif kind == "tool_error":
                    outs = mapper.feed_tool_error(payload)
                else:  # pragma: no cover — queue protocol is closed
                    outs = [None]
                for out in outs:
                    if out:
                        yield out
            # Both pumps exhausted: reap them (propagates pump exceptions).
            await asyncio.gather(*pumps)
            yield mapper.done_event()
        finally:
            for pump in pumps:
                pump.cancel()
