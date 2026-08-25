"use client";

/**
 * 文件工作区：agent 通过 write_file 生成的交付物（报告、数据文件等）都在这里，
 * 也可以手动上传文件。文件存储于 RustFS 的用户 /files/ 区域。
 */

import { useRef, useState } from "react";
import useSWR from "swr";
import { apiListFiles, apiUploadFile } from "@/lib/api/files";
import { apiUrl } from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import type { FileItem, Page } from "@/lib/api/types";
import { downloadWithAuth, formatBytes, formatRelativeTime } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { useToast } from "@/components/ui/toast";
import { DownloadIcon, FileTextIcon, FolderIcon, ImageIcon, UploadIcon } from "@/components/ui/icons";

/** 下载接口的路径参数按段编码（文件名含 / 和特殊字符） */
function downloadPath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

function fileIcon(name: string) {
  if (/\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(name)) {
    return <ImageIcon width={16} height={16} className="shrink-0 text-ink-3" />;
  }
  return <FileTextIcon width={16} height={16} className="shrink-0 text-ink-3" />;
}

export default function FilesPage() {
  const { show } = useToast();
  const { data, mutate, isLoading } = useSWR<Page<FileItem>>("files", apiListFiles);

  const [uploading, setUploading] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    let ok = 0;
    for (const file of Array.from(files)) {
      try {
        await apiUploadFile(file);
        ok += 1;
      } catch (err) {
        show(err instanceof ApiError ? `${file.name}: ${err.message}` : `${file.name} 上传失败`, "error");
      }
    }
    if (ok > 0) {
      mutate();
      show(`已上传 ${ok} 个文件`);
    }
    setUploading(false);
    if (uploadInputRef.current) uploadInputRef.current.value = "";
  };

  const handleDownload = async (item: FileItem) => {
    try {
      await downloadWithAuth(apiUrl(`/files/${downloadPath(item.name)}`), item.name.split("/").pop());
    } catch (err) {
      show(err instanceof Error ? err.message : "下载失败", "error");
    }
  };

  const items = data?.items ?? [];

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="文件"
        description="助手生成的报告、数据等交付物会保存在这里（单文件最大 10MB）"
        actions={
          <>
            <Button onClick={() => uploadInputRef.current?.click()} loading={uploading}>
              {!uploading && <UploadIcon width={14} height={14} />}
              上传文件
            </Button>
            <input
              ref={uploadInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => handleUpload(e.target.files)}
            />
          </>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <p className="py-12 text-center text-sm text-ink-3">加载中…</p>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<FolderIcon width={26} height={26} />}
            title="暂无文件"
            description="在对话中让助手生成文件（如周报、数据分析），或手动上传，都会出现在这里。"
            action={<Button onClick={() => uploadInputRef.current?.click()}>上传文件</Button>}
          />
        ) : (
          <div className="mx-auto max-w-4xl overflow-hidden rounded-xl border border-line bg-card">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line bg-page text-left text-xs text-ink-3">
                  <th className="px-4 py-2.5 font-medium">文件名</th>
                  <th className="w-24 px-3 py-2.5 font-medium">大小</th>
                  <th className="w-32 px-3 py-2.5 font-medium">修改时间</th>
                  <th className="w-16 px-3 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.name} className="group border-b border-line-soft last:border-b-0 hover:bg-page/60">
                    <td className="max-w-0 px-4 py-3">
                      <div className="flex min-w-0 items-center gap-2.5">
                        {fileIcon(item.name)}
                        <span className="truncate font-mono text-[13px]" title={item.name}>
                          {item.name}
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-3 text-xs text-ink-3">{formatBytes(item.size)}</td>
                    <td className="px-3 py-3 text-xs text-ink-3">
                      {item.last_modified ? formatRelativeTime(item.last_modified) : "—"}
                    </td>
                    <td className="px-3 py-3 text-right">
                      <button
                        onClick={() => handleDownload(item)}
                        className="rounded-md p-1.5 text-ink-3 opacity-0 transition-opacity hover:bg-primary-soft hover:text-primary group-hover:opacity-100"
                        title="下载"
                      >
                        <DownloadIcon width={14} height={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
