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

/** A ⋯ button that opens a short list of actions under it: the ones a page keeps out of its way.
 *  Closed as the `Info` panel is (a second click, a click outside it, or Esc), and by picking one. */
export function Menu({ label, children, align = "right" }: {
  label: string; align?: "left" | "right";
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); setOpen(false); } };
    document.addEventListener("mousedown", away);
    window.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("mousedown", away); window.removeEventListener("keydown", esc, true); };
  }, [open]);
  return (
    <span className="info" ref={box}>
      <button className="tool-btn" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open}
              aria-controls={id} onClick={() => setOpen(!open)}>
        <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.4" /><circle cx="8" cy="8" r="1.4" /><circle cx="12.5" cy="8" r="1.4" /></svg>
      </button>
      {open && <span className={`menu-pop ${align}`} id={id} role="menu" aria-label={label}>{children(() => setOpen(false))}</span>}
    </span>
  );
}
