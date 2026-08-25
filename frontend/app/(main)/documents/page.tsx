"use client";

/**
 * 知识库文档管理：上传（PDF/DOCX/TXT）→ 后台向量化索引 → 模型可检索问答。
 * 上传接口返回 202，索引状态通过轮询列表更新（有处理中的文档时每 3 秒刷新）。
 */

import { useRef, useState } from "react";
import useSWR from "swr";
import { apiDeleteDocument, apiListDocuments, apiUploadDocument } from "@/lib/api/documents";
import { ApiError } from "@/lib/api/client";
import type { DocumentItem, Page } from "@/lib/api/types";
import { cn, formatBytes, formatRelativeTime } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { useToast } from "@/components/ui/toast";
import { AlertIcon, BookIcon, FileTextIcon, LoaderIcon, TrashIcon, UploadIcon } from "@/components/ui/icons";

const STATUS_META: Record<string, { label: string; className: string; spinning?: boolean }> = {
  pending: { label: "排队中", className: "bg-amber-50 text-amber-600" },
  processing: { label: "解析中", className: "bg-blue-50 text-blue-600", spinning: true },
  ready: { label: "已完成", className: "bg-green-50 text-green-600" },
  failed: { label: "失败", className: "bg-red-50 text-red-500" },
};

const ACCEPT = ".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";

function StatusBadge({ doc }: { doc: DocumentItem }) {
  const meta = STATUS_META[doc.status] ?? { label: doc.status, className: "bg-page text-ink-3" };
  return (
    <span
      title={doc.error ?? undefined}
      className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px]", meta.className)}
    >
      {meta.spinning && <LoaderIcon className="animate-spin" width={10} height={10} />}
      {doc.status === "failed" && <AlertIcon width={10} height={10} />}
      {meta.label}
    </span>
  );
}

export default function DocumentsPage() {
  const { show } = useToast();
  const { data, mutate, isLoading } = useSWR<Page<DocumentItem>>("documents", () => apiListDocuments(100), {
    // 有处理中的文档时轮询，全部完成后停止
    refreshInterval: (latest) =>
      latest?.items.some((d) => d.status === "pending" || d.status === "processing") ? 3000 : 0,
  });

  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState<DocumentItem | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    let ok = 0;
    for (const file of Array.from(files)) {
      try {
        await apiUploadDocument(file);
        ok += 1;
      } catch (err) {
        show(err instanceof ApiError ? `${file.name}: ${err.message}` : `${file.name} 上传失败`, "error");
      }
    }
    if (ok > 0) {
      mutate();
      show(`已上传 ${ok} 个文档，正在后台解析`);
    }
    setUploading(false);
    if (uploadInputRef.current) uploadInputRef.current.value = "";
  };

  const handleDelete = async () => {
    if (!deleting) return;
    setDeleteLoading(true);
    try {
      await apiDeleteDocument(deleting.id);
      mutate();
      show(`已删除「${deleting.filename}」`);
      setDeleting(null);
    } catch (err) {
      show(err instanceof ApiError ? err.message : "删除失败", "error");
    } finally {
      setDeleteLoading(false);
    }
  };

  const items = data?.items ?? [];
  const hasActive = items.some((d) => d.status === "pending" || d.status === "processing");

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="知识库"
        description="上传文档建立知识库，对话中模型可检索引用（支持 PDF / DOCX / TXT）"
        actions={
          <>
            <Button onClick={() => uploadInputRef.current?.click()} loading={uploading}>
              {!uploading && <UploadIcon width={14} height={14} />}
              上传文档
            </Button>
            <input
              ref={uploadInputRef}
              type="file"
              accept={ACCEPT}
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
            icon={<BookIcon width={26} height={26} />}
            title="知识库为空"
            description="上传 PDF、Word 或文本文件，解析完成后即可在对话中提问文档内容。"
            action={<Button onClick={() => uploadInputRef.current?.click()}>上传文档</Button>}
          />
        ) : (
          <div className="mx-auto max-w-4xl">
            {hasActive && (
              <p className="mb-3 flex items-center gap-1.5 text-xs text-blue-600">
                <LoaderIcon className="animate-spin" width={12} height={12} />
                有文档正在解析中，完成后将自动更新
              </p>
            )}
            <div className="overflow-hidden rounded-xl border border-line bg-card">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line bg-page text-left text-xs text-ink-3">
                    <th className="px-4 py-2.5 font-medium">文件名</th>
                    <th className="w-24 px-3 py-2.5 font-medium">大小</th>
                    <th className="w-24 px-3 py-2.5 font-medium">状态</th>
                    <th className="w-20 px-3 py-2.5 font-medium">分块</th>
                    <th className="w-28 px-3 py-2.5 font-medium">上传时间</th>
                    <th className="w-14 px-3 py-2.5" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((doc) => (
                    <tr key={doc.id} className="group border-b border-line-soft last:border-b-0 hover:bg-page/60">
                      <td className="max-w-0 px-4 py-3">
                        <div className="flex min-w-0 items-center gap-2.5">
                          <FileTextIcon width={16} height={16} className="shrink-0 text-ink-3" />
                          <span className="truncate text-[13px]">{doc.filename}</span>
                        </div>
                        {doc.error && (
                          <p className="mt-0.5 truncate pl-[26px] text-xs text-red-400">{doc.error}</p>
                        )}
                      </td>
                      <td className="px-3 py-3 text-xs text-ink-3">{formatBytes(doc.size_bytes)}</td>
                      <td className="px-3 py-3">
                        <StatusBadge doc={doc} />
                      </td>
                      <td className="px-3 py-3 text-xs text-ink-3">{doc.status === "ready" ? doc.chunk_count : "—"}</td>
                      <td className="px-3 py-3 text-xs text-ink-3">{formatRelativeTime(doc.created_at)}</td>
                      <td className="px-3 py-3 text-right">
                        <button
                          onClick={() => setDeleting(doc)}
                          className="rounded-md p-1.5 text-ink-3 opacity-0 transition-opacity hover:bg-red-50 hover:text-red-500 group-hover:opacity-100"
                          title="删除"
                        >
                          <TrashIcon width={14} height={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={deleting !== null}
        title="删除文档"
        text={`确定删除「${deleting?.filename}」吗？删除后知识库中将无法再检索到它。`}
        loading={deleteLoading}
        onConfirm={handleDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
