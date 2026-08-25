"use client";

/**
 * 聊天页：/chat 为新对话，/chat?c={id} 为具体会话。
 *
 * 会话 id 用查询参数而不是动态路由段——Next 保证 searchParams 变化
 * 只重渲染、不重挂载页面组件（动态段参数变化会重挂载，进而 abort 掉
 * 正在进行的 SSE 流），首次发送 router.replace('/chat?c={id}') 时
 * 流不断开（详见 chat-view.tsx 与 2026-08-24 的线上复现记录）。
 */

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ChatView } from "@/components/chat/chat-view";

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatPageInner />
    </Suspense>
  );
}

function ChatPageInner() {
  const searchParams = useSearchParams();
  const conversationId = searchParams.get("c");
  return <ChatView conversationId={conversationId} />;
}
