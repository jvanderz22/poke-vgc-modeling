import { useEffect, useRef, useState } from "react";
import { api, type SheetRead } from "../api";
import { emptyEntry, SHEET_SIZE, type SheetEntry } from "../sheet";

/** The form's six cards from what OCR read. A field it could not read is left empty, as the
 *  form leaves anything you have not entered. */
export function entriesFromRead(read: SheetRead): SheetEntry[] {
  const out: SheetEntry[] = read.entries.slice(0, SHEET_SIZE).map((e) => ({
    species: e.species, ability: e.ability, nature: e.nature, item: e.item,
    moves: [0, 1, 2, 3].map((i) => e.moves[i] ?? null),
  }));
  while (out.length < SHEET_SIZE) out.push(emptyEntry());
  return out;
}

/** Fill a sheet from a screenshot of it: a broadcast's team list, or the game's own sheet.
 *
 *  Paste (⌘V) while this is open, drop an image on it, or choose a file. What is read replaces
 *  the form, the way an import does, and everything it could not read is listed under it: the
 *  server never fills a field with its nearest guess, so an empty field is one to look at. */
export function SheetFromImage({ reg, onFill }: { reg: string; onFill: (s: SheetEntry[]) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [read, setRead] = useState<SheetRead | null>(null);
  const [over, setOver] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  const take = async (image: Blob | null | undefined) => {
    if (!image || busy) return;
    if (!image.type.startsWith("image/")) { setError("That isn't an image."); return; }
    setBusy(true);
    setError(null);
    setRead(null);
    try {
      const r = await api.readSheet(image, reg);
      if (!r.entries.length) {
        setError(r.notes[0] ?? "No team sheet found in that image.");
      } else {
        onFill(entriesFromRead(r));
        setRead(r);
      }
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  // A screenshot on the clipboard is pasted anywhere on the page while this is open.
  useEffect(() => {
    const onPaste = (ev: ClipboardEvent) => {
      const item = [...(ev.clipboardData?.items ?? [])].find((i) => i.type.startsWith("image/"));
      if (!item) return;
      ev.preventDefault();
      take(item.getAsFile());
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  });

  const blanks = read ? read.entries.reduce((k, e) =>
    k + [e.ability, e.nature, e.item].filter((x) => !x).length + Math.max(0, 4 - e.moves.length), 0) : 0;

  return (
    <div className="sheet-import">
      <div className={`sheet-drop${over ? " over" : ""}`}
           onDragOver={(e) => { e.preventDefault(); setOver(true); }}
           onDragLeave={() => setOver(false)}
           onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files[0]); }}>
        {busy ? "Reading the sheet…" : (
          <>
            Paste a screenshot of the sheet (⌘V), drop one here, or{" "}
            <button type="button" className="link" onClick={() => file.current?.click()}>choose a file</button>.
          </>
        )}
        <input ref={file} type="file" accept="image/*" hidden
               onChange={(e) => { take(e.target.files?.[0]); e.target.value = ""; }} />
      </div>
      {error && <div className="tiny err">{error}</div>}
      {read && (
        <div className="tiny">
          <div>
            Read {read.entries.length} Pokémon{read.title ? ` from ${read.title}'s sheet` : ""}.{" "}
            {blanks === 0 ? "Every field read." : `${blanks} field${blanks === 1 ? "" : "s"} left empty: fill ${blanks === 1 ? "it" : "them"} in below.`}
          </div>
          {read.unread.length > 0 && (
            <div className="dim">Couldn't read: {read.unread.map((u) => `“${u.text}”`).join(", ")}</div>
          )}
          {read.problems.filter((p) => p.severity === "error").map((p, i) => (
            <div key={i} className="err">{p.pokemon ? `${p.pokemon}: ` : ""}{p.message}</div>
          ))}
        </div>
      )}
    </div>
  );
}
