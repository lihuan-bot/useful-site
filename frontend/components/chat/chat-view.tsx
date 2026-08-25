"use client";

/**
 * 聊天视图编排：会话侧边栏 + 顶部标题栏 + 消息流 + 输入区。
 *
 * 关键流程：
 * - 发送：无会话时先创建会话，然后用 history.replaceState 更新 URL 而不
 *   触发组件卸载——SSE 流不断，回复直接渲染在当前页面；
 * - 刷新恢复：进入会话时若列表标志显示有活动生成（或中断待续），通过
 *   attach() 重放事件日志；
 * - HITL：interrupt 事件 → 表单 → POST /resume 续答。
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { apiGetConversation, apiListConversations } from "@/lib/api/conversations";
import { apiUrl, ApiError } from "@/lib/api/client";
import type { Conversation, ConversationDetail, Message, Page } from "@/lib/api/types";
import {
  PreemptedError,
  useChatStream,
  type GenerationEnd,
} from "@/hooks/use-chat-stream";
import { useToast } from "@/components/ui/toast";
import { LoaderIcon, SparklesIcon } from "@/components/ui/icons";
import { ConversationSidebar } from "./conversation-sidebar";
import { Composer, type ComposerImage } from "./composer";
import { InterruptForm } from "./interrupt-form";
import { MessageList } from "./message-list";
import type { ChatEntry } from "./message-item";

const SUGGESTIONS = [
  "帮我联网搜索一下最近的热点新闻",
  "总结一个网页的内容（附链接）",
  "写一份工作周报并保存到文件",
];

/** 边界去重：DB 消息与本地乐观条目是否为同一条（乐观条目已被服务端镜像） */
function sameMessage(db: Message, entry: ChatEntry): boolean {
  if (db.role !== entry.role) return false;
  if (
    entry.role === "user" &&
    db.content.startsWith("[已补充信息]") &&
    entry.content.startsWith("[已补充信息]")
  ) {
    return true;
  }
  return db.content === entry.content;
}

export function ChatView({ conversationId }: { conversationId: string | null }) {
  const router = useRouter();
  const { show } = useToast();
  const { mutate: mutateList } = useSWRConfig();
  const { state, busy, send, resume, attach, stop, cancel, dismissInterrupt } = useChatStream();

  // 当前会话 id：初始来自路由 prop；首次发送创建会话后由本组件更新。
  // 路由 prop 变化（侧边栏切换 / 前进后退）时同步并重置会话内状态；
  // 首次发送的内部导航（router.replace）不重置——SSE 流要继续。
  const [prevConversationId, setPrevConversationId] = useState(conversationId);
  const [currentId, setCurrentId] = useState<string | null>(conversationId);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [images, setImages] = useState<ComposerImage[]>([]);
  const [attachMode, setAttachMode] = useState(false);
  const [attachAttempted, setAttachAttempted] = useState(false);
  const [resumeSubmitting, setResumeSubmitting] = useState(false);
  /** 会话由本次页面会话创建（首条消息）：展示只依赖本地条目，忽略 DB 镜像 */
  const [sessionOwned, setSessionOwned] = useState(false);
  /** 首条发送触发的内部导航标记：prop 同步时跳过会话重置、不取消流 */
  const [internalNav, setInternalNav] = useState(false);

  if (conversationId !== prevConversationId) {
    setPrevConversationId(conversationId);
    setCurrentId(conversationId);
    if (!internalNav) {
      // 用户导航（侧边栏 / 新建对话 / 前进后退）：重置会话内状态
      setEntries([]);
      setDraft("");
      setImages([]);
      setAttachMode(false);
      setAttachAttempted(false);
      setSessionOwned(false);
    }
  }

  // 会话切换的副作用：用户导航时取消上一个会话的 UI 流
  // （后端继续生成，侧边栏显示「生成中」，返回该会话时重放恢复）；
  // 内部导航只消费标记，流继续。
  const prevCurrentIdRef = useRef(currentId);
  useEffect(() => {
    if (prevCurrentIdRef.current === currentId) return;
    prevCurrentIdRef.current = currentId;
    void (async () => {
      if (internalNav) {
        setInternalNav(false);
      } else {
        cancel();
      }
    })();
  }, [currentId, internalNav, cancel]);

  const { data: detail, mutate: mutateDetail } = useSWR<ConversationDetail>(
    currentId ? `detail:${currentId}` : null,
    () => apiGetConversation(currentId!),
  );
  // 与侧边栏共享的会话列表（同一 SWR key，不会重复请求）。
  // 详情接口不带 streaming 标志，重连判断依赖列表里的实时标志。
  const { data: list } = useSWR<Page<Conversation>>("conversations", () => apiListConversations(100));

  const localId = useRef(0);
  const detailRef = useRef<ConversationDetail | undefined>(undefined);
  // 供 handleGenerationEnd 读取最新详情（去重判断），ref 只能在 effect 中更新
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const handleGenerationEnd = useCallback(
    (end: GenerationEnd) => {
      // 会话列表刷新（标题 / 排序 / 状态标记）
      mutateList("conversations");
      const last = detailRef.current?.messages.at(-1);
      const append = (entry: Omit<ChatEntry, "key">) =>
        setEntries((prev) => [...prev, { ...entry, key: `a-${++localId.current}` }]);

      switch (end.kind) {
        case "done": {
          if (!end.text) return;
          // attach 重放了一条已入库的消息：与 DB 最后一条一致则跳过，避免重复
          if (last?.role === "assistant" && last.is_complete && last.content === end.text) return;
          append({
            role: "assistant",
            content: end.text,
            isComplete: true,
            toolSteps: end.toolSteps,
            artifacts: end.artifacts,
          });
          break;
        }
        case "interrupt":
          if (end.text) {
            append({
              role: "assistant",
              content: end.text,
              isComplete: false,
              toolSteps: end.toolSteps,
              artifacts: end.artifacts,
            });
          }
          break;
        case "error":
          append({
            role: "assistant",
            content: end.text
              ? `${end.text}\n\n⚠️ 生成失败：${end.errorMessage ?? "未知错误"}`
              : `⚠️ 生成失败：${end.errorMessage ?? "未知错误"}`,
            isComplete: false,
            error: true,
            toolSteps: end.toolSteps,
            artifacts: end.artifacts,
          });
          break;
        case "stopped":
          if (end.text || end.toolSteps.length > 0) {
            append({
              role: "assistant",
              content: end.text,
              isComplete: false,
              toolSteps: end.toolSteps,
              artifacts: end.artifacts,
            });
          } else if (!end.stoppedByUser) {
            // 网络中断且该轮已结束/事件日志过期：完整内容后端已入库，从 DB 恢复
            setSessionOwned(false);
            mutateDetail();
            show("连接中断，已恢复完整内容");
          }
          break;
      }
    },
    [mutateList, mutateDetail, show],
  );

  // 进入会话时，仅当列表标志显示有活动生成（或中断待续）才重连一次。
  // 无条件重连会重放 10 分钟内刚完成的历史流，造成最后一条回答"重新打字"。
  useEffect(() => {
    if (!currentId || attachAttempted || !list) return;

    const flags = list.items.find((c) => c.id === currentId);
    if (!flags?.streaming && !flags?.interrupted) return;

    (async () => {
      // 标记已尝试（避免列表刷新导致重复重连）；丢弃 DB 末尾未完成的助手消息由重放补全
      setAttachAttempted(true);
      setAttachMode(true);
      try {
        let result = await attach(currentId);
        let tries = 0;
        while (result.status === "pending" && tries < 8) {
          await new Promise((r) => setTimeout(r, 1000));
          result = await attach(currentId);
          tries += 1;
        }
        if (result.status !== "ended") setAttachMode(false);
        if (result.end) handleGenerationEnd(result.end);
      } catch {
        // 重连失败（含被发送抢占）：按 DB 内容正常渲染
        setAttachMode(false);
      }
    })();
    // attach / handleGenerationEnd 为稳定引用；此效果只在会话切换时执行
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, list, attachAttempted]);

  const handleSend = useCallback(async () => {
    if (busy || state.phase === "streaming") return;
    const content = draft.trim();
    if (!content && images.length === 0) return;

    const prevDraft = draft;
    const prevImages = images;
    const entryKey = `u-${++localId.current}`;
    setEntries((prev) => [
      ...prev,
      {
        key: entryKey,
        role: "user",
        content,
        images: images.map((i) => ({ url: i.previewUrl, path: i.path, name: i.name })),
        isComplete: true,
      },
    ]);
    setDraft("");
    setImages([]);

    try {
      const { conversationId: newId, wait } = await send(
        currentId,
        content,
        images.map((i) => i.path),
      );
      if (newId && newId !== currentId) {
        // 新会话：router.replace 到同一页面的查询参数（/chat → /chat?c={id}）。
        // searchParams 变化只重渲染不重挂载，SSE 流不断；
        // internalNav 让 prop 同步跳过会话重置
        setInternalNav(true);
        setSessionOwned(true);
        router.replace(`/chat?c=${newId}`);
        mutateList("conversations");
      }
      const end = await wait;
      handleGenerationEnd(end);
    } catch (err) {
      if (err instanceof PreemptedError) return; // 被更新的发送抢占，静默
      setEntries((prev) => prev.filter((e) => e.key !== entryKey));
      setDraft(prevDraft);
      setImages(prevImages);
      show(err instanceof ApiError ? err.message : "发送失败", "error");
    }
  }, [busy, state.phase, draft, images, send, currentId, router, mutateList, handleGenerationEnd, show]);

  const handleResume = useCallback(
    async (answers: Record<string, string>) => {
      if (!currentId) return;
      setResumeSubmitting(true);
      const entryKey = `r-${++localId.current}`;
      const summary = Object.entries(answers)
        .map(([k, v]) => `${k}：${v}`)
        .join(", ");
      setEntries((prev) => [
        ...prev,
        { key: entryKey, role: "user", content: `已补充信息：${summary}`, isComplete: true },
      ]);
      try {
        const end = await resume(currentId, answers);
        handleGenerationEnd(end);
      } catch (err) {
        if (err instanceof PreemptedError) return;
        setEntries((prev) => prev.filter((e) => e.key !== entryKey));
        show(err instanceof ApiError ? err.message : "提交失败", "error");
      } finally {
        setResumeSubmitting(false);
      }
    },
    [currentId, resume, handleGenerationEnd, show],
  );

  const handleStop = useCallback(async () => {
    if (!currentId) return;
    try {
      await stop(currentId);
    } catch (err) {
      show(err instanceof ApiError ? err.message : "停止失败", "error");
    }
  }, [currentId, stop, show]);

  // 展示列表 = DB 消息（重连时去掉末尾未完成的助手消息，由重放补全）+ 本页新增条目，
  // 边界去重消除「乐观条目已被服务端镜像入库」造成的重复。
  // sessionOwned（本页创建的会话）时忽略 DB 镜像——镜像有 2 秒延迟且条目已完整。
  const displayEntries = useMemo<ChatEntry[]>(() => {
    let db = sessionOwned ? [] : detail?.messages ?? [];
    if (attachMode) {
      const last = db.at(-1);
      if (last && last.role === "assistant" && !last.is_complete) {
        db = db.slice(0, -1);
      }
    }
    const session = [...entries];
    while (db.length > 0 && session.length > 0) {
      if (sameMessage(db[db.length - 1], session[0])) {
        db = db.slice(0, -1);
        session.shift();
      } else {
        break;
      }
    }
    return [
      ...db.map((m) => ({
        key: m.id,
        role: m.role,
        content: m.content,
        isComplete: m.is_complete,
        // 持久化的交付物路径 → 下载卡片（刷新后仍可见）
        artifacts: (m.artifacts ?? []).map((path) => ({
          name: path.replace(/^\/files\//, ""),
          download_url: apiUrl(path),
        })),
      })),
      ...session,
    ];
  }, [detail, entries, attachMode, sessionOwned]);

  const showLive =
    state.phase === "streaming" &&
    (state.text.length > 0 || state.toolSteps.length > 0 || state.artifacts.length > 0);

  const tail =
    state.phase === "awaiting_input" && state.interrupt ? (
      <InterruptForm
        payload={state.interrupt}
        submitting={resumeSubmitting}
        onSubmit={handleResume}
        onDismiss={dismissInterrupt}
      />
    ) : undefined;

  // 欢迎态：新对话，或已加载但没有任何消息的会话
  const showEmpty = currentId === null || (detail !== undefined && detail.messages.length === 0);
  const empty = showEmpty ? (
    <div className="flex h-full flex-col items-center justify-center gap-6 px-6">
      <div className="flex h-16 w-16 items-center justify-center rounded-3xl bg-primary text-white shadow-lg shadow-primary/25">
        <SparklesIcon width={30} height={30} />
      </div>
      <div className="text-center">
        <h2 className="text-xl font-semibold">你好，我是智能助手</h2>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-3">
          联网搜索、网页阅读、订单填写、知识库问答、生成文件，都可以直接问我
        </p>
      </div>
      <div className="grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-3">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => setDraft(s)}
            className="rounded-xl border border-line bg-card px-3 py-2.5 text-left text-[13px] leading-relaxed text-ink-2 transition-colors hover:border-primary/40 hover:bg-primary-softer"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  ) : undefined;

  return (
    <div className="flex h-full">
      <ConversationSidebar currentId={currentId} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-center border-b border-line-soft bg-card/80 px-4 backdrop-blur">
          <h1 className="max-w-[40vw] truncate text-sm font-medium text-ink">
            {detail?.title || "新对话"}
          </h1>
          {state.phase === "streaming" && (
            <span className="ml-2 flex items-center gap-1 text-xs text-primary">
              <LoaderIcon className="animate-spin" width={12} height={12} />
              正在生成
            </span>
          )}
          {state.phase === "awaiting_input" && (
            <span className="ml-2 text-xs text-amber-500">等待补充信息</span>
          )}
        </header>

        <MessageList
          entries={displayEntries}
          streaming={state.phase === "streaming"}
          liveText={showLive ? state.text : ""}
          liveSteps={showLive ? state.toolSteps : []}
          liveArtifacts={showLive ? state.artifacts : []}
          tail={tail}
          empty={empty}
        />

        <Composer
          streaming={state.phase === "streaming"}
          awaitingInput={state.phase === "awaiting_input"}
          draft={draft}
          onDraftChange={setDraft}
          images={images}
          onImagesChange={setImages}
          onSend={handleSend}
          onStop={handleStop}
        />
      </div>
    </div>
  );
}
