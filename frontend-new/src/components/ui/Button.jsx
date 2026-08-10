import cn from "../../lib/cn";

const variants = {
  primary: "border border-transparent bg-ink text-white hover:bg-ink-2",
  secondary: "border border-line bg-surface text-ink-2 hover:border-line-strong hover:bg-surface-2 hover:text-ink",
  ghost: "border border-transparent text-muted hover:bg-surface-3 hover:text-ink",
  danger: "border border-transparent bg-red text-white hover:brightness-95",
};

const sizes = {
  sm: "h-8 px-2.5 text-xs",
  md: "h-9 px-3.5 text-xs",
};

export function Button({ variant = "secondary", size = "md", className = "", children, ...props }) {
  return (
    <button
      type="button"
      data-variant={variant}
      data-size={size}
      className={cn(
        "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-control font-semibold leading-none transition-[color,background-color,border-color,box-shadow] duration-200 disabled:cursor-not-allowed disabled:opacity-55",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
