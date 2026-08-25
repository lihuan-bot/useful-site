"use client";

/** 单条消息渲染：用户气泡（右侧）+ 助手消息（左侧带头像，Markdown）。 */

import { useState } from "react";
import type { LiveArtifact, ToolStep } from "@/hooks/use-chat-stream";
import { ArtifactList } from "./artifact-card";
import { Elapsed } from "./elapsed";
import { Markdown } from "./markdown";
import { ToolSteps } from "./tool-steps";
import { CheckIcon, CopyIcon, SparklesIcon } from "@/components/ui/icons";

export interface ChatEntryImage {
  url: string;
  path: string;
  name: string;
}

export interface ChatEntry {
  key: string;
  role: "user" | "assistant";
  content: string;
  images?: ChatEntryImage[];
  /** 助手消息是否完整（false 时显示「已暂停」） */
  isComplete: boolean;
  /** 生成失败标记 */
  error?: boolean;
  toolSteps?: ToolStep[];
  artifacts?: LiveArtifact[];
}

function AssistantAvatar() {
  return (
    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-white">
      <SparklesIcon width={14} height={14} />
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用时静默失败
    }
  };
  return (
    <button
      onClick={copy}
      className="flex items-center gap-1 rounded-md p-1 text-ink-3 opacity-0 transition-all hover:bg-black/[0.04] hover:text-ink group-hover:opacity-100"
      title="复制"
    >
      {copied ? <CheckIcon width={13} height={13} className="text-green-500" /> : <CopyIcon width={13} height={13} />}
    </button>
  );
}

export function MessageItem({ entry }: { entry: ChatEntry }) {
  if (entry.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%]">
          {entry.images && entry.images.length > 0 && (
            <div className="mb-2 flex flex-wrap justify-end gap-2">
              {entry.images.map((img) => (
                // eslint-disable-next-line @next/next/no-img-element -- 本地 blob 预览，next/image 不适用
                <img
                  key={img.path}
                  src={img.url}
                  alt={img.name}
                  className="h-20 w-20 rounded-lg border border-line object-cover"
                />
              ))}
            </div>
          )}
          <div className="whitespace-pre-wrap break-words rounded-2xl bg-user-bubble px-4 py-2.5 text-[14.5px] leading-relaxed text-ink">
            {entry.content}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex gap-3">
      <AssistantAvatar />
      <div className="min-w-0 flex-1">
        {entry.toolSteps && entry.toolSteps.length > 0 && (
          <ToolSteps steps={entry.toolSteps} className="mb-2.5" />
        )}

        {entry.error ? (
          <p className="text-[14.5px] leading-relaxed text-red-500">{entry.content}</p>
        ) : (
          <Markdown>{entry.content}</Markdown>
        )}

        {!entry.isComplete && !entry.error && (
          <span className="mt-1.5 inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-600">
            已暂停
          </span>
        )}

        {entry.artifacts && entry.artifacts.length > 0 && (
          <ArtifactList artifacts={entry.artifacts} className="mt-2.5" />
        )}

        {entry.isComplete && entry.content && (
          <div className="mt-1 flex h-6 items-center opacity-0 transition-opacity group-hover:opacity-100">
            <CopyButton text={entry.content} />
          </div>
        )}
      </div>
    </div>
  );
}

/** 流式输出中的助手消息（尚未落成条目） */
export function StreamingItem({
  text,
  steps,
  artifacts,
}: {
  text: string;
  steps: ToolStep[];
  artifacts: LiveArtifact[];
}) {
  // 挂载时刻作为「正在思考」的计时起点；重连时组件会重挂载，计时随之刷新
  const [liveSince] = useState(() => Date.now());
  const lastFinished = steps.reduce<number | null>(
    (max, s) => (s.finishedAt !== undefined ? Math.max(max ?? 0, s.finishedAt) : max),
    null,
  );
  const hasRunningStep = steps.some((s) => s.status === "running");

  return (
    <div className="flex gap-3">
      <AssistantAvatar />
      <div className="min-w-0 flex-1">
        <ToolSteps steps={steps} className="mb-2.5" />
        {text.length > 0 ? (
          <div className="streaming-cursor">
            <Markdown>{text}</Markdown>
          </div>
        ) : (
          // 无输出阶段：明确展示「工具执行 / 模型思考」的等待状态与耗时
          <div className="flex items-center gap-2 py-1.5 text-[13px] text-ink-3">
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-ink-3" />
              <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-ink-3 [animation-delay:0.2s]" />
              <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-ink-3 [animation-delay:0.4s]" />
            </span>
            <span>
              {hasRunningStep
                ? "正在等待工具执行完成"
                : lastFinished !== null
                  ? "正在组织回答"
                  : "正在思考"}
            </span>
            <Elapsed since={lastFinished ?? liveSince} />
          </div>
        )}
        <ArtifactList artifacts={artifacts} className="mt-2.5" />
      </div>
    </div>
  );
}
