import { useEffect, useState } from "react";
import { api, type Species, type SpeciesOptions } from "../api";
import {
  emptyEntry, emptySheet, filled, fromShowdown, missing, NATURE_STATS, natureHint, SHEET_SIZE,
  type SheetEntry,
} from "../sheet";
import { Combo, type ComboOption } from "./Combo";

// Asked once per species and per page load: the lists do not change while the app runs.
const optionsCache = new Map<string, Promise<SpeciesOptions>>();
let itemsCache: Promise<string[]> | null = null;

function speciesOptions(name: string, reg: string): Promise<SpeciesOptions> {
  const key = `${reg}:${name}`;
  if (!optionsCache.has(key)) {
    const p = api.species(name, reg);
    p.catch(() => optionsCache.delete(key));
    optionsCache.set(key, p);
  }
  return optionsCache.get(key)!;
}

function allItems(reg: string): Promise<string[]> {
  if (!itemsCache) {
    itemsCache = api.pool(reg).then((p) => p.items);
    itemsCache.catch(() => { itemsCache = null; });
  }
  return itemsCache;
}

const pct = (share: number) => (share >= 0.005 ? `${Math.round(share * 100)}%` : undefined);

/** A sheet as it is kept between visits: the form you were half way through survives a reload. */
export function useSheetDraft(key: string): [SheetEntry[], (s: SheetEntry[]) => void] {
  const [sheet, setSheet] = useState<SheetEntry[]>(() => {
    try {
      const raw = localStorage.getItem(key);
      const parsed = raw ? JSON.parse(raw) : null;
      return Array.isArray(parsed) && parsed.length === SHEET_SIZE ? parsed : emptySheet();
    } catch { return emptySheet(); }
  });
  const set = (s: SheetEntry[]) => {
    setSheet(s);
    try { localStorage.setItem(key, JSON.stringify(s)); } catch { /* private mode: kept in memory */ }
  };
  return [sheet, set];
}

/** An open team sheet, entered the way it is shown: six cards, each with species, ability, nature,
 *  item and four moves. No Stat Points, because the sheet does not show them.
 *
 *  Every field searches as you type and offers what is legal for that Pokémon, ordered by how
 *  often people run it, so most fields are two letters and ↵. Nothing is filled in for you beyond
 *  an ability that is the only one possible: a guessed move that you forget to correct would be
 *  read as a fact about their team. Pasting text is still there, as an import into the form. */
export function SheetForm({ reg, pool, value, onChange, label }: {
  reg: string;
  pool: Species[];
  value: SheetEntry[];
  onChange: (s: SheetEntry[]) => void;
  label: string;
}) {
  const [paste, setPaste] = useState<string | null>(null);
  const abilitiesOf = new Map(pool.map((s) => [s.name, [...new Set(s.abilities)]]));
  const speciesList: ComboOption[] = pool.map((s) => ({
    name: s.name, hint: `${s.types.join("/")}${s.seen ? ` · ${s.seen.toLocaleString()}` : ""}`,
  }));
  const taken = new Set(value.map((e) => e.species).filter(Boolean));
  const set = (i: number, e: SheetEntry) => onChange(value.map((x, j) => (j === i ? e : x)));
  const n = filled(value);
  const gaps = value.reduce((k, e) => k + missing(e).length, 0);

  return (
    <div className="sheet-form">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <label style={{ margin: 0 }}>{label}</label>
        <span className="row">
          <span className="tiny dim">{n}/{SHEET_SIZE}{n === SHEET_SIZE && gaps > 0 ? `, ${gaps} field${gaps === 1 ? "" : "s"} empty` : ""}</span>
          <button type="button" className="link tiny" onClick={() => setPaste(paste === null ? "" : null)}>
            {paste === null ? "import text" : "close import"}
          </button>
          {n > 0 && <button type="button" className="link tiny" onClick={() => onChange(emptySheet())}>clear</button>}
        </span>
      </div>

      {paste !== null && (
        <div className="sheet-import">
          <textarea value={paste} rows={6} placeholder="Showdown or PokéPaste text, to fill the form from"
                    onChange={(e) => setPaste(e.target.value)} />
          <button type="button" disabled={!paste.trim()}
                  onClick={() => { onChange(fromShowdown(paste)); setPaste(null); }}>
            Fill the form
          </button>
        </div>
      )}

      <div className="sheet-cards">
        {value.map((e, i) => (
          <SheetCard key={i} reg={reg} index={i} entry={e} onChange={(x) => set(i, x)} abilitiesOf={abilitiesOf}
                     species={e.species ? speciesList : speciesList.filter((s) => !taken.has(s.name))} />
        ))}
      </div>
    </div>
  );
}

function SheetCard({ reg, index, entry, species, abilitiesOf, onChange }: {
  reg: string;
  index: number;
  entry: SheetEntry;
  species: ComboOption[];
  abilitiesOf: Map<string, string[]>;
  onChange: (e: SheetEntry) => void;
}) {
  const [opts, setOpts] = useState<SpeciesOptions | null>(null);
  const [items, setItems] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOpts(null);
    setError(null);
    if (!entry.species) return;
    let live = true;
    speciesOptions(entry.species, reg).then((o) => live && setOpts(o))
      .catch((e) => live && setError(String(e.message ?? e)));
    allItems(reg).then((x) => live && setItems(x)).catch(() => {});
    return () => { live = false; };
  }, [entry.species, reg]);

  const abilities: ComboOption[] = (opts?.abilities ?? []).map((a) => ({ name: a.name, hint: pct(a.share) }));
  const run = new Set((opts?.natures ?? []).map((x) => x.name));
  const natures: ComboOption[] = [
    ...(opts?.natures ?? []).map((x) => ({ name: x.name, hint: `${natureHint(x.name)} · ${pct(x.share) ?? "<1%"}` })),
    ...Object.keys(NATURE_STATS).sort().filter((x) => !run.has(x)).map((x) => ({ name: x, hint: natureHint(x) })),
  ];
  const usual = new Set((opts?.items ?? []).map((x) => x.name));
  const itemList: ComboOption[] = [
    ...(opts?.items ?? []).map((x) => ({ name: x.name, hint: pct(x.share) ?? "<1%" })),
    ...items.filter((x) => !usual.has(x)).map((x) => ({ name: x })),
  ];
  const moves = (slot: number): ComboOption[] => {
    const other = new Set(entry.moves.filter((m, j) => m && j !== slot));
    return (opts?.moves ?? []).filter((m) => !other.has(m.name)).map((m) => ({ name: m.name, hint: pct(m.share) }));
  };
  const gaps = missing(entry);

  return (
    <div className={`sheet-card${entry.species ? "" : " empty"}`}>
      <div className="sheet-head">
        <Combo value={entry.species} options={species} placeholder={`Pokémon ${index + 1}`}
               onPick={(name) => {
                 if (name === entry.species) return;
                 // A different Pokémon: nothing the last one had carries over, and the one ability
                 // it can have is not a guess, so it is filled in at once.
                 const only = name ? abilitiesOf.get(name) ?? [] : [];
                 onChange(name ? { ...emptyEntry(), species: name, ability: only.length === 1 ? only[0] : null } : emptyEntry());
               }} />
        {opts && <span className="tiny dim sheet-types">{opts.types.join(" / ")}</span>}
      </div>
      {entry.species && (
        <>
          <div className="sheet-row">
            <Combo value={entry.ability} options={abilities} placeholder="Ability"
                   onPick={(a) => onChange({ ...entry, ability: a })} />
            <Combo value={entry.nature} options={natures} placeholder="Nature"
                   onPick={(x) => onChange({ ...entry, nature: x })} />
            <Combo value={entry.item} options={itemList} placeholder="Item"
                   onPick={(x) => onChange({ ...entry, item: x })} />
          </div>
          {entry.nature && NATURE_STATS[entry.nature] && (
            <div className="tiny nature-arrows">
              <span className="up">▲ {NATURE_STATS[entry.nature]![0]}</span>{" "}
              <span className="down">▼ {NATURE_STATS[entry.nature]![1]}</span>
            </div>
          )}
          <div className="sheet-moves">
            {[0, 1, 2, 3].map((slot) => (
              <Combo key={slot} value={entry.moves[slot]} options={moves(slot)} placeholder={`Move ${slot + 1}`}
                     onPick={(m) => onChange({ ...entry, moves: entry.moves.map((x, j) => (j === slot ? m : x)) })} />
            ))}
          </div>
          {error && <div className="tiny err">{error}</div>}
          {!error && opts && gaps.length > 0 && <div className="tiny dim">Still to fill: {gaps.join(", ")}.</div>}
        </>
      )}
    </div>
  );
}
