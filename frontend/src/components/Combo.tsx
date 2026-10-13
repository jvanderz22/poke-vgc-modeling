import { useEffect, useMemo, useState } from "react";

export type ComboOption = { name: string; hint?: string };

/** One field of a sheet: type to search, ↑↓ and ↵ to pick, and ↵ moves on to the next field.
 *
 *  Options arrive in the order to show them, which for a sheet is how often people run each one,
 *  so the right answer is usually in the first few after two letters. The text a field holds is
 *  only committed as a value when it names an option; anything else is shown as not recognised
 *  rather than sent, because a misspelt move would reach the simulator as no move at all. */
export function Combo({
  value, options, onPick, placeholder, ariaLabel, disabled = false,
}: {
  value: string | null;
  options: ComboOption[];
  onPick: (name: string | null) => void;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  const [text, setText] = useState(value ?? "");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);

  useEffect(() => { setText(value ?? ""); }, [value]);

  const matches = useMemo(() => {
    const q = text.trim().toLowerCase();
    if (!q || q === (value ?? "").toLowerCase()) return options.slice(0, 30);
    const starts: ComboOption[] = [], contains: ComboOption[] = [];
    for (const o of options) {
      const n = o.name.toLowerCase();
      if (n.startsWith(q)) starts.push(o);
      else if (n.includes(q) || o.hint?.toLowerCase().includes(q)) contains.push(o);
    }
    return [...starts, ...contains].slice(0, 30);
  }, [options, text, value]);

  const exact = (t: string) => options.find((o) => o.name.toLowerCase() === t.trim().toLowerCase());
  const unknown = text.trim() !== "" && !exact(text);

  function choose(o: ComboOption | undefined) {
    onPick(o ? o.name : null);
    setText(o ? o.name : "");
    setOpen(false);
  }

  return (
    <div className="picker combo">
      <input
        type="text" value={text} placeholder={placeholder} aria-label={ariaLabel ?? placeholder}
        disabled={disabled} aria-invalid={unknown || undefined} className={unknown && !open ? "unknown" : undefined}
        onChange={(e) => { setText(e.target.value); setOpen(true); setCursor(0); }}
        onFocus={(e) => { setOpen(true); e.currentTarget.select(); }}
        onBlur={() => {
          setOpen(false);
          // Leaving a field commits what it says, when that is something: a typed name in full
          // counts, an empty box clears it, and a partial word is kept on screen as not recognised.
          if (!text.trim()) { if (value) onPick(null); }
          else if (exact(text) && exact(text)!.name !== value) choose(exact(text));
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setCursor((c) => Math.min(c + 1, matches.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
          else if (e.key === "Enter") {
            e.preventDefault();
            const pick = open ? matches[cursor] : exact(text);
            if (pick) {
              const input = e.currentTarget;
              choose(pick);
              // After the render: picking a species is what puts the rest of its card on screen.
              requestAnimationFrame(() => nextField(input)?.focus());
            }
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && matches.length > 0 && (
        <ul className="options" onMouseDown={(e) => e.preventDefault()}>
          {matches.map((o, i) => (
            <li key={o.name}>
              <button type="button" className={i === cursor ? "option active" : "option"}
                      onMouseEnter={() => setCursor(i)} onClick={() => choose(o)}>
                <span>{o.name}</span>
                {o.hint && <span className="tiny dim">{o.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The next empty field of the sheet after this one, so filling it from the keyboard is a name and
 *  ↵ per field, and one already filled (the only ability a species has) is stepped over. */
function nextField(input: HTMLInputElement): HTMLInputElement | null {
  const form = input.closest(".sheet-form");
  if (!form) return null;
  const all = [...form.querySelectorAll<HTMLInputElement>(".sheet-cards input:not([disabled])")];
  return all.slice(all.indexOf(input) + 1).find((x) => !x.value) ?? null;
}
