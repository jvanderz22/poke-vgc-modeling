import type { LiveMon, LiveView, SpeedRead } from "../api";

const STAT_ORDER = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"];

function hpClass(hp: number) {
  return hp > 0.5 ? "good" : hp > 0.2 ? "warn" : "bad";
}

/** One Pokémon on the field. HP reads as a percentage for both sides because that is what the
 *  cartridge shows you; your own also carries the real number, which it can because you built it. */
export function ActiveCard({ mon, own, onHp, onPick, selected }: {
  mon: LiveMon;
  own: boolean;
  onHp: () => void;
  onPick: () => void;
  selected: boolean;
}) {
  const boosts = STAT_ORDER.filter((s) => mon.boosts[s]).map((s) => `${s} ${mon.boosts[s] > 0 ? "+" : ""}${mon.boosts[s]}`);
  return (
    <div className={`active-card${selected ? " selected" : ""}`}>
      <button className="active-name" onClick={onPick}>
        {mon.forme}
        {mon.mega && <span className="chip">mega</span>}
        {mon.status && <span className={`chip status-${mon.status}`}>{mon.status}</span>}
      </button>

      <button className="hp-bar" onClick={onHp} title="log damage">
        <span className={`hp-fill ${hpClass(mon.hp)}`} style={{ width: `${Math.round(mon.hp * 100)}%` }} />
        <span className="hp-text">
          {Math.round(mon.hp * 100)}%
          {own && mon.hp_exact != null && mon.hp_max != null && (
            <span className="dim"> · {mon.hp_exact}/{mon.hp_max}</span>
          )}
        </span>
      </button>

      <div className="chips">
        {boosts.map((b) => <span key={b} className="chip boost">{b}</span>)}
        {mon.volatiles.map((v) => <span key={v} className="chip">{v}</span>)}
      </div>

      {/* What is known about the set, and — as importantly — what is not. An opponent's blank
          item is not "no item": it is an item nobody has seen, and the two must not look alike. */}
      <div className="chips small-chips">
        <span className={`chip ${mon.ability ? "known" : "unknown"}`}>
          {mon.ability ?? (mon.ability_unknown.length ? `not ${mon.ability_unknown.join(", ")}` : "ability ?")}
        </span>
        <span className={`chip ${mon.item !== null ? "known" : "unknown"}`}>
          {mon.item === "" ? (mon.lost_item ? `used ${mon.lost_item}` : "no item")
            : mon.item ?? "item ?"}
        </span>
      </div>
    </div>
  );
}

function Arrow({ read }: { read: SpeedRead }) {
  const text = read.verdict === "faster" ? "you move first"
    : read.verdict === "slower" ? "they move first"
    : read.verdict === "undecided" ? "not decided yet" : "unknown";
  return (
    <span className={`speed-read ${read.verdict}`} title={
      read.their_speed
        ? `${read.mine} ${read.my_speed?.[0]}–${read.my_speed?.[1]} vs ${read.theirs} ${read.their_speed[0]}–${read.their_speed[1]}`
        : undefined}>
      {read.mine} → {read.theirs}: {text}{read.trick_room ? " (Trick Room)" : ""}
    </span>
  );
}

/** The field, and what the Speed bound says about it.
 *
 *  The Speed line is the one number a turn actually turns on, and it is a *bound*: it says "you
 *  move first" only when nothing they could have invested changes that, and otherwise says it has
 *  not been decided. That third answer appears as often as it is true, because being told you
 *  outspeed and being wrong loses a game and being told "not yet" costs nothing.
 */
export function Field({ view, onHp, onPick, selected }: {
  view: LiveView;
  onHp: (side: "p1" | "p2", slot: number) => void;
  onPick: (side: "p1" | "p2", slot: number) => void;
  selected: { side: string; slot: number } | null;
}) {
  const actives = (side: "p1" | "p2") =>
    [0, 1].map((slot) => view.sides[side].mons.find((m) => m.state === "active" && m.slot === slot) ?? null);

  const field = [
    view.field.weather, view.field.terrain, ...Object.keys(view.field.pseudo),
  ].filter(Boolean) as string[];

  return (
    <div className="panel field-panel">
      <div className="field-head">
        <span className="dim tiny">Them</span>
        {field.length > 0 && <span className="chips">{field.map((f) => <span key={f} className="chip field">{f}</span>)}</span>}
      </div>
      <div className="field-row">
        {actives("p2").map((m, i) => m
          ? <ActiveCard key={i} mon={m} own={false} onHp={() => onHp("p2", i)} onPick={() => onPick("p2", i)}
                        selected={selected?.side === "p2" && selected.slot === i} />
          : <div key={i} className="active-card empty">empty</div>)}
      </div>

      <div className="speed-strip">
        {view.speed.length === 0
          ? <span className="tiny dim">Speed reads once both sides are on the field.</span>
          : view.speed.map((r, i) => <Arrow key={i} read={r} />)}
      </div>

      <div className="field-row">
        {actives("p1").map((m, i) => m
          ? <ActiveCard key={i} mon={m} own onHp={() => onHp("p1", i)} onPick={() => onPick("p1", i)}
                        selected={selected?.side === "p1" && selected.slot === i} />
          : <div key={i} className="active-card empty">empty</div>)}
      </div>
      <div className="field-head"><span className="dim tiny">You</span></div>
    </div>
  );
}
