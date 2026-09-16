import { useEffect, useRef } from "react";
import cn from "../../lib/cn";

export function Modal({ open, onClose, children, maxWidth = 560, label, dismissOnBackdrop = true }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return undefined;
    const previousFocus = document.activeElement;
    const focusable = () => [...(dialogRef.current?.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]') ?? [])].filter((element) => !element.closest('[hidden]'));
    (focusable()[0] ?? dialogRef.current)?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); }
      if (event.key === "Tab") {
        const nodes = focusable();
        const first = nodes[0];
        const last = nodes.at(-1);
        if (!first) { event.preventDefault(); dialogRef.current?.focus(); }
        else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); if (previousFocus?.isConnected) previousFocus.focus(); };
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-6 backdrop-blur-sm"
      role="presentation"
      onClick={dismissOnBackdrop ? onClose : undefined}
    >
      <div
        role="dialog"
        ref={dialogRef}
        tabIndex={-1}
        aria-modal="true"
        aria-label={label}
        className={cn("modal-in max-h-[85vh] w-full overflow-auto rounded-overlay border border-line bg-surface shadow-card-hover")}
        style={{ maxWidth }}
        onClick={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
