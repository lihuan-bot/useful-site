"use client";

/**
 * 聊天流式会话 hook —— 对应后端三个 SSE 接口：
 *
 * - POST /conversations/{id}/stream   发送消息并流式接收
 * - POST /conversations/{id}/resume   HITL 中断后提交补充信息续答
 * - GET  /conversations/{id}/stream   重连进行中的生成（刷新页面后恢复）
 *
 * 事件协议见 backend/app/agent/runtime.py 的 SSEEventMapper：
 * message / tool_call / tool_result / artifact / done / error / interrupt，
 * 以及重连时的 status 事件（active=false 表示没有正在运行的生成）。
 *
 * 并发模型：同一时刻只允许一个消费者。send / resume 会「抢占」挂起的
 * attach（用户意图优先），被抢占的消费者以 PreemptedError 收场；
 * attach 遇到进行中的 send 则直接跳过（返回 inactive）。
 *
 * 网络韧性：消费中途连接中断（非主动取消）时，自动通过 GET /stream
 * 重放同一代事件日志恢复——后端生成与页面连接本就解耦，重放从头下发，
 * 本地已收的部分文本清空重来即可无缝衔接。重试上限内失败才放弃。
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { apiCreateConversation } from "@/lib/api/conversations";
import { apiStopGeneration } from "@/lib/api/chat";
import { ApiError } from "@/lib/api/client";
import { connectSSE, type SSEClient } from "@/lib/sse";
import type {
  ArtifactEvent,
  ErrorEvent,
  InterruptEvent,
  InterruptPayload,
  MessageEvent,
  StreamStatusEvent,
  ToolCallEvent,
  ToolResultEvent,
} from "@/lib/api/types";

export type ChatPhase = "idle" | "streaming" | "awaiting_input";

export interface ToolStep {
  key: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "running" | "done" | "error";
  output?: string;
  /** 开始时间（ms），用于执行耗时展示 */
  startedAt: number;
  /** 结束时间（ms），用于「工具完成后模型思考」的计时起点 */
  finishedAt?: number;
}

export interface LiveArtifact {
  name: string;
  download_url: string;
}

export interface StreamState {
  phase: ChatPhase;
  /** 本轮生成的助手文本增量 */
  text: string;
  toolSteps: ToolStep[];
  artifacts: LiveArtifact[];
  interrupt: InterruptPayload | null;
  error: string | null;
}

const INITIAL_STATE: StreamState = {
  phase: "idle",
  text: "",
  toolSteps: [],
  artifacts: [],
  interrupt: null,
  error: null,
};

type Action =
  | { type: "begin" }
  | { type: "delta"; delta: string }
  | { type: "tool_call"; key: string; name: string; args: Record<string, unknown>; startedAt: number }
  | { type: "tool_result"; key: string; output: string; isError: boolean; finishedAt: number }
  | { type: "artifact"; artifact: LiveArtifact }
  | { type: "interrupt"; payload: InterruptPayload }
  | { type: "error"; message: string }
  | { type: "stream_closed" }
  | { type: "dismiss_interrupt" }
  | { type: "reset" };

function reducer(state: StreamState, action: Action): StreamState {
  switch (action.type) {
    case "begin":
      return { ...INITIAL_STATE, phase: "streaming" };
    case "delta":
      return { ...state, text: state.text + action.delta };
    case "tool_call":
      return {
        ...state,
        toolSteps: [
          ...state.toolSteps,
          {
            key: action.key,
            name: action.name,
            arguments: action.args,
            status: "running",
            startedAt: action.startedAt,
          },
        ],
      };
    case "tool_result": {
      const idx = state.toolSteps.findIndex((s) => s.key === action.key);
      if (idx < 0) return state;
      const steps = [...state.toolSteps];
      steps[idx] = {
        ...steps[idx],
        status: action.isError ? "error" : "done",
        output: action.output,
        finishedAt: action.finishedAt,
      };
      return { ...state, toolSteps: steps };
    }
    case "artifact":
      return { ...state, artifacts: [...state.artifacts, action.artifact] };
    case "interrupt":
      return { ...state, phase: "awaiting_input", interrupt: action.payload };
    case "error":
      return { ...state, phase: "idle", error: action.message };
    case "stream_closed":
      // 流提前结束（用户停止 / 生成方退出）——保留已收到的增量文本
      return { ...state, phase: "idle" };
    case "dismiss_interrupt":
      return { ...state, phase: "idle", interrupt: null };
    case "reset":
      return INITIAL_STATE;
  }
}

/** 一轮生成的最终结果（消费循环结束时返回，供上层把内容落进消息列表） */
export interface GenerationEnd {
  kind: "done" | "interrupt" | "stopped" | "error";
  text: string;
  toolSteps: ToolStep[];
  artifacts: LiveArtifact[];
  interruptPayload?: InterruptPayload;
  /** kind === "error" 时的错误信息 */
  errorMessage?: string;
  /** kind === "stopped" 时：是否为用户主动停止（用于区分网络中断） */
  stoppedByUser?: boolean;
}

export interface AttachResult {
  /** ended=重放完成；inactive=没有运行中的生成（或正在发送中，跳过）；pending=已预留但尚未开始 */
  status: "ended" | "inactive" | "pending";
  end?: GenerationEnd;
}

interface SendResult {
  conversationId: string;
  /** 生成结束时 resolve */
  wait: Promise<GenerationEnd>;
}

/** 消费循环被更新的 send/resume 抢占 */
export class PreemptedError extends Error {
  constructor() {
    super("stream preempted");
    this.name = "PreemptedError";
  }
}

/** 网络中断自动重连的最大次数与退避 */
const MAX_RECONNECT = 4;

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function safeJson(data: string): Record<string, unknown> {
  try {
    return JSON.parse(data);
  } catch {
    return {};
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

interface ConsumeOptions {
  /** 重连场景：不提前进入 streaming 态，等第一个真实事件 */
  lazyBegin: boolean;
  /** 是否抢占进行中的消费者（send/resume=true；attach=false 遇忙直接跳过） */
  preempt: boolean;
  /** 网络中断时用于重放恢复的 GET 地址（send/resume 传入；attach 自身即重放地址） */
  replayPath: string | null;
}

export function useChatStream() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const activeClientRef = useRef<SSEClient | null>(null);
  const epochRef = useRef(0);
  /** 发送/续答的操作序号：cancel 会使其失效，挂起的 409 重试据此中止 */
  const operationRef = useRef(0);
  /** 用户主动停止标记：消费循环自然结束时读取，区分「停止」与「网络中断」 */
  const stopRequestedRef = useRef(false);
  /**
   * 主动中断的意图标记：abort 在部分浏览器报 TypeError 而非 AbortError，
   * 不能靠错误类型区分「主动取消」与「网络中断」——靠意图标记最可靠。
   */
  const abortReasonRef = useRef<"unmount" | null>(null);
  const fallbackKeyRef = useRef(0);

  // 组件卸载时中断正在进行的 SSE 消费（fetch 端）。
  // 服务端生成不受影响——重新挂载后通过 attach() 重放恢复。
  useEffect(() => {
    return () => {
      // 一并作废 epoch / 操作序号：中止挂起的 409 重试与消费循环
      epochRef.current += 1;
      operationRef.current += 1;
      abortReasonRef.current = "unmount";
      activeClientRef.current?.abort();
      activeClientRef.current = null;
    };
  }, []);

  const release = useCallback((epoch: number) => {
    // 只有当前轮次的消费者才有权释放 busy（旧消费者收尾不能影响新的）
    if (epoch === epochRef.current) {
      busyRef.current = false;
      setBusy(false);
      activeClientRef.current = null;
    }
  }, []);

  const finish = useCallback(
    (epoch: number, end: GenerationEnd): { end: GenerationEnd } => {
      release(epoch);
      return { end };
    },
    [release],
  );

  /**
   * 消费一条 SSE 流并驱动 reducer。
   * 网络中断（非主动取消）时自动重连：重放同一代事件日志，
   * 本地累积清空重来，对上层表现为一条连续的流。
   */
  const consume = useCallback(
    async (
      path: string,
      init: RequestInit,
      opts: ConsumeOptions,
    ): Promise<{ end: GenerationEnd } | { status: "inactive" | "pending" }> => {
      if (busyRef.current) {
        if (!opts.preempt) {
          // attach 遇到进行中的 send/resume：直接跳过，不打扰正在进行的生成
          return { status: "inactive" };
        }
        // 抢占：中断旧消费者（epoch 失配使其以 PreemptedError 收场）
        activeClientRef.current?.abort();
      }

      const epoch = ++epochRef.current;
      abortReasonRef.current = null; // 清掉可能残留的旧标记（如卸载时无活跃消费者）
      busyRef.current = true;
      setBusy(true);
      if (!opts.lazyBegin) dispatch({ type: "begin" });

      let text = "";
      let started = !opts.lazyBegin;
      const steps: ToolStep[] = [];
      const artifacts: LiveArtifact[] = [];

      let attempt = 0; // 0 = 原始请求；>0 = 网络中断后的重放重连
      let reconnectDelay = 1000;

      while (true) {
        const isReconnect = attempt > 0;
        let client: SSEClient;
        try {
          client = await connectSSE(isReconnect && opts.replayPath ? opts.replayPath : path, {
            ...(isReconnect ? { method: "GET" } : init),
          });
        } catch (err) {
          if (!isReconnect) {
            // 首次请求失败（409/429/网络错误）：交给调用方提示
            release(epoch);
            if (!opts.lazyBegin) dispatch({ type: "reset" });
            throw err;
          }
          if (epoch !== epochRef.current) throw new PreemptedError();
          if (attempt >= MAX_RECONNECT) {
            return finish(epoch, { kind: "stopped", text, toolSteps: steps, artifacts });
          }
          await sleep(reconnectDelay);
          reconnectDelay *= 2;
          attempt += 1;
          continue;
        }
        activeClientRef.current = client;

        if (isReconnect) {
          // 重放从头下发：清空本地累积，UI 重打一遍
          text = "";
          steps.length = 0;
          artifacts.length = 0;
          dispatch({ type: "reset" });
          dispatch({ type: "begin" });
          started = true;
        }

        try {
          for await (const evt of client.events) {
            const data = safeJson(evt.data);
            switch (evt.event) {
              case "status": {
                const s = data as unknown as StreamStatusEvent;
                if (isReconnect) {
                  // 重连时后端报告无活动生成：该轮已结束/事件日志已过期，
                  // 完整内容在后端已入库，交给视图从 DB 恢复
                  return finish(epoch, { kind: "stopped", text, toolSteps: steps, artifacts });
                }
                // 首次 attach：无正在运行的生成时后端只回一个 status 事件
                release(epoch);
                if (!started) dispatch({ type: "reset" });
                return { status: s.pending ? "pending" : "inactive" };
              }
              case "message": {
                if (!started) {
                  started = true;
                  dispatch({ type: "begin" });
                }
                const delta = (data as unknown as MessageEvent).delta ?? "";
                text += delta;
                dispatch({ type: "delta", delta });
                break;
              }
              case "tool_call": {
                if (!started) {
                  started = true;
                  dispatch({ type: "begin" });
                }
                const t = data as unknown as ToolCallEvent;
                const key = t.tool_call_id || `${t.name}#${++fallbackKeyRef.current}`;
                const step: ToolStep = {
                  key,
                  name: t.name ?? "unknown",
                  arguments: t.arguments ?? {},
                  status: "running",
                  startedAt: Date.now(),
                };
                steps.push(step);
                dispatch({
                  type: "tool_call",
                  key: step.key,
                  name: step.name,
                  args: step.arguments,
                  startedAt: step.startedAt,
                });
                break;
              }
              case "tool_result": {
                if (!started) {
                  started = true;
                  dispatch({ type: "begin" });
                }
                const t = data as unknown as ToolResultEvent;
                const step = steps.find((s) => s.key === t.tool_call_id);
                if (step) {
                  step.status = t.is_error ? "error" : "done";
                  step.output = t.output ?? "";
                  step.finishedAt = Date.now();
                }
                dispatch({
                  type: "tool_result",
                  key: t.tool_call_id ?? "",
                  output: t.output ?? "",
                  isError: Boolean(t.is_error),
                  finishedAt: step?.finishedAt ?? Date.now(),
                });
                break;
              }
              case "artifact": {
                if (!started) {
                  started = true;
                  dispatch({ type: "begin" });
                }
                const a = data as unknown as ArtifactEvent;
                const artifact: LiveArtifact = { name: a.name, download_url: a.download_url };
                artifacts.push(artifact);
                dispatch({ type: "artifact", artifact });
                break;
              }
              case "done":
                dispatch({ type: "reset" });
                return finish(epoch, { kind: "done", text, toolSteps: steps, artifacts });
              case "interrupt": {
                const payload = (data as unknown as InterruptEvent).payload;
                dispatch({ type: "interrupt", payload });
                return finish(epoch, {
                  kind: "interrupt",
                  text,
                  toolSteps: steps,
                  artifacts,
                  interruptPayload: payload,
                });
              }
              case "error": {
                const message = (data as unknown as ErrorEvent).message ?? "生成失败";
                dispatch({ type: "error", message });
                // 保留错误发生前的部分文本
                return finish(epoch, {
                  kind: "error",
                  text,
                  toolSteps: steps,
                  artifacts,
                  errorMessage: message,
                });
              }
            }
          }
          // 流在 done/interrupt/error 之前正常关闭：用户停止或生产端退出
          const stoppedByUser = stopRequestedRef.current;
          stopRequestedRef.current = false;
          dispatch({ type: "stream_closed" });
          return finish(epoch, {
            kind: "stopped",
            text,
            toolSteps: steps,
            artifacts,
            stoppedByUser,
          });
        } catch (err) {
          client.abort();
          activeClientRef.current = null;
          const reason = abortReasonRef.current;
          abortReasonRef.current = null;
          // 抢占 / cancel 都会先作废 epoch → 旧消费者一律按抢占收场（与错误类型无关）
          if (epoch !== epochRef.current) throw new PreemptedError();
          if (reason === "unmount" || isAbortError(err)) {
            // 卸载或未知来源的 abort：静默收尾，不向 UI 报错
            dispatch({ type: "stream_closed" });
            return finish(epoch, { kind: "stopped", text, toolSteps: steps, artifacts });
          }
          // 网络中断：自动重连重放（后端生成与页面连接解耦）
          if (attempt >= MAX_RECONNECT) {
            dispatch({ type: "stream_closed" });
            return finish(epoch, { kind: "stopped", text, toolSteps: steps, artifacts });
          }
          await sleep(reconnectDelay);
          reconnectDelay *= 2;
          attempt += 1;
        }
      }
      // 注意：不主动 abort —— 各结束路径（done/interrupt/error/status）后端都会
      // 关闭响应，正常收尾才不会在 Network 面板留下 ERR_ABORTED 噪音。
    },
    [finish, release],
  );

  /** 发送消息。无会话时先创建会话；立即返回会话 id，生成结束时可 await wait。 */
  const send = useCallback(
    async (
      conversationId: string | null,
      content: string,
      imagePaths: string[],
    ): Promise<SendResult> => {
      let convId = conversationId;
      if (!convId) {
        const conv = await apiCreateConversation();
        convId = conv.id;
      }

      stopRequestedRef.current = false; // 新一轮生成，清掉上一轮的停止标记
      const op = ++operationRef.current;

      const attempt = () =>
        consume(
          `/conversations/${convId}/stream`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content, image_paths: imagePaths.length > 0 ? imagePaths : null }),
          },
          {
            lazyBegin: false,
            preempt: true,
            replayPath: `/conversations/${convId}/stream`,
          },
        ).then((r) =>
          "end" in r
            ? r.end
            : ({ kind: "stopped", text: "", toolSteps: [], artifacts: [] }) as GenerationEnd,
        );

      // 409：上一轮生成的 done 事件先于后端释放单飞锁到达，紧接着发送会撞上。
      // 退避后自动重试；持续冲突（如另一个标签页正在生成）则抛出给调用方提示。
      const wait = attempt().catch(async (err): Promise<GenerationEnd> => {
        if (!(err instanceof ApiError) || err.status !== 409) throw err;
        for (let i = 1; i <= 5; i++) {
          await sleep(400 * i);
          // 会话已切换（cancel 使操作失效）：中止重试，不再向旧会话发流
          if (operationRef.current !== op) throw new PreemptedError();
          try {
            return await attempt();
          } catch (retryErr) {
            if (!(retryErr instanceof ApiError) || retryErr.status !== 409) throw retryErr;
          }
        }
        throw err;
      });

      return { conversationId: convId, wait };
    },
    [consume],
  );

  /** HITL 续答：提交补充表单，继续同一条线程。 */
  const resume = useCallback(
    async (conversationId: string, answers: Record<string, string>): Promise<GenerationEnd> => {
      stopRequestedRef.current = false;
      const r = await consume(
        `/conversations/${conversationId}/resume`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers }),
        },
        {
          lazyBegin: false,
          preempt: true,
          replayPath: `/conversations/${conversationId}/stream`,
        },
      );
      if ("end" in r) return r.end;
      return { kind: "stopped", text: "", toolSteps: [], artifacts: [] };
    },
    [consume],
  );

  /** 重连：刷新后恢复进行中的生成（或确认没有运行中的生成）。 */
  const attach = useCallback(
    async (conversationId: string): Promise<AttachResult> => {
      const r = await consume(
        `/conversations/${conversationId}/stream`,
        { method: "GET" },
        { lazyBegin: true, preempt: false, replayPath: `/conversations/${conversationId}/stream` },
      );
      if ("end" in r) return { status: "ended", end: r.end };
      return { status: r.status };
    },
    [consume],
  );

  const stop = useCallback(async (conversationId: string) => {
    stopRequestedRef.current = true;
    // 通知后端停止即可；流会以 stream_closed 收尾
    await apiStopGeneration(conversationId);
  }, []);

  /**
   * 取消当前消费（用于会话切换）：中断 UI 侧的流并释放 busy。
   * 服务端生成不受影响——返回该会话时通过 attach() 重放恢复。
   */
  const cancel = useCallback(() => {
    operationRef.current += 1; // 中止挂起的 409 重试
    if (busyRef.current) {
      // 作废旧消费者：其收尾路径看到 epoch 失配，抛出 PreemptedError
      epochRef.current += 1;
      activeClientRef.current?.abort();
      busyRef.current = false;
      setBusy(false);
      activeClientRef.current = null;
    }
    dispatch({ type: "reset" });
  }, []);

  const dismissInterrupt = useCallback(() => dispatch({ type: "dismiss_interrupt" }), []);

  return { state, busy, send, resume, attach, stop, cancel, dismissInterrupt };
}
