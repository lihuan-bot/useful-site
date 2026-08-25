"use client";

/** 技能管理：列表 / 新建 / 编辑 / 导入 / 删除。技能文件存于 RustFS，由后端中间件按需注入模型。 */

import { useRef, useState } from "react";
import useSWR from "swr";
import { apiDeleteSkill, apiImportSkill, apiListSkills } from "@/lib/api/skills";
import { ApiError } from "@/lib/api/client";
import type { Page, Skill } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { useToast } from "@/components/ui/toast";
import { AlertIcon, EditIcon, TrashIcon, UploadIcon, ZapIcon } from "@/components/ui/icons";
import { SkillEditorModal, type SkillEditorState } from "@/components/skills/skill-editor-modal";

export default function SkillsPage() {
  const { show } = useToast();
  const { data, mutate, isLoading } = useSWR<Page<Skill>>("skills", apiListSkills);

  const [editor, setEditor] = useState<SkillEditorState | null>(null);
  const [deleting, setDeleting] = useState<Skill | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);

  const handleImport = async (file: File | undefined) => {
    if (!file) return;
    try {
      await apiImportSkill(file);
      mutate();
      show(`已导入技能`);
    } catch (err) {
      show(err instanceof ApiError ? err.message : "导入失败", "error");
    } finally {
      if (importInputRef.current) importInputRef.current.value = "";
    }
  };

  const handleDelete = async () => {
    if (!deleting) return;
    setDeleteLoading(true);
    try {
      await apiDeleteSkill(deleting.name);
      mutate();
      show(`已删除技能「${deleting.name}」`);
      setDeleting(null);
    } catch (err) {
      show(err instanceof ApiError ? err.message : "删除失败", "error");
    } finally {
      setDeleteLoading(false);
    }
  };

  const items = data?.items ?? [];

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="技能"
        description="技能以 SKILL.md 形式保存，模型对话时会按需读取并执行"
        actions={
          <>
            <Button variant="outline" onClick={() => importInputRef.current?.click()}>
              <UploadIcon width={14} height={14} />
              导入技能
            </Button>
            <Button onClick={() => setEditor({ mode: "create" })}>新建技能</Button>
            <input
              ref={importInputRef}
              type="file"
              accept=".md,.markdown,.txt"
              className="hidden"
              onChange={(e) => handleImport(e.target.files?.[0])}
            />
          </>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <p className="py-12 text-center text-sm text-ink-3">加载中…</p>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<ZapIcon width={26} height={26} />}
            title="还没有技能"
            description="创建或导入一个技能，例如「json-to-table」：把 JSON 数据转换为 Markdown 表格。"
            action={<Button onClick={() => setEditor({ mode: "create" })}>新建技能</Button>}
          />
        ) : (
          <div className="mx-auto grid max-w-5xl grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((skill) => (
              <div
                key={skill.name}
                className="group flex flex-col rounded-xl border border-line bg-card p-4 transition-colors hover:border-primary/30"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
                      <ZapIcon width={15} height={15} />
                    </span>
                    <h3 className="truncate font-mono text-[13px] font-semibold">{skill.name}</h3>
                    {skill.status === "broken" && (
                      <span
                        title={skill.load_error ?? "无法解析"}
                        className="flex shrink-0 items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[11px] text-red-500"
                      >
                        <AlertIcon width={10} height={10} />
                        无法加载
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      onClick={() => setEditor({ mode: "edit", skill })}
                      className="rounded-md p-1.5 text-ink-3 transition-colors hover:bg-black/[0.04] hover:text-ink"
                      title="编辑"
                    >
                      <EditIcon width={14} height={14} />
                    </button>
                    <button
                      onClick={() => setDeleting(skill)}
                      className="rounded-md p-1.5 text-ink-3 transition-colors hover:bg-red-50 hover:text-red-500"
                      title="删除"
                    >
                      <TrashIcon width={14} height={14} />
                    </button>
                  </div>
                </div>
                <p className="mt-2.5 line-clamp-2 text-[13px] leading-relaxed text-ink-2">
                  {skill.description}
                </p>
                {skill.load_error && (
                  <p className="mt-2 line-clamp-2 text-xs text-red-400">{skill.load_error}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <SkillEditorModal
        state={editor}
        onClose={() => setEditor(null)}
        onSaved={() => {
          setEditor(null);
          mutate();
          show("技能已保存");
        }}
      />

      <ConfirmDialog
        open={deleting !== null}
        title="删除技能"
        text={`确定删除技能「${deleting?.name}」吗？删除后模型将无法再使用它。`}
        loading={deleteLoading}
        onConfirm={handleDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
