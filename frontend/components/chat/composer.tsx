"use client";

/** 输入区（豆包风格）：居中卡片、自动增高输入框、图片上传、发送/停止按钮。 */

import { useRef, useState, type KeyboardEvent } from "react";
import { apiUploadFile } from "@/lib/api/files";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { CloseIcon, ImageIcon, LoaderIcon, SendIcon, StopIcon } from "@/components/ui/icons";

export interface ComposerImage {
  /** 发送给后端的虚拟路径（/files/... 前缀） */
  path: string;
  name: string;
  previewUrl: string;
}

interface ComposerProps {
  streaming: boolean;
  awaitingInput: boolean;
  draft: string;
  onDraftChange: (v: string) => void;
  images: ComposerImage[];
  onImagesChange: (images: ComposerImage[]) => void;
  onSend: () => void;
  onStop: () => void;
}

export function Composer({
  streaming,
  awaitingInput,
  draft,
  onDraftChange,
  images,
  onImagesChange,
  onSend,
  onStop,
}: ComposerProps) {
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { show } = useToast();

  const canSend = !streaming && (draft.trim().length > 0 || images.length > 0) && !uploading;

  const pickImages = () => fileInputRef.current?.click();

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    const added: ComposerImage[] = [];
    for (const file of Array.from(files)) {
      if (!file.type.startsWith("image/")) {
        show("仅支持图片文件", "error");
        continue;
      }
      try {
        const uploaded = await apiUploadFile(file);
        added.push({
          path: `/files${uploaded.path}`,
          name: uploaded.name,
          previewUrl: URL.createObjectURL(file),
        });
      } catch (err) {
        show(err instanceof ApiError ? err.message : `上传 ${file.name} 失败`, "error");
      }
    }
    if (added.length > 0) onImagesChange([...images, ...added]);
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canSend) onSend();
    }
  };

  const removeImage = (path: string) => {
    onImagesChange(images.filter((img) => img.path !== path));
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-6">
      {awaitingInput && (
        <p className="mb-2 text-center text-xs text-amber-600">
          等待补充信息 —— 请在下方表单中填写，或直接输入消息继续
        </p>
      )}

      <div className="rounded-2xl border border-line bg-card shadow-sm transition-colors focus-within:border-primary/60">
        {images.length > 0 && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {images.map((img) => (
              <div key={img.path} className="group relative">
                {/* eslint-disable-next-line @next/next/no-img-element -- 本地 blob 预览，next/image 不适用 */}
                <img
                  src={img.previewUrl}
                  alt={img.name}
                  className="h-16 w-16 rounded-lg border border-line object-cover"
                />
                <button
                  onClick={() => removeImage(img.path)}
                  className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-slate-800 text-white opacity-0 shadow transition-opacity group-hover:opacity-100"
                  aria-label="移除图片"
                >
                  <CloseIcon width={10} height={10} />
                </button>
              </div>
            ))}
          </div>
        )}

        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息…（Enter 发送，Shift+Enter 换行）"
          rows={1}
          className="block max-h-40 w-full resize-none bg-transparent px-4 pb-2 pt-3 text-[14.5px] leading-relaxed outline-none placeholder:text-ink-3"
        />

        <div className="flex items-center justify-between px-2.5 pb-2.5">
          <div className="flex items-center gap-0.5">
            <button
              onClick={pickImages}
              disabled={streaming || uploading}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-3 transition-colors hover:bg-black/[0.04] hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              title="上传图片"
            >
              {uploading ? <LoaderIcon className="animate-spin" width={17} height={17} /> : <ImageIcon width={16} height={16} />}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => handleFiles(e.target.files)}
            />
          </div>

          {streaming ? (
            <button
              onClick={onStop}
              className={cn(
                "flex h-9 w-9 items-center justify-center rounded-xl text-white transition-colors",
                "bg-red-500 hover:bg-red-600",
              )}
              title="停止生成"
            >
              <StopIcon width={15} height={15} />
            </button>
          ) : (
            <button
              onClick={onSend}
              disabled={!canSend}
              className={cn(
                "flex h-9 w-9 items-center justify-center rounded-xl text-white transition-all",
                canSend
                  ? "bg-primary shadow-sm hover:bg-primary-hover"
                  : "cursor-not-allowed bg-ink-3/40",
              )}
              title="发送"
            >
              <SendIcon width={16} height={16} />
            </button>
          )}
        </div>
      </div>

      <p className="mt-2 text-center text-[11px] text-ink-3">
        内容由 AI 生成，请注意甄别
      </p>
    </div>
  );
}
