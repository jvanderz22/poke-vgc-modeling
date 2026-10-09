// The turn so far, for the Turn Stepper. Where the turn has got to (who has acted, the move still
// owed its results, the slots waiting for a replacement) and what could happen at its end both come
// from the backend (`view.turn_progress`, `view.end_of_turn`), so a reload resumes on the right
// question. What is left here is presentation: who stood where for a sentence, and screen order.

import type { Entry, EndOfTurn, LiveMon, LiveView, MoveOption } from "./api";
import { across, screenOrder, type Pos, type Side } from "./screen";

export const BRING = 4;

export function toId(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, "");
}

export const posKey = (p: Pos) => `${p.side}${p.slot}`;

/** Who stood in each slot just before each entry. Entries name slots, not Pokémon, so a line in
 *  "turn so far" needs this to say who moved. */
export function occupants(journal: Entry[]): Record<string, string>[] {
  const occ: Record<string, string> = {};
  const before: Record<string, string>[] = [];
  for (const e of journal) {
    before.push({ ...occ });
    const k = `${e.side}${e.slot}`;
    if ((e.kind === "lead" || e.kind === "switch") && typeof e.species === "string") {
      occ[k] = e.species;
    } else if (e.kind === "swap") {
      const other = `${e.side}${1 - Number(e.slot)}`;
      [occ[k], occ[other]] = [occ[other], occ[k]];
      for (const x of [k, other]) if (occ[x] === undefined) delete occ[x];
    } else if (e.kind === "faint" || (e.kind === "damage" && e.fainted)) {
      delete occ[k];
    }
  }
  return before;
}

/** The index of the last `turn` entry, or -1 before turn 1. */
export function turnStart(journal: Entry[]): number {
  for (let i = journal.length - 1; i >= 0; i--) if (journal[i].kind === "turn") return i;
  return -1;
}

/** Why a Pokémon took its turn without moving: the reasons a `cant` entry names. */
export const CANT_REASONS: [string, string][] = [
  ["flinch", "Flinched"], ["par", "Fully paralysed"], ["slp", "Asleep"], ["frz", "Frozen"], ["recharge", "Recharging"],
];
const CANT_WORDS: Record<string, string> = {
  flinch: "flinched", par: "fully paralysed", slp: "asleep", frz: "frozen", recharge: "recharging",
};

/** Whether a Pokémon has had its turn, by `turn_progress`. Kept by Pokémon rather than slot,
 *  because Ally Switch moves a Pokémon that has acted into a slot that has not. */
export function actedSet(view: LiveView): Set<string> {
  return new Set((view.turn_progress?.acted ?? []).map((a) => `${a.side}:${toId(a.species)}`));
}

export function active(view: LiveView, p: Pos): LiveMon | null {
  return view.sides[p.side].mons.find((m) => m.state === "active" && m.slot === p.slot) ?? null;
}

/** Who could still come in (the backend leaves out the two a side did not bring, once it shows). */
export function benchFor(view: LiveView, side: Side): { species: string; hp: number; state: string }[] {
  return view.menu[side].bench;
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

/** The end-of-turn list, in the cartridge's order of effects and screen order within each. */
export function endOfTurn(view: LiveView): EndOfTurn[] {
  const order = screenOrder().map(posKey);
  const effects = [...new Set((view.end_of_turn ?? []).map((r) => r.effect))];
  return [...(view.end_of_turn ?? [])].sort((a, b) =>
    effects.indexOf(a.effect) - effects.indexOf(b.effect)
    || order.indexOf(posKey(a)) - order.indexOf(posKey(b)));
}

// --- the turn as sentences -------------------------------------------------------------------

export type Line = { index: number; text: string; error: string | null };

const STATUS_WORDS: Record<string, string> = {
  brn: "burned", par: "paralysed", psn: "poisoned", tox: "badly poisoned", slp: "asleep", frz: "frozen",
};

const RESULT_WORDS: Record<string, string> = {
  miss: "It missed.", protected: "Protected.", immune: "No effect.", failed: "It failed.",
};

const PRETTY: Record<string, string> = {
  sunnyday: "Sun", raindance: "Rain", sandstorm: "Sandstorm", snow: "Snow",
  grassyterrain: "Grassy Terrain", electricterrain: "Electric Terrain",
  psychicterrain: "Psychic Terrain", mistyterrain: "Misty Terrain", trickroom: "Trick Room",
  leftovers: "Leftovers", blacksludge: "Black Sludge", poison: "Poison", badpoison: "Bad poison",
  burn: "Burn", poisonheal: "Poison Heal",
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
  const before = occupants(journal);
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
        if (e.result && e.result !== "hit") text += ` ${RESULT_WORDS[String(e.result)] ?? e.result}`;
        for (const r of (e.results as { side: Side; slot: number; result: string }[] | undefined) ?? []) {
          if (r.result !== "hit") text += ` ${who(r.side, r.slot)}: ${(RESULT_WORDS[r.result] ?? r.result).toLowerCase()}`;
        }
        // What the replay applied on its own lands on the move or on the damage that caused it
        // (recoil, Rough Skin), and all of it belongs to this one sentence.
        const applied = [...(view.applied?.[String(at)] ?? [])];
        while (journal[i + 1]?.kind === "damage" && !errors.has(i + 1)) {
          i++;
          text += ` ${hp(journal[i], i)}`;
          applied.push(...(view.applied?.[String(i)] ?? []));
        }
        text += worked(view.move_effects?.[toId(String(e.move))], e, who, applied);
        break;
      }
      case "damage": text = hp(e, i); break;
      case "heal": {
        // `heal` sets HP alone, whichever way it went: Leftovers, but also sand and recoil.
        const cause = e.eot ? String(e.eot).split(":")[0] : "";
        text = `${cause ? `${PRETTY[cause] ?? cause}: ` : ""}${who(e.side, e.slot)} on ${e.hp != null ? `${e.hp} HP` : `${e.pct}%`}.`;
        break;
      }
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
      case "reveal": text = `${e.species}'s ${e.what} is ${PRETTY[toId(String(e.value ?? ""))] ?? (e.value || "nothing")}.`; break;
      case "consume": text = `${e.species} used up its ${e.item}.`; break;
      case "mega": text = `${e.species} Mega Evolved${e.forme ? ` into ${e.forme}` : ""}.`; break;
      case "answer":
        text = e.option == null ? `${e.species ?? "It"}: not sure what happened.`
          : e.label ? `${e.species ?? ""}: ${e.label}.` : "A question answered.";
        break;
      case "cant": text = `${who(e.side, e.slot)} couldn't move (${CANT_WORDS[String(e.reason)] ?? e.reason}).`; break;
      case "note": text = String(e.text ?? ""); break;
      case "end": text = (e.winner ? `${names[e.winner as Side]} won` : "The game ended")
        + (e.by === "forfeit" ? " by forfeit." : "."); break;
      default: text = e.kind;
    }
    // A Rocky Helmet picked as an answer, say: what it did is on the line that picked it.
    if (e.kind !== "move" && view.applied?.[String(at)]?.length) text += ` Worked out: ${view.applied[String(at)].join("; ")}.`;
    lines.push({ index: at, text, error: err });
  }
  return lines;
}

/** What a move did that nobody typed in: what follows on its own once it hits (Close Combat's
 *  drops), the chance effects you ticked, and what the replay applied itself (Life Orb). The stepper says the first before the HP question
 *  and then stops asking, so the line keeps it on screen once the move is logged. */
function worked(fx: Pick<MoveOption, "follows" | "chance"> | undefined, e: Entry,
                who: (side: unknown, slot: unknown) => string, applied: string[]): string {
  if (!fx) return applied.length ? ` Worked out: ${applied.join("; ")}.` : "";
  const t = e.target as Pos | null;
  const hit = !e.result || e.result === "hit";
  const parts = hit ? (fx.follows ?? []).map((f) => f
    .replace(/^user /, `${who(e.side, e.slot)} `)
    .replace(/^target /, t ? `${who(t.side, t.slot)} ` : "each target hit: ")) : [];
  const chance = (fx.chance ?? []).map((c) => c.label).join(" or ");
  for (const c of (e.chance as Pos[] | undefined) ?? []) parts.push(`${who(c.side, c.slot)} ${chance}`);
  parts.push(...applied);
  return parts.length ? ` Worked out: ${parts.join("; ")}.` : "";
}
