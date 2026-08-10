import Icon from "./Icon";
import cn from "../../lib/cn";
import { Button } from "./Button";

export function ErrorNotice({ title = "数据加载失败", detail, onRetry, className = "" }) {
  return (
    <div className={cn("flex items-center gap-3 rounded-card border border-red/25 bg-red-soft px-4 py-3", className)} role="alert">
      <span className="shrink-0 text-red"><Icon name="alert" size={16} /></span>
      <div className="min-w-0 flex-1">
        <strong className="block text-body font-semibold text-red">{title}</strong>
        {detail && <span className="mt-0.5 block text-xs text-red/80">{detail}</span>}
      </div>
      {onRetry && <Button size="sm" onClick={onRetry}>重试</Button>}
    </div>
  );
}
