"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  BookIcon,
  ChatIcon,
  FolderIcon,
  LogoutIcon,
  SparklesIcon,
  ZapIcon,
} from "@/components/ui/icons";

const NAV_ITEMS = [
  { href: "/chat", label: "对话", icon: ChatIcon },
  { href: "/skills", label: "技能", icon: ZapIcon },
  { href: "/documents", label: "知识库", icon: BookIcon },
  { href: "/files", label: "文件", icon: FolderIcon },
] as const;

/** 左侧导航栏：豆包风格的竖向功能入口 + 底部用户区。 */
export function NavRail({ username }: { username: string }) {
  const pathname = usePathname();
  const { logout } = useAuth();

  return (
    <nav className="flex w-[68px] shrink-0 flex-col items-center border-r border-line bg-card py-4">
      <Link
        href="/chat"
        className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-white shadow-sm transition-transform hover:scale-105"
        aria-label="首页"
      >
        <SparklesIcon width={20} height={20} />
      </Link>

      <div className="mt-6 flex flex-1 flex-col items-center gap-1">
        {NAV_ITEMS.map(({ href, label, icon: ItemIcon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex w-[52px] flex-col items-center gap-0.5 rounded-lg py-2 text-[11px] transition-colors",
                active
                  ? "bg-primary-soft text-primary"
                  : "text-ink-3 hover:bg-black/[0.04] hover:text-ink-2",
              )}
            >
              <ItemIcon width={18} height={18} />
              <span>{label}</span>
            </Link>
          );
        })}
      </div>

      <div className="flex flex-col items-center gap-3">
        <div
          className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-soft text-sm font-semibold text-primary"
          title={username}
        >
          {username ? username[0].toUpperCase() : "?"}
        </div>
        <button
          onClick={logout}
          className="rounded-lg p-1.5 text-ink-3 transition-colors hover:bg-black/[0.04] hover:text-ink"
          title="退出登录"
        >
          <LogoutIcon width={16} height={16} />
        </button>
      </div>
    </nav>
  );
}
