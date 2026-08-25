"use client";

import { cn } from "@/lib/utils";
import { LoaderIcon } from "./icons";

type ButtonVariant = "primary" | "outline" | "ghost" | "danger";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  loading?: boolean;
}

const variantClass: Record<ButtonVariant, string> = {
  primary: "bg-primary text-white hover:bg-primary-hover",
  outline: "border border-line bg-card text-ink hover:bg-page",
  ghost: "text-ink-2 hover:bg-black/[0.04]",
  danger: "text-red-500 hover:bg-red-50",
};

const sizeClass = {
  sm: "h-7 px-2.5 text-xs gap-1",
  md: "h-9 px-4 text-sm gap-1.5",
  lg: "h-10 px-5 text-sm gap-1.5",
} as const;

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={cn(
        "inline-flex select-none items-center justify-center rounded-lg font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variantClass[variant],
        sizeClass[size],
        className,
      )}
      {...rest}
    >
      {loading && <LoaderIcon className="animate-spin" width={14} height={14} />}
      {children}
    </button>
  );
}
