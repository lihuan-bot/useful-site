"use client";

import { useAuth } from "@/lib/auth";
import { NavRail } from "./nav-rail";

/** 登录后的应用外壳：左侧导航栏 + 右侧内容区。 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();

  return (
    <div className="flex h-full overflow-hidden">
      <NavRail username={user?.username ?? ""} />
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
