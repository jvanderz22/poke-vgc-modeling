import type { ReactNode } from "react";
import { sideNames, type LiveMon, type LiveView } from "../api";
import { across, type Side } from "../screen";
import { BRING } from "../turn";

const STAT_ORDER = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"];

/** What is known about whether a Pokémon is in this game. Four answers, and the page keeps them
 *  apart because they are different claims: one in the back is alive and can come in; one not
 *  brought never will; one that has not been seen may be either until four of its side have. */
type Standing = "field" | "back" | "fainted" | "out" | "unknown";

const LABEL: Record<Standing, string> = {
  field: "on the field", back: "in the back", fainted: "fainted", out: "not brought", unknown: "brought?",
};
const ORDER: Standing[] = ["field", "back", "fainted", "unknown", "out"];

function standing(m: LiveMon, seen: number): Standing {
  if (m.state === "active") return "field";
  if (m.state === "bench") return "back";
  if (m.state === "fainted") return "fainted";
  if (m.state === "not_brought") return "out";
  // Unrevealed: once four of the side have shown themselves, the rest sat this game out.
  return seen >= BRING ? "out" : "unknown";
}

function hpClass(hp: number) {
  return hp > 0.5 ? "hp-ok" : hp > 0.2 ? "hp-low" : "hp-crit";
}

/** Both sides in full, side by side: who is on the field, who is in the back, who has fainted,
 *  who was not brought and who might not have been, and what is known about each set. This is
 *  what changes slowly and matters when planning, so it sits under the row where you work. */
export function BattleState({ view, extra }: { view: LiveView; extra?: Partial<Record<Side, ReactNode>> }) {
  const names = sideNames(view.perspective);
  const own: Side | null = view.perspective === "spectator" ? null : "p1";
  const order: Side[] = ["p1", "p2"];
  return (
    <div className="panel battle-state">
      <div className="state-grid">
        {order.map((side) => <SideState key={side} view={view} side={side} own={side === own}
                                        title={names[side] === "You" ? "Your side" : names[side] === "Them" ? "Their side" : names[side]}
                                        extra={extra?.[side]} />)}
      </div>
    </div>
  );
}

function SideState({ view, side, own, title, extra }: {
  view: LiveView; side: Side; own: boolean; title: string; extra?: ReactNode;
}) {
  const s = view.sides[side];
  const seen = s.mons.filter((m) => m.state !== "unrevealed" && m.state !== "not_brought").length;
  const rows = s.mons.map((m) => ({ m, st: standing(m, seen) }))
    .sort((a, b) => ORDER.indexOf(a.st) - ORDER.indexOf(b.st) || (a.m.slot ?? 0) - (b.m.slot ?? 0));
  const conditions = Object.keys(s.conditions);
  const spreads = new Map(view.beliefs.filter((b) => b.side === side).map((b) => [b.species, b]));
  const sets = new Map((view.wp?.belief ?? []).map((b) => [b.species, b]));
  const brought = rows.filter((r) => r.st !== "out" && r.st !== "unknown").length;
  return (
    <div className="side-state">
      <div className="side-head">
        <b>{title}</b>
        <span className="tiny dim">{brought} of {BRING} brought {own || brought >= BRING ? "known" : "seen"}</span>
        {conditions.map((c) => <span key={c} className="chip field">{c}</span>)}
      </div>
      <div className="state-rows">
        {rows.map(({ m, st }) => {
          const boosts = STAT_ORDER.filter((k) => m.boosts[k]).map((k) => `${k} ${m.boosts[k] > 0 ? "+" : ""}${m.boosts[k]}`);
          const sp = !own ? spreads.get(m.species) : undefined;
          // One set left is a sheet, or a set this battle has pinned: nothing is open to show.
          const ranked = !own ? sets.get(m.species) : undefined;
          const set = ranked && (ranked.off_meta || ranked.sets > 1) ? ranked : undefined;
          // A stat pinned at 0 is the unused offensive stat, which is 0 by construction: not news.
          const bounds = sp
            ? Object.entries(sp.bounds).filter(([k, [lo, hi]]) => k !== "_unspent" && hi > 0 && (lo > 0 || hi < 32))
                .map(([k, [lo, hi]]) => `${k} ${lo}–${hi}`).join(" · ")
            : "";
          const known = st !== "out" && st !== "unknown";
          return (
            <div key={m.species} className={`state-row st-${st}`}>
              <span className="st-name">
                {m.forme}
                {m.mega && <span className="chip">mega</span>}
                {m.status && <span className={`chip status-${m.status}`}>{m.status}</span>}
              </span>
              <span className="st-label tiny">
                {LABEL[st]}{st === "field" && m.slot != null ? ` · ${across(side, m.slot)}` : ""}
              </span>
              <span className={`board-hp${known ? "" : " empty"}`}>
                {known && <i className={hpClass(m.hp)} style={{ width: `${Math.round(m.hp * 100)}%` }} />}
              </span>
              <span className="st-pct tiny">
                {known ? (own && m.hp_exact != null ? `${m.hp_exact}/${m.hp_max}` : `${Math.round(m.hp * 100)}%`) : ""}
              </span>
              <span className="st-chips">
                {boosts.map((b) => <span key={b} className="chip boost">{b}</span>)}
                <span className={`chip ${m.ability ? "known" : "unknown"}`}>
                  {m.ability ?? (m.ability_unknown.length ? `not ${m.ability_unknown.join(", ")}` : "ability ?")}
                </span>
                <span className={`chip ${m.item !== null ? "known" : "unknown"}`}>
                  {m.item === "" ? (m.lost_item ? `used ${m.lost_item}` : "no item") : m.item ?? "item ?"}
                </span>
                {m.moves_used.length > 0 && !own && !m.moves.length &&
                  <span className="chip known">{m.moves_used.join(", ")}</span>}
              </span>
              {(set || bounds) && (
                <span className="st-belief tiny dim">
                  {set && (set.off_meta ? <span className="warn-text">nobody&apos;s sheet matches</span>
                    : <>{set.sets} set{set.sets === 1 ? "" : "s"} left{set.sets > 1 && <> · top {Math.round(set.concentration * 100)}%</>}</>)}
                  {bounds && <> · spread {bounds}</>}
                  {sp && sp.narrowed > 0 && <> · {Math.round(sp.narrowed * 100)}% ruled out</>}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {extra}
    </div>
  );
}
