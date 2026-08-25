"use client";

/**
 * 会话状态频道（GET /conversations/events）：
 * 后端在生成开始/结束时发布 conversation_status 事件，会话列表据此
 * 实时更新「生成中 / 等待补充」状态，无需轮询。断线 3 秒后自动重连。
 */

import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import { connectSSE, type SSEClient } from "@/lib/sse";
import type { ConversationStatusEvent } from "@/lib/api/types";

export interface ConversationEvents {
  /** 正在生成中的会话 id */
  running: Set<string>;
  /** 等待用户补充信息的会话 id */
  awaitingInput: Set<string>;
}

/** 状态事件触发的列表刷新防抖窗口：重连重放可达 200 条事件，合并成一次请求 */
const REVALIDATE_DEBOUNCE_MS = 600;

/**
 * 连接活跃度看门狗：服务端每 15s 发一次 keepalive，超过 45s 无任何字节
 * 视为连接假死（浏览器未感知的 TCP 中断），主动断开触发重连重放。
 */
const WATCHDOG_CHECK_MS = 10_000;
const WATCHDOG_TIMEOUT_MS = 45_000;

export function useConversationEvents(): ConversationEvents {
  const { mutate } = useSWRConfig();
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [awaitingInput, setAwaitingInput] = useState<Set<string>>(new Set());
  const clientRef = useRef<SSEClient | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let revalidateTimer: number | undefined;
    let lastActivity = Date.now();
    // 重连退避：3s 起指数增长，上限 30s——后端停机期间控制台不会每 3 秒刷一条
    let reconnectDelay = 3000;
    const watchdog = window.setInterval(() => {
      if (Date.now() - lastActivity > WATCHDOG_TIMEOUT_MS) {
        clientRef.current?.abort();
      }
    }, WATCHDOG_CHECK_MS);

    // 列表刷新合并：无论来多少条状态事件，600ms 内只重新拉取一次
    const scheduleRevalidate = () => {
      if (revalidateTimer !== undefined) return;
      revalidateTimer = window.setTimeout(() => {
        revalidateTimer = undefined;
        mutate("conversations");
      }, REVALIDATE_DEBOUNCE_MS);
    };

    const apply = (data: ConversationStatusEvent) => {
      // 本地标志即时更新（角标、生成中状态不依赖网络）
      setRunning((prev) => {
        const next = new Set(prev);
        if (data.status === "running") next.add(data.conversation_id);
        else next.delete(data.conversation_id);
        return next;
      });
      setAwaitingInput((prev) => {
        const next = new Set(prev);
        if (data.status === "awaiting_input") next.add(data.conversation_id);
        else next.delete(data.conversation_id);
        return next;
      });
      // 标题 / 排序 / 最新消息时间在 done 时才最终确定，防抖后统一刷新
      scheduleRevalidate();
    };

    const start = async () => {
      if (cancelled) return;
      try {
        const client = await connectSSE("/conversations/events", {
          onActivity: () => {
            lastActivity = Date.now();
          },
        });
        clientRef.current = client;
        reconnectDelay = 3000; // 连接成功，重置退避
        for await (const evt of client.events) {
          lastActivity = Date.now();
          if (evt.event !== "conversation_status") continue;
          try {
            apply(JSON.parse(evt.data) as ConversationStatusEvent);
          } catch {
            // 忽略无法解析的事件
          }
        }
      } catch {
        // 连接失败 / 被服务端关闭 / 看门狗触发：稍后重连（重连会重放状态流）
      }
      if (!cancelled) {
        timer = window.setTimeout(start, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 30_000);
      }
    };

    start();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      if (revalidateTimer) window.clearTimeout(revalidateTimer);
      window.clearInterval(watchdog);
      clientRef.current?.abort();
    };
  }, [mutate]);

  return { running, awaitingInput };
}
