/**
 * 基于 fetch 的 SSE 客户端。
 *
 * 后端的聊天流（POST /stream、POST /resume、GET /stream 重连）和会话状态频道
 * （GET /conversations/events）都需要带 Authorization 头，原生 EventSource
 * 不支持自定义请求头，因此用 fetch + ReadableStream 手动解析 SSE 帧。
 *
 * 帧格式与后端保持一致：`event: <name>\ndata: <json>\n\n`，注释行
 * `: keepalive` 用于保活，直接忽略。
 */

import { ApiError, getToken } from "./api/client";

export interface SSEEvent {
  event: string;
  data: string;
}

export interface SSEClient {
  events: AsyncGenerator<SSEEvent, void, void>;
  response: Response;
  abort: () => void;
}

export interface ConnectSSEOptions extends RequestInit {
  /** 每收到一批字节回调（含 keepalive 注释）——用于连接活跃度看门狗 */
  onActivity?: () => void;
}

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

function parseBlock(raw: string): SSEEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue; // 注释/keepalive
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

export async function connectSSE(path: string, init: ConnectSSEOptions = {}): Promise<SSEClient> {
  const controller = new AbortController();
  const token = getToken();
  const { onActivity, ...rest } = init;

  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    signal: controller.signal,
    headers: {
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(rest.headers ?? {}),
    },
  });

  if (!res.ok || !res.body) {
    let detail = `连接失败 (HTTP ${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // 非 JSON 响应，保留默认信息
    }
    throw new ApiError(res.status, detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();

  async function* gen(): AsyncGenerator<SSEEvent, void, void> {
    let buf = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        onActivity?.();
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const evt = parseBlock(raw);
          if (evt) yield evt;
        }
      }
      // 流结束时可能残留一个未以 \n\n 结尾的帧
      if (buf.trim()) {
        const evt = parseBlock(buf);
        if (evt) yield evt;
      }
    } finally {
      reader.releaseLock();
    }
  }

  return {
    events: gen(),
    response: res,
    abort: () => controller.abort(),
  };
}
