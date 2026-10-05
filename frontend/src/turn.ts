// Where a turn has got to, read off the journal. The Turn Stepper asks for the next sentence of
// the turn, so it has to know which sentences have been said: who has acted since the last
// `turn` entry, what is left at the end of the turn, which slots wait for a replacement. All of it
// is worked out here from the view, and none of it is stored, so a reload lands on the same
// question. (When the backend reports `turn_progress`, this file is what it replaces.)

import type { Entry, LiveMon, LiveView, MoveOption } from "./api";
import { across, screenOrder, type Pos, type Side } from "./screen";

export const BRING = 4;

export function toId(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, "");
}

export const posKey = (p: Pos) => `${p.side}${p.slot}`;

/** Who stood in each slot just before each entry. Entries name slots, not Pokémon, so a line in
 *  "turn so far" needs this to say who moved; `last` also remembers who stood in a slot that is
 *  now empty, for "Who came in for Amoonguss?". */
export function occupants(journal: Entry[]): { before: Record<string, string>[]; last: Record<string, string> } {
  const occ: Record<string, string> = {};
  const last: Record<string, string> = {};
  const before: Record<string, string>[] = [];
  for (const e of journal) {
    before.push({ ...occ });
    const k = `${e.side}${e.slot}`;
    if ((e.kind === "lead" || e.kind === "switch") && typeof e.species === "string") {
      occ[k] = last[k] = e.species;
    } else if (e.kind === "swap") {
      const other = `${e.side}${1 - Number(e.slot)}`;
      [occ[k], occ[other]] = [occ[other], occ[k]];
      [last[k], last[other]] = [last[other], last[k]];
      for (const x of [k, other]) if (occ[x] === undefined) delete occ[x];
    } else if (e.kind === "faint" || (e.kind === "damage" && e.fainted)) {
      delete occ[k];
    }
  }
  return { before, last };
}

/** The index of the last `turn` entry, or -1 before turn 1. */
export function turnStart(journal: Entry[]): number {
  for (let i = journal.length - 1; i >= 0; i--) if (journal[i].kind === "turn") return i;
  return -1;
}

/** A Pokémon that took its turn without moving. Logged as a `note` until the backend has a `cant`
 *  entry: a note changes nothing, and the stepper still needs to know the Pokémon has acted. */
export type Cant = { side: Side; slot: number; reason: string };
export const CANT_REASONS = ["flinched", "fully paralysed", "asleep", "frozen", "recharging"];

/** Everyone who has had their turn since the last `turn` entry, as `side:species`. A switch counts
 *  for the Pokémon coming in, which does not act in the turn it arrives. Kept by Pokémon rather
 *  than slot, because Ally Switch moves a Pokémon that has acted into a slot that has not. */
export function actedThisTurn(journal: Entry[]): Set<string> {
  const { before } = occupants(journal);
  const out = new Set<string>();
  for (let i = turnStart(journal) + 1; i < journal.length; i++) {
    const e = journal[i];
    const who = (side: unknown, slot: unknown) => before[i][`${side}${slot}`];
    if (e.kind === "move" && !e.called_by) {
      const sp = who(e.side, e.slot);
      if (sp) out.add(`${e.side}:${toId(sp)}`);
    } else if (e.kind === "switch" && typeof e.species === "string") {
      out.add(`${e.side}:${toId(e.species)}`);
    } else if (e.kind === "note" && e.cant) {
      const c = e.cant as Cant;
      const sp = who(c.side, c.slot);
      if (sp) out.add(`${c.side}:${toId(sp)}`);
    }
  }
  return out;
}

/** Whether anything has moved yet this turn — once it has, the start of the turn is over. */
export function movedThisTurn(journal: Entry[]): boolean {
  return journal.slice(turnStart(journal) + 1).some((e) => e.kind === "move" || (e.kind === "note" && e.cant));
}

export function active(view: LiveView, p: Pos): LiveMon | null {
  return view.sides[p.side].mons.find((m) => m.state === "active" && m.slot === p.slot) ?? null;
}

/** Who could still come in. Once four of a side have been seen, the two that have not were not
 *  brought, so they stop being offered even where the backend still lists them as unrevealed. */
export function benchFor(view: LiveView, side: Side): { species: string; hp: number; state: string }[] {
  const seen = view.sides[side].mons.filter((m) => m.state !== "unrevealed" && m.state !== "not_brought").length;
  return view.menu[side].bench.filter((b) => seen < BRING || b.state !== "unrevealed");
}

/** The side that has lost, once one has: every Pokémon it brought has fainted. */
export function loser(view: LiveView): Side | null {
  if (!view.started) return null;
  for (const side of ["p1", "p2"] as Side[]) {
    const fainted = view.sides[side].mons.filter((m) => m.state === "fainted").length;
    const standing = view.menu[side].actives.some(Boolean) || benchFor(view, side).length > 0;
    if (fainted >= BRING || !standing) return side;
  }
  return null;
}

/** Empty slots that have to be filled before the next turn, in screen order, with who fell there. */
export function replacements(view: LiveView): { pos: Pos; was: string | null }[] {
  const { last } = occupants(view.journal);
  return screenOrder()
    .filter((p) => !view.menu[p.side].actives[p.slot] && benchFor(view, p.side).length > 0)
    .map((p) => ({ pos: p, was: last[posKey(p)] ?? null }));
}

// --- end of turn ---------------------------------------------------------------------------

/** Something that could happen at the end of the turn, with the HP it would leave. */
export type Residual = {
  key: string; label: string; pos: Pos; species: string;
  kind: "damage" | "heal";
  /** What it would leave: a percentage for theirs, a real number for yours. */
  expect: { pct?: number; hp?: number };
};

/** What could happen at the end of this turn, in the cartridge's order of effects and in screen
 *  order within each. The list is a set of possibilities, not a prediction: you confirm each that
 *  happened in the order you saw it, and say when the rest did not.
 *
 *  `types` is each species' types where the pool knows them; a Pokémon whose types are unknown is
 *  offered rather than skipped, because a missing row costs a tap and a missing event costs HP. */
export function residuals(view: LiveView, own: Side | null, types: Map<string, string[]>): Residual[] {
  const mons = screenOrder().map((p) => ({ p, m: active(view, p) })).filter((x) => x.m) as { p: Pos; m: LiveMon }[];
  const typeOf = (m: LiveMon) => types.get(toId(m.forme)) ?? types.get(toId(m.species)) ?? null;
  const out: Residual[] = [];
  const add = (label: string, p: Pos, m: LiveMon, kind: "damage" | "heal", frac: number) => {
    if (kind === "heal" && m.hp >= 1) return;
    const sign = kind === "heal" ? 1 : -1;
    const mine = p.side === own && m.hp_exact != null && m.hp_max != null;
    const expect = mine
      ? { hp: Math.max(0, Math.min(m.hp_max!, m.hp_exact! + sign * Math.max(1, Math.floor(m.hp_max! * frac)))) }
      : { pct: Math.max(0, Math.min(100, Math.round(m.hp * 100 + sign * frac * 100))) };
    out.push({ key: `${label}:${posKey(p)}`, label, pos: p, species: m.forme, kind, expect });
  };
  if (view.field.weather === "sandstorm") {
    for (const { p, m } of mons) {
      const t = typeOf(m);
      if (!t || !t.some((x) => ["Rock", "Ground", "Steel"].includes(x))) add("Sandstorm", p, m, "damage", 1 / 16);
    }
  }
  if (view.field.terrain === "grassyterrain") {
    for (const { p, m } of mons) {
      const t = typeOf(m);
      const floats = t?.includes("Flying") || m.ability === "levitate" || m.item === "airballoon";
      if (!floats) add("Grassy Terrain", p, m, "heal", 1 / 16);
    }
  }
  for (const { p, m } of mons) {
    if (m.item === "leftovers") add("Leftovers", p, m, "heal", 1 / 16);
    if (m.item === "blacksludge") {
      const poison = typeOf(m)?.includes("Poison");
      add("Black Sludge", p, m, poison ? "heal" : "damage", poison ? 1 / 16 : 1 / 8);
    }
  }
  for (const { p, m } of mons) {
    if (m.status === "psn") add("Poison", p, m, "damage", 1 / 8);
    if (m.status === "tox") add("Bad poison", p, m, "damage", 1 / 16);
  }
  for (const { p, m } of mons) if (m.status === "brn") add("Burn", p, m, "damage", 1 / 16);
  return out;
}

// --- the turn as sentences -------------------------------------------------------------------

export type Line = { index: number; text: string; error: string | null };

const STATUS_WORDS: Record<string, string> = {
  brn: "burned", par: "paralysed", psn: "poisoned", tox: "badly poisoned", slp: "asleep", frz: "frozen",
};

const PRETTY: Record<string, string> = {
  sunnyday: "Sun", raindance: "Rain", sandstorm: "Sandstorm", snow: "Snow",
  grassyterrain: "Grassy Terrain", electricterrain: "Electric Terrain",
  psychicterrain: "Psychic Terrain", mistyterrain: "Misty Terrain", trickroom: "Trick Room",
  reflect: "Reflect", lightscreen: "Light Screen", auroraveil: "Aurora Veil", tailwind: "Tailwind",
};

export function moveName(view: LiveView, id: string): string {
  for (const side of ["p1", "p2"] as Side[]) {
    for (const a of view.menu[side].actives) {
      const m = a?.moves.find((x: MoveOption) => x.id === toId(id));
      if (m) return m.name;
    }
  }
  return PRETTY[toId(id)] ?? id;
}

/** Each entry since the last `turn` entry, written the way the cartridge would write it. A move
 *  and the damage straight after it are one line, as they are one sentence on the screen. */
export function turnLines(view: LiveView, names: Record<Side, string>): Line[] {
  const journal = view.journal;
  const { before } = occupants(journal);
  const errors = new Map<number, string>();
  for (const err of view.errors) {
    const m = /^entry (\d+)/.exec(err);
    if (m) errors.set(Number(m[1]), err.replace(/^entry \d+ \([^)]*\): /, ""));
  }
  // "Them" is a label; a sentence wants "They" and "them".
  const subj = (side: unknown) => (names[side as Side] === "Them" ? "They" : names[side as Side] ?? String(side));
  const obj = (side: unknown) => (names[side as Side] === "Them" ? "them" : names[side as Side] ?? String(side));
  const lines: Line[] = [];
  const start = turnStart(journal) + 1;
  for (let i = start; i < journal.length; i++) {
    const e = journal[i];
    const at = i;
    const who = (side: unknown, slot: unknown) =>
      before[at][`${side}${slot}`] ?? `${names[side as Side] ?? side} ${across(side as Side, Number(slot))}`;
    const hp = (x: Entry, j: number) => {
      const name = before[j][`${x.side}${x.slot}`] ?? who(x.side, x.slot);
      const crit = x.crit ? "A critical hit! " : "";
      if (x.fainted) return `${crit}${name} fainted.`;
      if (x.hp != null) return `${crit}${name} on ${x.hp} HP.`;
      return `${crit}${name} on ${x.pct}%.`;
    };
    let text: string;
    const err = errors.get(at) ?? null;
    switch (e.kind) {
      case "move": {
        const t = e.target as Pos | null;
        text = `${who(e.side, e.slot)} used ${moveName(view, String(e.move))}${t ? ` → ${who(t.side, t.slot)}` : ""}.`;
        while (journal[i + 1]?.kind === "damage" && !errors.has(i + 1)) {
          i++;
          text += ` ${hp(journal[i], i)}`;
        }
        break;
      }
      case "damage": text = hp(e, i); break;
      case "heal": text = `${who(e.side, e.slot)} healed to ${e.hp != null ? `${e.hp} HP` : `${e.pct}%`}.`; break;
      case "switch": {
        const was = before[at][`${e.side}${e.slot}`];
        text = `${subj(e.side)} sent in ${e.species}${was ? ` for ${was}` : ""}.`;
        break;
      }
      case "lead": text = `${subj(e.side)} led with ${e.species}.`; break;
      case "bring": text = `${subj(e.side)} brought ${(e.species as string[]).join(", ")}.`; break;
      case "swap": text = `${who(e.side, e.slot)} traded places with its partner.`; break;
      case "faint": text = `${who(e.side, e.slot)} fainted.`; break;
      case "status": text = e.status ? `${who(e.side, e.slot)} is ${STATUS_WORDS[String(e.status)] ?? e.status}.`
        : `${who(e.side, e.slot)} was cured.`; break;
      case "boost": {
        const n = Number(e.stages);
        text = `${who(e.side, e.slot)}'s ${e.stat} ${n > 0 ? "rose" : "fell"}${Math.abs(n) > 1 ? ` ${Math.abs(n)} stages` : ""}.`;
        break;
      }
      case "field": text = `${PRETTY[toId(String(e.value ?? ""))] ?? e.value ?? e.what} ${e.on === false ? "ended" : "started"}.`; break;
      case "side": text = `${PRETTY[toId(String(e.condition))] ?? e.condition} ${e.on === false ? "ended" : "went up"} for ${obj(e.side)}.`; break;
      case "reveal": text = `${e.species}'s ${e.what} is ${e.value || "nothing"}.`; break;
      case "consume": text = `${e.species} used up its ${e.item}.`; break;
      case "mega": text = `${e.species} Mega Evolved${e.forme ? ` into ${e.forme}` : ""}.`; break;
      case "answer":
        text = e.option == null ? `${e.species ?? "It"}: not sure what happened.`
          : e.label ? `${e.species ?? ""}: ${e.label}.` : "A question answered.";
        break;
      case "note": {
        const c = e.cant as Cant | undefined;
        text = c ? `${who(c.side, c.slot)} couldn't move (${c.reason}).` : String(e.text ?? "");
        break;
      }
      case "end": text = e.winner ? `${names[e.winner as Side]} won.` : "The game ended."; break;
      default: text = e.kind;
    }
    lines.push({ index: at, text, error: err });
  }
  return lines;
}
