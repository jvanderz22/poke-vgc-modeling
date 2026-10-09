import { useMemo, useState } from "react";
import type { Species } from "../api";

/** Search-as-you-type over the legal pool.
 *
 *  The list arrives sorted by how many real sheets each species appeared in, so an empty box
 *  shows what people actually play rather than whatever is alphabetically first. */
export function SpeciesPicker({
  pool, value, onPick, placeholder = "Search…",
}: {
  pool: Species[];
  value: string | null;
  onPick: (name: string | null) => void;
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return pool.slice(0, 40);
    const starts: Species[] = [], contains: Species[] = [];
    for (const s of pool) {
      const n = s.name.toLowerCase();
      if (n.startsWith(q)) starts.push(s);
      else if (n.includes(q)) contains.push(s);
      if (starts.length >= 40) break;
    }
    return [...starts, ...contains].slice(0, 40);
  }, [pool, query]);

  function choose(s: Species) {
    onPick(s.name);
    setQuery("");
    setOpen(false);
  }

  if (value) {
    return (
      <div className="picked">
        <span className="picked-name">{value}</span>
        <button className="link tiny" onClick={() => onPick(null)} aria-label={`Remove ${value}`}>×</button>
      </div>
    );
  }

  return (
    <div className="picker">
      <input
        type="text"
        value={query}
        placeholder={placeholder}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setCursor(0); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, matches.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
          else if (e.key === "Enter" && matches[cursor]) {
            e.preventDefault();
            const next = nextEmpty(e.currentTarget);
            choose(matches[cursor]);
            // This box goes once it has a value; the keyboard moves on to the next empty one.
            if (next) requestAnimationFrame(() => next.focus());
          }
          else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && matches.length > 0 && (
        // Pressing on an option must not take focus from the box: the blur would close the list
        // before the click lands, which a slow click always loses.
        <ul className="options" onMouseDown={(e) => e.preventDefault()}>
          {matches.map((s, i) => (
            <li key={s.id}>
              <button className={i === cursor ? "option active" : "option"}
                      onMouseEnter={() => setCursor(i)} onClick={() => choose(s)}>
                <span>{s.name}</span>
                <span className="tiny dim">
                  {s.types.join("/")}{s.seen > 0 && ` · ${s.seen.toLocaleString()}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The next empty picker after this one among its siblings, wrapping round: filling six slots
 *  from the keyboard is six names and six ↵. */
function nextEmpty(input: HTMLInputElement): HTMLInputElement | null {
  const group = input.closest(".slots");
  if (!group) return null;
  const all = [...group.querySelectorAll<HTMLInputElement>(".picker input")];
  const at = all.indexOf(input);
  return [...all.slice(at + 1), ...all.slice(0, at)][0] ?? null;
}
