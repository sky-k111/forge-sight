import cn from "../../lib/cn";

export function Card({ className = "", children, ...props }) {
  return (
    <div data-ui="card" className={cn("ui-card rounded-card border border-line bg-surface shadow-card transition-[border-color,box-shadow] duration-200", className)} {...props}>
      {children}
    </div>
  );
}
