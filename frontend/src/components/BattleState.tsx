import type { ReactNode } from "react";
import { sideNames, type LiveMon, type LiveView } from "../api";
import { across, type Side } from "../screen";
import { BRING } from "../turn";

const STAT_ORDER = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"];

/** What is known about whether a Pokémon is in this game. Four answers, and the page keeps them
 *  apart because they are different claims: one in the back is alive and can come in; one not
 *  brought never will; one that has not been seen may be either until four of its side have. */
export type Standing = "field" | "back" | "fainted" | "out" | "unknown";

export const LABEL: Record<Standing, string> = {
  field: "on the field", back: "in the back", fainted: "fainted", out: "not brought", unknown: "brought?",
};
export const ORDER: Standing[] = ["field", "back", "fainted", "unknown", "out"];

export function standing(m: LiveMon, seen: number): Standing {
  if (m.state === "active") return "field";
  if (m.state === "bench") return "back";
  if (m.state === "fainted") return "fainted";
  if (m.state === "not_brought") return "out";
  // Unrevealed: once four of the side have shown themselves, the rest sat this game out.
  return seen >= BRING ? "out" : "unknown";
}

export function hpClass(hp: number) {
  return hp > 0.5 ? "hp-ok" : hp > 0.2 ? "hp-low" : "hp-crit";
}

/** The side whose sets are yours to know: none while watching. */
export function ownSide(view: LiveView): Side | null {
  return view.perspective === "spectator" ? null : "p1";
}

/** What the beliefs add about one of their six: the sets still open and the spread's bounds. */
export function beliefOf(view: LiveView, side: Side, m: LiveMon) {
  if (side === ownSide(view)) return { sp: undefined, set: undefined, bounds: "" };
  const sp = view.beliefs.find((b) => b.side === side && b.species === m.species);
  // A set the sheet gives whole has nothing left to rank, and whether the corpus ever saw
  // it is not news. One set left is a set this battle has pinned: nothing open either.
  const whole = m.moves.length > 0 && m.item !== null && m.ability !== null && m.nature !== null;
  const ranked = !whole ? view.wp?.belief?.find((b) => b.species === m.species) : undefined;
  const set = ranked && (ranked.off_meta || ranked.sets > 1) ? ranked : undefined;
  // A stat pinned at 0 is the unused offensive stat, which is 0 by construction: not news.
  const bounds = sp
    ? Object.entries(sp.bounds).filter(([k, [lo, hi]]) => k !== "_unspent" && hi > 0 && (lo > 0 || hi < 32))
        .map(([k, [lo, hi]]) => `${k} ${lo}–${hi}`).join(" · ")
    : "";
  return { sp, set, bounds };
}

const STATS = ["hp", "atk", "def", "spa", "spd", "spe"];

/** One Pokémon's set, all of it that is known, for a card's popover: what the sheet or the battle
 *  has shown, what is still open, and what the beliefs have narrowed it to. A guess is never shown
 *  as though it were known. */
export function SetDetail({ view, side, m, st, name = (x) => x }: {
  view: LiveView; side: Side; m: LiveMon; st: Standing; name?: (id: string) => string;
}) {
  const own = side === ownSide(view);
  const { sp, set, bounds } = beliefOf(view, side, m);
  const known = st !== "out" && st !== "unknown";
  const item = m.item === "" ? (m.lost_item ? `${name(m.lost_item)} (used)` : "no item") : m.item ? name(m.item) : null;
  const ability = m.ability ? name(m.ability)
    : m.ability_unknown.length ? `not ${m.ability_unknown.map(name).join(", ")}` : null;
  const boosts = STAT_ORDER.filter((k) => m.boosts[k]).map((k) => `${k} ${m.boosts[k] > 0 ? "+" : ""}${m.boosts[k]}`);
  const used = new Set(m.moves_used);
  const unseen = (what: string) => <span className="dim">{what}</span>;
  return (
    <div className="set-detail">
      <div className="sd-head">
        <b>{m.forme}</b>
        {m.mega && <span className="chip">mega</span>}
        {m.status && <span className={`chip status-${m.status}`}>{m.status}</span>}
        <span className="tiny dim sd-where">
          {LABEL[st]}{known ? ` · ${own && m.hp_exact != null ? `${m.hp_exact}/${m.hp_max}` : `${Math.round(m.hp * 100)}%`}` : ""}
        </span>
      </div>
      <dl className="sd-set">
        <dt>Item</dt>
        <dd>{item ? <>{item}{m.item_source === "belief" && <span className="dim"> (likely)</span>}</> : unseen("not seen")}</dd>
        <dt>Ability</dt>
        <dd>{m.ability ? <>{ability}{m.ability_source === "belief" && <span className="dim"> (likely)</span>}</>
          : ability ? <span className="dim">{ability}</span> : unseen("not seen")}</dd>
        <dt>Nature</dt>
        <dd>{m.nature ?? unseen("not shown")}</dd>
        <dt>Moves</dt>
        <dd>
          {m.moves.length > 0
            ? <>
                <ul className="sd-moves">{m.moves.map((mv) => (
                  <li key={mv} className={used.has(mv) ? "used" : undefined}>{name(mv)}</li>
                ))}</ul>
                {m.moves.some((mv) => used.has(mv)) && <span className="tiny dim">bold: used this game</span>}
              </>
            : m.moves_used.length > 0
              ? <>{m.moves_used.map(name).join(", ")} <span className="dim">(seen so far)</span></>
              : unseen("none seen")}
        </dd>
        {m.stats && <>
          <dt>Stats</dt>
          <dd className="mono">{STATS.filter((k) => m.stats![k] != null).map((k) => `${k} ${m.stats![k]}`).join(" · ")}</dd>
        </>}
        {(boosts.length > 0 || m.volatiles.length > 0) && <>
          <dt>Now</dt>
          <dd>
            {boosts.map((b) => <span key={b} className="chip boost">{b}</span>)}
            {m.volatiles.map((v) => <span key={v} className="chip">{name(v)}</span>)}
          </dd>
        </>}
      </dl>
      {(set || bounds || sp?.contradicted) && (
        <div className="sd-belief tiny">
          {set && (set.off_meta ? <div className="warn-text">nobody&apos;s sheet matches</div>
            : <div>{set.sets} sets left · top {Math.round(set.concentration * 100)}%
                {set.evidence.length > 0 && <span className="dim"> · from {set.evidence.join("; ")}</span>}</div>)}
          {bounds && <div>Stat Points {bounds}
            {sp && sp.narrowed > 0 && <span className="dim"> · {Math.round(sp.narrowed * 100)}% ruled out</span>}</div>}
          {sp?.contradicted && <div className="warn-text">what was logged can&apos;t all be true ({sp.contradicted})</div>}
        </div>
      )}
    </div>
  );
}

/** Both sides in full, side by side: who is on the field, who is in the back, who has fainted,
 *  who was not brought and who might not have been, and what is known about each set. This is
 *  what changes slowly and matters when planning, so it sits under the row where you work. */
export function BattleState({ view, extra }: { view: LiveView; extra?: Partial<Record<Side, ReactNode>> }) {
  const names = sideNames(view);
  const own = ownSide(view);
  const order: Side[] = ["p1", "p2"];
  return (
    <div className="panel battle-state">
      <div className="state-grid">
        {order.map((side) => <SideState key={side} view={view} side={side} own={side === own}
                                        title={names[side] === "You" ? "Your side" : `${names[side]}'s side`}
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
  const brought = rows.filter((r) => r.st !== "out" && r.st !== "unknown").length;
  return (
    <div className="side-state">
      <div className="side-head">
        <b className="side-name" title={title}>{title}</b>
        <span className="tiny dim">{brought} of {BRING} brought {own || brought >= BRING ? "known" : "seen"}</span>
        {conditions.map((c) => <span key={c} className="chip field">{c}</span>)}
      </div>
      <div className="state-rows">
        {rows.map(({ m, st }) => {
          const boosts = STAT_ORDER.filter((k) => m.boosts[k]).map((k) => `${m.boosts[k] > 0 ? "▲" : "▼"} ${k} ${m.boosts[k] > 0 ? "+" : ""}${m.boosts[k]}`);
          const { sp, set, bounds } = beliefOf(view, side, m);
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
