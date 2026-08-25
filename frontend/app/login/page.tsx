"use client";

/** 登录 / 注册页 —— 豆包风格：渐变底 + 居中白色卡片。 */

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { SparklesIcon } from "@/components/ui/icons";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

type Mode = "login" | "register";

export default function LoginPage() {
  const { user, login, register } = useAuth();
  const router = useRouter();

  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 已登录直接进入
  useEffect(() => {
    if (user) router.replace("/chat");
  }, [user, router]);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError("");
    setConfirm("");
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");

    const name = username.trim();
    if (!name || !password) {
      setError("请输入用户名和密码");
      return;
    }
    if (mode === "register" && password.length < 6) {
      setError("密码至少 6 位");
      return;
    }
    if (mode === "register" && password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }

    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(name, password);
      } else {
        await register(name, password);
      }
      router.replace("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "操作失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden bg-page">
      {/* 豆包风格的柔和渐变光斑 */}
      <div className="pointer-events-none absolute -top-32 left-1/2 h-96 w-[640px] -translate-x-1/2 rounded-full bg-primary/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-40 right-[12%] h-80 w-80 rounded-full bg-indigo-200/40 blur-3xl" />
      <div className="pointer-events-none absolute -left-20 bottom-[20%] h-72 w-72 rounded-full bg-sky-200/40 blur-3xl" />

      <div className="animate-fade-up relative z-10 w-[min(400px,calc(100vw-32px))] rounded-2xl border border-line bg-card p-8 shadow-xl shadow-black/[0.04]">
        <div className="mb-7 flex flex-col items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-white shadow-md shadow-primary/30">
            <SparklesIcon width={24} height={24} />
          </div>
          <h1 className="text-xl font-semibold">智能助手</h1>
          <p className="text-[13px] text-ink-3">
            {mode === "login" ? "登录后开始对话" : "创建账号，体验 AI 工作台"}
          </p>
        </div>

        <div className="mb-6 grid grid-cols-2 rounded-lg bg-page p-1 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              onClick={() => switchMode(m)}
              className={cn(
                "rounded-md py-1.5 font-medium transition-colors",
                mode === m ? "bg-card text-ink shadow-sm" : "text-ink-3 hover:text-ink-2",
              )}
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] text-ink-2">用户名</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="3-32 位字母、数字、下划线或连字符"
              autoComplete="username"
              className="h-10 rounded-lg border border-line bg-card px-3 text-sm outline-none transition-colors placeholder:text-ink-3 focus:border-primary"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] text-ink-2">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "register" ? "至少 6 位" : "请输入密码"}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              className="h-10 rounded-lg border border-line bg-card px-3 text-sm outline-none transition-colors placeholder:text-ink-3 focus:border-primary"
            />
          </div>
          {mode === "register" && (
            <div className="flex flex-col gap-1.5">
              <label className="text-[13px] text-ink-2">确认密码</label>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="再次输入密码"
                autoComplete="new-password"
                className="h-10 rounded-lg border border-line bg-card px-3 text-sm outline-none transition-colors placeholder:text-ink-3 focus:border-primary"
              />
            </div>
          )}

          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-500">{error}</p>
          )}

          <Button type="submit" size="lg" loading={submitting} className="mt-1 w-full">
            {mode === "login" ? "登录" : "注册并登录"}
          </Button>
        </form>
      </div>
    </div>
  );
}
