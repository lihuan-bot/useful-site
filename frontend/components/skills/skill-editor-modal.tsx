"use client";

/** 技能新建 / 编辑弹窗。名称仅创建时可填（后端以 name 为键，不支持改名）。 */

import { useState } from "react";
import { apiCreateSkill, apiUpdateSkill } from "@/lib/api/skills";
import { ApiError } from "@/lib/api/client";
import type { Skill } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

export type SkillEditorState = { mode: "create" } | { mode: "edit"; skill: Skill };

interface SkillEditorModalProps {
  state: SkillEditorState | null;
  onClose: () => void;
  onSaved: () => void;
}

const NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/**
 * 弹窗由父组件用 key 控制挂载（create / 每个技能的 edit 各一个 key），
 * 编辑内容切换时整体重挂载，表单初始值从 props 读取一次即可。
 */
export function SkillEditorModal({ state, onClose, onSaved }: SkillEditorModalProps) {
  if (!state) return null;
  return (
    <SkillEditorForm
      key={state.mode === "edit" ? `edit:${state.skill.name}` : "create"}
      state={state}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function SkillEditorForm({
  state,
  onClose,
  onSaved,
}: {
  state: SkillEditorState;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isCreate = state.mode === "create";
  const [name, setName] = useState(isCreate ? "" : state.skill.name);
  const [description, setDescription] = useState(isCreate ? "" : state.skill.description);
  const [instructions, setInstructions] = useState(isCreate ? "" : state.skill.instructions);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const trimmedName = name.trim();
    const trimmedDesc = description.trim();
    const trimmedInstr = instructions.trim();

    if (isCreate && !NAME_RE.test(trimmedName)) {
      setError("名称仅支持小写字母、数字和单连字符（如 json-to-table）");
      return;
    }
    if (!trimmedDesc) {
      setError("请填写描述（何时使用此技能）");
      return;
    }
    if (!trimmedInstr) {
      setError("请填写技能指令");
      return;
    }

    setSaving(true);
    setError("");
    try {
      if (isCreate) {
        await apiCreateSkill({ name: trimmedName, description: trimmedDesc, instructions: trimmedInstr });
      } else {
        await apiUpdateSkill(state.skill.name, { description: trimmedDesc, instructions: trimmedInstr });
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title={isCreate ? "新建技能" : `编辑技能「${state.skill.name}」`}
      onClose={onClose}
      widthClass="max-w-2xl"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button onClick={save} loading={saving}>
            保存
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] text-ink-2">名称</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!isCreate}
            placeholder="json-to-table"
            className="h-10 rounded-lg border border-line bg-card px-3 text-sm outline-none transition-colors placeholder:text-ink-3 focus:border-primary disabled:bg-page disabled:text-ink-3"
          />
          {isCreate && <span className="text-[11px] text-ink-3">小写字母、数字、单连字符，创建后不可修改</span>}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] text-ink-2">描述</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="何时使用此技能，例如：把 JSON 数据转换为 Markdown 表格"
            className="h-10 rounded-lg border border-line bg-card px-3 text-sm outline-none transition-colors placeholder:text-ink-3 focus:border-primary"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] text-ink-2">指令（Markdown）</span>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder={"完整的技能指令，会按需注入给模型……"}
            rows={12}
            className="resize-y rounded-lg border border-line bg-card px-3 py-2.5 font-mono text-[13px] leading-relaxed outline-none transition-colors placeholder:font-sans placeholder:text-ink-3 focus:border-primary"
          />
        </label>

        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-500">{error}</p>}
      </div>
    </Modal>
  );
}
