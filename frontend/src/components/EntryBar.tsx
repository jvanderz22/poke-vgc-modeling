import { useEffect, useState } from "react";
import type { Entry, LiveView, MoveOption } from "../api";

type Side = "p1" | "p2";
type Target = { side: Side; slot: number } | null;

/** Moves that hit one chosen Pokémon. Anything else — a spread move, a self-target, the whole
 *  field — has no target to ask about, so asking would be a tap spent on nothing. */
const NEEDS_TARGET = new Set(["normal", "adjacentFoe", "any", "adjacentAlly", "adjacentAllyOrSelf"]);
const SPREADS = new Set(["allAdjacentFoes", "allAdjacent"]);

/** Percentages the HP pad offers. Ten buttons and a KO: coarse on purpose, because the cartridge
 *  shows you a bar and reading it to the nearest 5% is already generous. The damage channel is
 *  built for exactly this — an opponent's HP is known to about a percentage point and the belief
 *  treats it as an interval rather than a number. */
const HP_STEPS = [95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5];

function otherSide(side: Side): Side {
  return side === "p1" ? "p2" : "p1";
}

/** Logging a turn, in as few taps as it can be done in.
 *
 *  The loop a real turn takes: tap who acted, tap what they did, tap who it hit, tap the bar it
 *  left them on. Four taps for one Pokémon's action and its damage, and the two that carry the
 *  most information — the move and the HP — are the two that are one tap each from anywhere.
 *
 *  Your own moves come from the team you built, so they are a grid of four. Theirs start empty
 *  and fill in as you see them, which is why the move field also takes a name you type: on turn
 *  one there is no list to pick from, and by turn three there usually is.
 */
export function EntryBar({ view, selected, onSelect, onLog, busy }: {
  view: LiveView;
  selected: { side: Side; slot: number } | null;
  onSelect: (sel: { side: Side; slot: number } | null) => void;
  onLog: (entries: Entry[]) => void;
  busy: boolean;
}) {
  const [mode, setMode] = useState<"action" | "hp" | "switch" | "field">("action");
  const [move, setMove] = useState<MoveOption | null>(null);
  const [typed, setTyped] = useState("");

  // A new selection is a new action: nothing half-entered should survive it.
  useEffect(() => { setMode("action"); setMove(null); setTyped(""); }, [selected?.side, selected?.slot]);

  const active = selected ? view.menu[selected.side].actives[selected.slot] : null;
  const foes = selected
    ? [0, 1].map((slot) => view.menu[otherSide(selected.side)].actives[slot]).filter(Boolean)
    : [];

  function commitMove(m: MoveOption | { id: string; target: string | null }, target: Target) {
    if (!selected) return;
    onLog([{
      kind: "move", side: selected.side, slot: selected.slot, move: m.id,
      target: target ? { side: target.side, slot: target.slot } : null,
      spread: SPREADS.has(m.target ?? ""),
    }]);
    onSelect(null);
  }

  function pickMove(m: MoveOption) {
    if (!selected) return;
    const needs = NEEDS_TARGET.has(m.target ?? "");
    const choices = [0, 1].filter((slot) => view.menu[otherSide(selected.side)].actives[slot]);
    // One possible target is not a choice, so it is not a question.
    if (!needs) return commitMove(m, null);
    if (choices.length === 1) return commitMove(m, { side: otherSide(selected.side), slot: choices[0] });
    setMove(m);
  }

  if (!selected) {
    return (
      <div className="panel entry-bar">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="small dim">Tap a Pokémon above to log what it did, or its HP bar to log damage.</span>
          <div className="row">
            <button className="ghost" onClick={() => setMode(mode === "field" ? "action" : "field")}>
              Weather · terrain · screens
            </button>
            <button onClick={() => onLog([{ kind: "turn", n: view.turn + 1 }])} disabled={busy}>
              {view.started ? `End turn ${view.turn} →` : "Start turn 1 →"}
            </button>
          </div>
        </div>
        {mode === "field" && <FieldPad view={view} onLog={onLog} busy={busy} />}
      </div>
    );
  }

  const mine = selected.side === "p1";
  const name = active?.species ?? "—";

  return (
    <div className="panel entry-bar">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <b>{name}</b>
        <div className="row">
          <button className="seg" aria-pressed={mode === "action"} onClick={() => setMode("action")}>Move</button>
          <button className="seg" aria-pressed={mode === "switch"} onClick={() => setMode("switch")}>Switch</button>
          <button className="seg" aria-pressed={mode === "hp"} onClick={() => setMode("hp")}>HP</button>
          <button className="link" onClick={() => onSelect(null)}>cancel</button>
        </div>
      </div>

      {mode === "action" && !move && (
        <>
          <div className="pad">
            {(active?.moves ?? []).map((m) => (
              <button key={m.id} className="ghost" disabled={busy} onClick={() => pickMove(m)}>{m.name}</button>
            ))}
          </div>
          {!mine && !active?.moves_known && (
            <p className="tiny dim" style={{ margin: "6px 2px" }}>
              Nothing of theirs has been seen yet — type the move and it joins the list.
            </p>
          )}
          <form className="row" style={{ marginTop: 8 }}
                onSubmit={(e) => { e.preventDefault(); if (typed.trim()) commitMove({ id: typed.trim(), target: "normal" }, null); }}>
            <input type="text" value={typed} placeholder="…or type a move" style={{ flex: 1 }}
                   onChange={(e) => setTyped(e.target.value)} />
            <button className="ghost" disabled={busy || !typed.trim()}>Log</button>
          </form>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="ghost" disabled={busy}
                    onClick={() => { onLog([{ kind: "faint", side: selected.side, slot: selected.slot }]); onSelect(null); }}>
              Fainted
            </button>
            <BoostPad side={selected.side} slot={selected.slot} onLog={onLog} busy={busy} />
          </div>
        </>
      )}

      {mode === "action" && move && (
        <div>
          <div className="small dim" style={{ marginBottom: 6 }}>{move.name} — at which one?</div>
          <div className="pad">
            {foes.map((f) => (
              <button key={f!.slot} className="ghost" disabled={busy}
                      onClick={() => commitMove(move, { side: otherSide(selected.side), slot: f!.slot })}>
                {f!.species}
              </button>
            ))}
            <button className="link" onClick={() => setMove(null)}>back</button>
          </div>
        </div>
      )}

      {mode === "switch" && (
        <div className="pad">
          {view.menu[selected.side].bench.map((b) => (
            <button key={b.species} className="ghost" disabled={busy}
                    onClick={() => {
                      onLog([{ kind: "switch", side: selected.side, slot: selected.slot, species: b.species }]);
                      onSelect(null);
                    }}>
              {b.species} <span className="dim">{Math.round(b.hp * 100)}%</span>
            </button>
          ))}
          {view.menu[selected.side].bench.length === 0 && <span className="small dim">nobody left to bring in</span>}
        </div>
      )}

      {mode === "hp" && <HpPad view={view} sel={selected} onLog={onLog} onDone={() => onSelect(null)} busy={busy} />}
    </div>
  );
}

/** Where the damage number is entered, and the one place the two sides are treated differently:
 *  a percentage for theirs and a real number for yours, exactly as the cartridge gives them. The
 *  belief knows the difference — an opponent's loss is an interval and yours is a measurement. */
function HpPad({ view, sel, onLog, onDone, busy }: {
  view: LiveView;
  sel: { side: Side; slot: number };
  onLog: (entries: Entry[]) => void;
  onDone: () => void;
  busy: boolean;
}) {
  const mon = view.sides[sel.side].mons.find((m) => m.state === "active" && m.slot === sel.slot);
  const [exact, setExact] = useState("");
  if (!mon) return null;
  const own = sel.side === "p1";

  function log(entry: Entry) {
    onLog([entry]);
    onDone();
  }

  return (
    <div>
      <div className="small dim" style={{ marginBottom: 6 }}>
        {mon.forme} is on {Math.round(mon.hp * 100)}% — what is it on now?
      </div>
      <div className="pad hp-pad">
        {HP_STEPS.filter((p) => p < Math.round(mon.hp * 100)).map((p) => (
          <button key={p} className="ghost" disabled={busy}
                  onClick={() => log({ kind: "damage", side: sel.side, slot: sel.slot, pct: p })}>{p}%</button>
        ))}
        <button className="danger" disabled={busy}
                onClick={() => log({ kind: "damage", side: sel.side, slot: sel.slot, fainted: true })}>KO</button>
      </div>
      {own && mon.hp_max != null && (
        <form className="row" style={{ marginTop: 8 }}
              onSubmit={(e) => {
                e.preventDefault();
                const hp = Number(exact);
                if (Number.isFinite(hp)) log({ kind: "damage", side: sel.side, slot: sel.slot, hp });
              }}>
          <input type="text" value={exact} inputMode="numeric" style={{ width: 110 }}
                 placeholder={`of ${mon.hp_max}`} onChange={(e) => setExact(e.target.value)} />
          <button className="ghost" disabled={busy || !exact}>Exact HP</button>
          <span className="tiny dim">yours is a real number, so the belief can use it as one</span>
        </form>
      )}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="link" disabled={busy}
                onClick={() => log({ kind: "heal", side: sel.side, slot: sel.slot, pct: 100 })}>
          back to full
        </button>
      </div>
    </div>
  );
}

const STATUSES = ["brn", "par", "psn", "tox", "slp", "frz"];
const BOOSTABLE = ["atk", "def", "spa", "spd", "spe"];

/** Anything the rules did not derive: a Swords Dance, a burn, a Snarl the app was not told about.
 *  Kept behind one button because it is the uncommon case — most stat changes arrive through a
 *  question, where they cost nothing and pin an ability on the way. */
function BoostPad({ side, slot, onLog, busy }: {
  side: Side; slot: number; onLog: (e: Entry[]) => void; busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!open) return <button className="link" onClick={() => setOpen(true)}>boost · status</button>;
  return (
    <div className="pad" style={{ marginTop: 6 }}>
      {BOOSTABLE.map((stat) => (
        <span key={stat} className="stepper">
          <button className="ghost" disabled={busy}
                  onClick={() => onLog([{ kind: "boost", side, slot, stat, stages: -1 }])}>−</button>
          <span className="stepper-label">{stat}</span>
          <button className="ghost" disabled={busy}
                  onClick={() => onLog([{ kind: "boost", side, slot, stat, stages: 1 }])}>+</button>
        </span>
      ))}
      {STATUSES.map((s) => (
        <button key={s} className="ghost" disabled={busy}
                onClick={() => onLog([{ kind: "status", side, slot, status: s }])}>{s}</button>
      ))}
      <button className="ghost" disabled={busy}
              onClick={() => onLog([{ kind: "status", side, slot, status: null }])}>cured</button>
    </div>
  );
}

const WEATHER = ["sunnyday", "raindance", "sandstorm", "snow"];
const TERRAIN = ["grassyterrain", "electricterrain", "psychicterrain", "mistyterrain"];
const SIDE_CONDS = ["reflect", "lightscreen", "auroraveil", "tailwind"];

/** Field effects that arrive from something the app cannot see — a move, an item, a turn ending.
 *  Setting a terrain here asks the same seed question that a Grassy Surge arriving would, because
 *  it is the same cue and a Grassy Seed does not care what put the grass there. */
function FieldPad({ view, onLog, busy }: { view: LiveView; onLog: (e: Entry[]) => void; busy: boolean }) {
  const set = (what: string, value: string | null) =>
    onLog([{ kind: "field", what, value, on: value !== null }]);
  return (
    <div style={{ marginTop: 10 }}>
      <div className="pad">
        {WEATHER.map((w) => (
          <button key={w} className="ghost" aria-pressed={view.field.weather === w} disabled={busy}
                  onClick={() => set("weather", view.field.weather === w ? null : w)}>{w}</button>
        ))}
      </div>
      <div className="pad" style={{ marginTop: 6 }}>
        {TERRAIN.map((t) => (
          <button key={t} className="ghost" aria-pressed={view.field.terrain === t} disabled={busy}
                  onClick={() => set("terrain", view.field.terrain === t ? null : t)}>{t.replace("terrain", "")}</button>
        ))}
        <button className="ghost" aria-pressed={"trickroom" in view.field.pseudo} disabled={busy}
                onClick={() => onLog([{ kind: "field", what: "pseudo", value: "trickroom",
                                        on: !("trickroom" in view.field.pseudo) }])}>
          trick room
        </button>
      </div>
      <div className="pad" style={{ marginTop: 6 }}>
        {(["p1", "p2"] as Side[]).map((side) =>
          SIDE_CONDS.map((c) => (
            <button key={`${side}${c}`} className="ghost" disabled={busy}
                    aria-pressed={c in view.sides[side].conditions}
                    onClick={() => onLog([{ kind: "side", side, condition: c,
                                            on: !(c in view.sides[side].conditions) }])}>
              {side === "p1" ? "your " : "their "}{c}
            </button>
          )))}
      </div>
    </div>
  );
}
