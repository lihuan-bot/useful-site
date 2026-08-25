"use client";

/** 会话列表侧边栏（豆包风格）：新建对话 + 列表（状态角标/删除）+ SSE 实时状态。 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { apiDeleteConversation, apiListConversations } from "@/lib/api/conversations";
import { ApiError } from "@/lib/api/client";
import type { Conversation, Page } from "@/lib/api/types";
import { useConversationEvents } from "@/hooks/use-conversation-events";
import { cn, formatRelativeTime } from "@/lib/utils";
import { ConfirmDialog } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { ChatIcon, LoaderIcon, PlusIcon, TrashIcon } from "@/components/ui/icons";

interface ConversationSidebarProps {
  currentId: string | null;
}

function StatusBadge({ running, awaitingInput, paused }: { running: boolean; awaitingInput: boolean; paused: boolean }) {
  if (running) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-primary">
        <LoaderIcon className="animate-spin" width={11} height={11} />
        生成中
      </span>
    );
  }
  if (awaitingInput) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-amber-500">
        <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-amber-500" />
        等待补充
      </span>
    );
  }
  if (paused) {
    return <span className="shrink-0 text-[11px] text-ink-3">已暂停</span>;
  }
  return null;
}

export function ConversationSidebar({ currentId }: ConversationSidebarProps) {
  const router = useRouter();
  const { show } = useToast();
  const { mutate } = useSWRConfig();
  // 30s 兜底轮询：状态 SSE 偶发断连（如隧道抖动）时列表标志不会永远卡在「生成中」
  const { data } = useSWR<Page<Conversation>>("conversations", () => apiListConversations(100), {
    refreshInterval: 30_000,
  });
  const { running, awaitingInput } = useConversationEvents();

  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const handleDelete = async () => {
    if (!deleting) return;
    setDeleteLoading(true);
    try {
      await apiDeleteConversation(deleting.id);
      mutate("conversations");
      show("会话已删除");
      if (deleting.id === currentId) router.push("/chat");
      setDeleting(null);
    } catch (err) {
      show(err instanceof ApiError ? err.message : "删除失败", "error");
    } finally {
      setDeleteLoading(false);
    }
  };

  const items = data?.items ?? [];

  return (
    <aside className="flex w-[260px] shrink-0 flex-col border-r border-line bg-card">
      <div className="p-3">
        <Link
          href="/chat"
          className={cn(
            "flex h-9 w-full items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition-colors",
            currentId === null
              ? "bg-primary-soft text-primary"
              : "bg-primary text-white hover:bg-primary-hover",
          )}
        >
          <PlusIcon width={15} height={15} />
          新建对话
        </Link>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {items.length === 0 ? (
          <p className="px-3 py-8 text-center text-xs text-ink-3">暂无对话，开始新的对话吧</p>
        ) : (
          <div className="flex flex-col gap-0.5">
            {items.map((conv) => {
              const active = conv.id === currentId;
              const isRunning = conv.streaming || running.has(conv.id);
              const isAwaiting = awaitingInput.has(conv.id);
              const isPaused = conv.interrupted && !isAwaiting;

              return (
                <div
                  key={conv.id}
                  className={cn(
                    "group relative flex items-center gap-2 rounded-lg px-3 py-2.5 transition-colors",
                    active ? "bg-primary-soft" : "hover:bg-page",
                  )}
                >
                  <Link href={`/chat?c=${conv.id}`} className="flex min-w-0 flex-1 flex-col">
                    <span
                      className={cn(
                        "truncate text-[13px]",
                        active ? "font-medium text-primary" : "text-ink",
                      )}
                    >
                      {conv.title || "新对话"}
                    </span>
                    <span className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-3">
                      <span>{formatRelativeTime(conv.updated_at)}</span>
                      <StatusBadge running={isRunning} awaitingInput={isAwaiting} paused={isPaused} />
                    </span>
                  </Link>
                  <button
                    onClick={() => setDeleting(conv)}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-ink-3 opacity-0 transition-opacity hover:bg-red-50 hover:text-red-500 group-hover:opacity-100"
                    title="删除会话"
                  >
                    <TrashIcon width={14} height={14} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="border-t border-line-soft p-3">
        <p className="flex items-center gap-1.5 text-[11px] text-ink-3">
          <ChatIcon width={12} height={12} />
          共 {data?.total ?? 0} 个会话
        </p>
      </div>

      <ConfirmDialog
        open={deleting !== null}
        title="删除会话"
        text={`确定删除「${deleting?.title || "新对话"}」吗？删除后不可恢复。`}
        loading={deleteLoading}
        onConfirm={handleDelete}
        onCancel={() => setDeleting(null)}
      />
    </aside>
  );
}
