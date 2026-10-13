// An open team sheet as a form fills it in: six Pokémon, each with what the sheet shows — species,
// ability, nature, item and four moves, and no Stat Points, because an open sheet does not show
// them. The API still takes Showdown text, so `toShowdown` is the one place that format lives.

export type SheetEntry = {
  species: string | null;
  ability: string | null;
  nature: string | null;
  item: string | null;
  moves: (string | null)[];
};

export const SHEET_SIZE = 6;

export function emptyEntry(): SheetEntry {
  return { species: null, ability: null, nature: null, item: null, moves: [null, null, null, null] };
}

export function emptySheet(): SheetEntry[] {
  return Array.from({ length: SHEET_SIZE }, emptyEntry);
}

/** What each nature raises and lowers, as the sheet shows it with its two arrows. */
export const NATURE_STATS: Record<string, [string, string] | null> = {
  Adamant: ["Atk", "SpA"], Bold: ["Def", "Atk"], Brave: ["Atk", "Spe"], Calm: ["SpD", "Atk"],
  Careful: ["SpD", "SpA"], Gentle: ["SpD", "Def"], Hasty: ["Spe", "Def"], Impish: ["Def", "SpA"],
  Jolly: ["Spe", "SpA"], Lax: ["Def", "SpD"], Lonely: ["Atk", "Def"], Mild: ["SpA", "Def"],
  Modest: ["SpA", "Atk"], Naive: ["Spe", "SpD"], Naughty: ["Atk", "SpD"], Quiet: ["SpA", "Spe"],
  Rash: ["SpA", "SpD"], Relaxed: ["Def", "Spe"], Sassy: ["SpD", "Spe"], Timid: ["Spe", "Atk"],
  Bashful: null, Docile: null, Hardy: null, Quirky: null, Serious: null,
};

export function natureHint(n: string): string {
  const s = NATURE_STATS[n];
  return s ? `+${s[0]} −${s[1]}` : "neutral";
}

/** What is missing from one Pokémon, for the card to say before the form is sent. */
export function missing(e: SheetEntry): string[] {
  if (!e.species) return [];
  const out: string[] = [];
  if (!e.ability) out.push("ability");
  if (!e.nature) out.push("nature");
  const moves = e.moves.filter(Boolean).length;
  if (moves < 4) out.push(`${4 - moves} move${moves === 3 ? "" : "s"}`);
  return out;
}

export function filled(sheet: SheetEntry[]): number {
  return sheet.filter((e) => e.species).length;
}

export function toShowdown(sheet: SheetEntry[]): string {
  return sheet.filter((e) => e.species).map((e) => [
    `${e.species}${e.item ? ` @ ${e.item}` : ""}`,
    ...(e.ability ? [`Ability: ${e.ability}`] : []),
    ...(e.nature ? [`${e.nature} Nature`] : []),
    ...e.moves.filter(Boolean).map((m) => `- ${m}`),
  ].join("\n")).join("\n\n") + "\n";
}

/** Showdown or PokéPaste text, read into the form. Lenient on purpose: it is an import, and the
 *  form shows the result to be checked, so anything it cannot place is left blank, not guessed. */
export function fromShowdown(text: string): SheetEntry[] {
  const blocks = text.replace(/\r/g, "").split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  const out = blocks.slice(0, SHEET_SIZE).map((block) => {
    const e = emptyEntry();
    const lines = block.split("\n").map((l) => l.trim());
    const [head, item] = lines[0].split(" @ ");
    // "Nickname (Species) (F)": the species is the parenthesised name that is not a gender.
    const parens = [...head.matchAll(/\(([^)]+)\)/g)].map((m) => m[1]).filter((x) => x !== "M" && x !== "F");
    e.species = (parens[0] ?? head.replace(/\((M|F)\)/g, "")).trim() || null;
    e.item = item?.trim() || null;
    const moves: string[] = [];
    for (const l of lines.slice(1)) {
      if (l.startsWith("Ability:")) e.ability = l.slice(8).trim() || null;
      else if (/^\w+ Nature$/.test(l)) e.nature = l.split(" ")[0];
      else if (l.startsWith("-")) moves.push(l.replace(/^-\s*/, "").trim());
    }
    e.moves = [0, 1, 2, 3].map((i) => moves[i] ?? null);
    return e;
  });
  while (out.length < SHEET_SIZE) out.push(emptyEntry());
  return out;
}
