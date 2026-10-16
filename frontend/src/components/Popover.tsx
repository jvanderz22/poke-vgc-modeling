import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

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

/** A card that shows more of itself while the pointer is on it or it has focus: a read-only panel
 *  attached to it, so nothing in it needs the pointer. It opens a beat after the pointer arrives,
 *  so a pass along a row of cards opens nothing, and under the card unless only above has room.
 *  Leaving, a click elsewhere or Esc closes it; a tap opens it where there is no hover. */
export function HoverCard({ label, className, pop, align = "left", children }: {
  label: string; className?: string; pop: ReactNode; align?: "left" | "right"; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = (delay: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(true), delay);
  };
  const hide = () => { window.clearTimeout(timer.current); setOpen(false); setUp(false); };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useLayoutEffect(() => {
    if (!open || !box.current || !panel.current) return;
    const b = box.current.getBoundingClientRect(), p = panel.current.getBoundingClientRect();
    setUp(p.bottom > window.innerHeight && b.top > window.innerHeight - b.bottom);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    // As the `Info` panel: Esc closes it and goes no further, where the stepper would take it.
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); hide(); } };
    window.addEventListener("keydown", esc, true);
    return () => window.removeEventListener("keydown", esc, true);
  }, [open]);
  return (
    <div ref={box} className={`hover-card${className ? ` ${className}` : ""}`} tabIndex={0} aria-label={label}
         aria-describedby={open ? id : undefined}
         onMouseEnter={() => show(250)} onMouseLeave={hide} onFocus={() => show(0)} onBlur={hide}>
      {children}
      {open && <div ref={panel} id={id} role="tooltip" className={`hover-pop ${align}${up ? " up" : ""}`}>{pop}</div>}
    </div>
  );
}
