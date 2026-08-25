"use client";

/**
 * HITL 补充信息表单。
 *
 * 后端 field_collect 中间件在业务工具缺字段时暂停图执行，通过 SSE
 * `interrupt` 事件下发通用表单描述（missing / invalid / known），
 * 前端原样渲染 label(prompt) 和 placeholder，提交 POST /resume。
 */

import { useMemo, useState } from "react";
import type { InterruptPayload } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { CheckIcon } from "@/components/ui/icons";

const TOOL_LABELS: Record<string, string> = {
  submit_order: "提交订单",
};

interface InterruptFormProps {
  payload: InterruptPayload;
  submitting: boolean;
  onSubmit: (answers: Record<string, string>) => void;
  onDismiss: () => void;
}

export function InterruptForm({ payload, submitting, onSubmit, onDismiss }: InterruptFormProps) {
  const [values, setValues] = useState<Record<string, string>>({});

  const fields = useMemo(() => [...payload.missing, ...payload.invalid], [payload]);
  const knownEntries = Object.entries(payload.known ?? {});

  const allFilled = payload.missing.every((f) => (values[f.name] ?? "").trim().length > 0);

  const submit = () => {
    if (!allFilled || submitting) return;
    const answers: Record<string, string> = {};
    for (const f of fields) {
      const v = (values[f.name] ?? "").trim();
      if (v) answers[f.name] = v;
    }
    onSubmit(answers);
  };

  if (fields.length === 0) return null;

  return (
    <div className="animate-fade-up w-full max-w-xl rounded-xl border border-primary/25 bg-primary-softer p-4">
      <p className="text-sm font-semibold text-ink">
        {TOOL_LABELS[payload.tool] ?? payload.tool} 需要补充以下信息
      </p>

      {knownEntries.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {knownEntries.map(([name, value]) => (
            <span
              key={name}
              className="inline-flex items-center gap-1 rounded-full bg-card px-2.5 py-1 text-xs text-ink-2"
            >
              <CheckIcon width={11} height={11} className="text-green-500" />
              {name}: {value}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-col gap-3">
        {fields.map((f) => (
          <label key={f.name} className="flex flex-col gap-1">
            <span className="text-[13px] text-ink-2">{f.prompt}</span>
            <input
              value={values[f.name] ?? ""}
              onChange={(e) => setValues((prev) => ({ ...prev, [f.name]: e.target.value }))}
              placeholder={f.placeholder ?? f.hint ?? ""}
              className="h-9 rounded-lg border border-line bg-card px-3 text-sm outline-none transition-colors placeholder:text-ink-3 focus:border-primary"
            />
          </label>
        ))}
      </div>

      <div className="mt-4 flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onDismiss} disabled={submitting}>
          暂不填写
        </Button>
        <Button size="sm" onClick={submit} disabled={!allFilled || submitting} loading={submitting}>
          提交
        </Button>
      </div>
      <p className="mt-2 text-right text-[11px] text-ink-3">提交后将自动继续生成</p>
    </div>
  );
}
