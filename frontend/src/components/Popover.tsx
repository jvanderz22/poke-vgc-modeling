import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/** An ⓘ that opens a small panel attached to it: how a number was made, kept out of the way of
 *  the number itself. Non-modal, and closed by a second click, a click outside it, or Esc. */
export function Info({ label, children, align = "right" }: { label: string; children: ReactNode; align?: "left" | "right" }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    // Capture, and stop it there: Esc on an open popover only closes it, and never also reaches
    // the stepper, where Esc opens the entry bar.
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); setOpen(false); } };
    document.addEventListener("mousedown", away);
    window.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("mousedown", away); window.removeEventListener("keydown", esc, true); };
  }, [open]);
  return (
    <span className="info" ref={box}>
      <button className="icon-btn info-btn" aria-label={label} title={label} aria-expanded={open}
              aria-controls={id} onClick={() => setOpen(!open)}>ⓘ</button>
      {open && <span className={`info-pop ${align}`} id={id} role="dialog" aria-label={label}>{children}</span>}
    </span>
  );
}
