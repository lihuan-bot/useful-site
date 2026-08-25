"use client";

/** 助手消息的 Markdown 渲染（GFM），正文样式见 globals.css 的 .md */

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { CheckIcon, CopyIcon } from "@/components/ui/icons";

function CodeBlock({ className, children }: { className?: string; children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const text = String(children ?? "").replace(/\n$/, "");
  // 有语言标记或含换行视为块级代码；行内代码单独样式
  const isBlock = Boolean(className?.includes("language-")) || text.includes("\n");

  if (!isBlock) {
    return <code className="md-inline-code">{children}</code>;
  }

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
    <div className="group/code relative my-3 overflow-hidden rounded-lg border border-line bg-[#f6f7f9]">
      <button
        onClick={copy}
        className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/[0.05] px-2 py-1 text-[11px] text-ink-3 opacity-0 transition-opacity hover:bg-black/[0.1] group-hover/code:opacity-100"
      >
        {copied ? <CheckIcon width={11} height={11} className="text-green-500" /> : <CopyIcon width={11} height={11} />}
        {copied ? "已复制" : "复制"}
      </button>
      <pre className="overflow-x-auto p-3.5 text-[13px] leading-relaxed">
        <code className={cn("font-mono", className)}>{text}</code>
      </pre>
    </div>
  );
}

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("md", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code: (props) => <CodeBlock {...props} />,
          a: (props) => (
            <a {...props} target="_blank" rel="noreferrer noopener" />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
