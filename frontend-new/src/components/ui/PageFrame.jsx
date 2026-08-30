import Icon from "./Icon";
import cn from "../../lib/cn";

export function PageFrame({ children, className = "" }) {
  return <div data-ui="page-frame" className={cn("ui-page-frame motion-soft-rise mx-auto grid w-full max-w-[1360px] gap-5 px-4 pb-12 pt-5 sm:px-6 sm:pt-6 xl:px-8 xl:pt-7", className)}>{children}</div>;
}

export function SectionHeader({ eyebrow, title, action, onAction }) {
  return (
    <div data-ui="section-header" className="ui-section-header flex items-center justify-between gap-5 px-5 pb-3.5 pt-4.5">
      <div className="min-w-0">
        <span className="text-[11px] font-semibold tracking-[0.08em] text-faint">{eyebrow}</span>
        <h3 className="mt-1 truncate text-base font-semibold tracking-tight text-ink">{title}</h3>
      </div>
      {action && (
        <button
          type="button"
          className="flex shrink-0 cursor-pointer items-center gap-1 text-xs font-medium text-primary transition-opacity hover:opacity-75"
          onClick={onAction}
        >
          {action}
          <Icon name="arrow" size={13} />
        </button>
      )}
    </div>
  );
}
