"use client";

/** 工具调用步骤卡（豆包风格：生成过程中的「正在搜索…」小卡片，可展开查看细节） */

import { useState } from "react";
import type { ToolStep } from "@/hooks/use-chat-stream";
import { cn } from "@/lib/utils";
import { Elapsed } from "./elapsed";
import {
  AlertIcon,
  BookIcon,
  CartIcon,
  CheckIcon,
  ChevronDownIcon,
  FileTextIcon,
  GlobeIcon,
  LinkIcon,
  LoaderIcon,
  ZapIcon,
} from "@/components/ui/icons";

const TOOL_META: Record<string, { label: string; icon: React.ComponentType<{ width?: number; height?: number; className?: string }> }> = {
  web_search: { label: "联网搜索", icon: GlobeIcon },
  fetch_url: { label: "读取网页", icon: LinkIcon },
  submit_order: { label: "提交订单", icon: CartIcon },
  search_knowledge_base: { label: "知识库检索", icon: BookIcon },
  write_file: { label: "写入文件", icon: FileTextIcon },
};

function toolMeta(name: string) {
  return TOOL_META[name] ?? { label: name, icon: ZapIcon };
}

/** 从参数里挑一个用于摘要展示的值（query / url 优先） */
function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  const preferred = entries.find(([k]) => k === "query" || k === "url" || k === "file_path");
  const [k, v] = preferred ?? entries[0];
  const s = typeof v === "string" ? v : JSON.stringify(v);
  const label = k === "query" || k === "url" || k === "file_path" ? "" : `${k}: `;
  return `${label}${s.length > 60 ? `${s.slice(0, 60)}…` : s}`;
}

function ToolStepCard({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(false);
  const meta = toolMeta(step.name);
  const Icon = meta.icon;
  const summary = summarizeArgs(step.arguments);

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-card">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-page"
      >
        <span
          className={cn(
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
            step.status === "error" ? "bg-red-50 text-red-500" : "bg-primary-soft text-primary",
          )}
        >
          <Icon width={13} height={13} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-[13px]">
            <span className="font-medium text-ink">{meta.label}</span>
            <span
              className={cn(
                "flex items-center gap-1 text-[11px]",
                step.status === "running" && "text-primary",
                step.status === "done" && "text-green-600",
                step.status === "error" && "text-red-500",
              )}
            >
              {step.status === "running" && (
                <>
                  正在执行
                  <Elapsed since={step.startedAt} />
                </>
              )}
              {step.status === "done" && "已完成"}
              {step.status === "error" && "执行出错"}
            </span>
          </span>
          {summary && <span className="block truncate text-xs text-ink-3">{summary}</span>}
        </span>
        {step.status === "running" ? (
          <LoaderIcon className="shrink-0 animate-spin text-primary" width={14} height={14} />
        ) : step.status === "done" ? (
          <CheckIcon className="shrink-0 text-green-600" width={14} height={14} />
        ) : (
          <AlertIcon className="shrink-0 text-red-500" width={14} height={14} />
        )}
        <ChevronDownIcon
          width={13}
          height={13}
          className={cn("shrink-0 text-ink-3 transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (step.arguments || step.output) && (
        <div className="border-t border-line-soft bg-page/60 px-3 py-2.5 text-xs">
          {Object.keys(step.arguments ?? {}).length > 0 && (
            <div className="mb-2">
              <p className="mb-1 font-medium text-ink-3">参数</p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-card p-2 font-mono text-ink-2">
                {JSON.stringify(step.arguments, null, 2)}
              </pre>
            </div>
          )}
          {step.output && (
            <div>
              <p className="mb-1 font-medium text-ink-3">结果</p>
              <pre
                className={cn(
                  "max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-card p-2 font-mono",
                  step.status === "error" ? "text-red-500" : "text-ink-2",
                )}
              >
                {step.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ToolSteps({ steps, className }: { steps: ToolStep[]; className?: string }) {
  if (steps.length === 0) return null;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {steps.map((step) => (
        <ToolStepCard key={step.key} step={step} />
      ))}
    </div>
  );
}
