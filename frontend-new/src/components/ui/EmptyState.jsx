import cn from "../../lib/cn";

export function EmptyState({ title, description, action, className = "" }) {
  return (
    <div className={cn("grid justify-items-center gap-1.5 rounded-card border border-dashed border-line-strong bg-surface-2 px-5 py-8 text-center", className)}>
      <p className="m-0 text-body text-muted">{title}</p>
      {description && <p className="m-0 text-xs text-faint">{description}</p>}
      {action}
    </div>
  );
}
