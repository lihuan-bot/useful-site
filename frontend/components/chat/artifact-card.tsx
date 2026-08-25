"use client";

/** agent 写入 /files/ 的产物卡片（SSE artifact 事件），可点击下载 */

import { useState } from "react";
import type { LiveArtifact } from "@/hooks/use-chat-stream";
import { downloadWithAuth, cn } from "@/lib/utils";
import { DownloadIcon, FileTextIcon, LoaderIcon } from "@/components/ui/icons";

export function ArtifactCard({ artifact }: { artifact: LiveArtifact }) {
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await downloadWithAuth(artifact.download_url, artifact.name);
    } catch {
      // 下载失败保持静默，按钮恢复可重试
    } finally {
      setDownloading(false);
    }
  };

  return (
    <button
      onClick={handleDownload}
      disabled={downloading}
      className={cn(
        "group flex w-full items-center gap-3 rounded-lg border border-line bg-card px-3 py-2.5",
        "text-left transition-colors hover:border-primary/40 hover:bg-primary-softer",
        "disabled:cursor-wait",
      )}
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
        <FileTextIcon width={15} height={15} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-ink">{artifact.name}</span>
        <span className="text-xs text-ink-3">agent 生成的文件</span>
      </span>
      {downloading ? (
        <LoaderIcon className="shrink-0 animate-spin text-ink-3" width={15} height={15} />
      ) : (
        <DownloadIcon
          width={15}
          height={15}
          className="shrink-0 text-ink-3 transition-colors group-hover:text-primary"
        />
      )}
    </button>
  );
}

export function ArtifactList({ artifacts, className }: { artifacts: LiveArtifact[]; className?: string }) {
  if (artifacts.length === 0) return null;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {artifacts.map((a) => (
        <ArtifactCard key={`${a.name}-${a.download_url}`} artifact={a} />
      ))}
    </div>
  );
}
