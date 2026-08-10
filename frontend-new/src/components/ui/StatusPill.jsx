import cn from "../../lib/cn";

const tones = {
  green: "border-green/25 bg-green/8 text-green",
  teal: "border-teal/30 bg-teal-soft text-teal",
  blue: "border-primary/25 bg-primary-soft text-primary",
  amber: "border-amber/25 bg-amber-soft text-amber",
  red: "border-red/25 bg-red-soft text-red",
  violet: "border-violet/25 bg-violet/8 text-violet",
  gray: "border-line bg-surface-3 text-muted",
};

export function StatusPill({ tone = "blue", children }) {
  return (
    <span className={cn("inline-flex w-max items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-1 text-[11px] leading-none", tones[tone] ?? tones.blue)}>
      <i className="h-[5px] w-[5px] rounded-full bg-current" />
      {children}
    </span>
  );
}
