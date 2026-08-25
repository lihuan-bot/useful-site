/** 管理页统一的页头：标题 + 描述 + 右侧操作区。 */

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-line-soft bg-card px-6">
      <div>
        <h1 className="text-[15px] font-semibold">{title}</h1>
        {description && <p className="mt-0.5 text-xs text-ink-3">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}
