"use client";

import { cn } from "@/lib/utils";
import { CloseIcon } from "./icons";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  /** 宽度类名，默认 max-w-lg */
  widthClass?: string;
}

export function Modal({ open, title, onClose, children, footer, widthClass }: ModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div
        className={cn(
          "animate-fade-up relative z-10 flex max-h-[85vh] w-full flex-col rounded-xl bg-card shadow-xl",
          widthClass ?? "max-w-lg",
        )}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-ink-3 transition-colors hover:bg-black/[0.04] hover:text-ink"
            aria-label="关闭"
          >
            <CloseIcon width={16} height={16} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>
        )}
      </div>
    </div>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  text: string;
  confirmText?: string;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** 危险操作确认框（删除等） */
export function ConfirmDialog({
  open,
  title,
  text,
  confirmText = "删除",
  loading,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      widthClass="max-w-sm"
      footer={
        <>
          <button
            onClick={onCancel}
            disabled={loading}
            className="h-9 rounded-lg border border-line bg-card px-4 text-sm text-ink transition-colors hover:bg-page disabled:opacity-50"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-red-500 px-4 text-sm font-medium text-white transition-colors hover:bg-red-600 disabled:opacity-50"
          >
            {loading && (
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            )}
            {confirmText}
          </button>
        </>
      }
    >
      <p className="text-sm leading-relaxed text-ink-2">{text}</p>
    </Modal>
  );
}
