"use client";

/** 登录路由守卫：未登录跳转 /login，校验中显示加载态。 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { useAuth } from "@/lib/auth";
import { LoaderIcon } from "@/components/ui/icons";

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex h-full items-center justify-center text-ink-3">
        <LoaderIcon className="animate-spin" width={22} height={22} />
      </div>
    );
  }

  return <AppShell>{children}</AppShell>;
}
