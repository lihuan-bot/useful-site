"use client";

/** 消息列表：滚动容器 + 自动吸底（用户上滑查看历史时不打扰） */

import { useEffect, useRef, useState, type UIEvent } from "react";
import type { LiveArtifact, ToolStep } from "@/hooks/use-chat-stream";
import { MessageItem, StreamingItem, type ChatEntry } from "./message-item";

interface MessageListProps {
  entries: ChatEntry[];
  streaming: boolean;
  liveText: string;
  liveSteps: ToolStep[];
  liveArtifacts: LiveArtifact[];
  /** 渲染在消息流末尾（HITL 表单） */
  tail?: React.ReactNode;
  /** 无任何消息时的欢迎态 */
  empty?: React.ReactNode;
}

export function MessageList({
  entries,
  streaming,
  liveText,
  liveSteps,
  liveArtifacts,
  tail,
  empty,
}: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stickBottom, setStickBottom] = useState(true);

  const handleScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    setStickBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 120);
  };

  // 新内容时若在底部附近则自动滚动
  useEffect(() => {
    if (stickBottom) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }
  }, [entries.length, liveText, liveSteps.length, liveArtifacts.length, tail, stickBottom]);

  const showLive = streaming && (liveText.length > 0 || liveSteps.length > 0 || liveArtifacts.length > 0);
  const showTyping = streaming && !showLive;

  if (entries.length === 0 && !showLive && !showTyping && !tail) {
    return (
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {empty}
      </div>
    );
  }

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6">
        {entries.map((entry) => (
          <MessageItem key={entry.key} entry={entry} />
        ))}
        {showLive && (
          <StreamingItem text={liveText} steps={liveSteps} artifacts={liveArtifacts} />
        )}
        {showTyping && (
          <StreamingItem text="" steps={[]} artifacts={[]} />
        )}
        {tail}
      </div>
    </div>
  );
}
