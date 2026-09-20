import type { PreviewResult, SavedTeam } from "../api";
import { BringBadges, MonBadge } from "./MonBadge";

export type Session = {
  team: SavedTeam | null;
  teamText: string;
  leads: string[];
  back: string[];
  wp: number;
  theirs: string[];
  theirLikely: Record<string, number> | null;
  version: string;
};

export function startSession(
  result: PreviewResult,
  option: { leads: string[]; back: string[]; wp: number },
  team: SavedTeam | null,
  teamText: string,
): Session {
  return {
    team, teamText,
    leads: option.leads, back: option.back, wp: option.wp,
    theirs: result.theirs ?? [],
    theirLikely: result.their_likely_bring,
    version: result.version,
  };
}

/** The in-battle companion. Not built yet — this page exists so choosing a bring has somewhere to
 *  go, and so the shape of what is missing is written down where it will be built.
 *
 *  Turn-by-turn advice is Phase 6 (expected WP per joint action, from search over a belief) plus
 *  the state-reconstruction work in PLAN.md L5b: turning what you type into a Showdown state the
 *  evaluator can clone and step. That needs its own parity tests — a reconstructed state must
 *  offer the same legal actions and the same damage rolls as the true one — before any number it
 *  produces is worth showing. */
export function BattleSession({ session, onBack }: { session: Session | null; onBack: () => void }) {
  if (!session) {
    return (
      <div className="panel">
        <h2>No battle started</h2>
        <p className="small dim" style={{ margin: 0 }}>
          Rank your brings on the <b>Battle</b> page, then choose <b>Use this team</b> on the option
          you are going with.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <h2 style={{ margin: 0 }}>Your four</h2>
          <button className="ghost" onClick={onBack}>Change</button>
        </div>
        <BringBadges leads={session.leads} back={session.back} />
        <p className="tiny dim" style={{ margin: "10px 0 0" }}>
          {session.team ? <>{session.team.name} · </> : null}
          preview WP {(session.wp * 100).toFixed(1)}% from {session.version}. Leads in blue.
        </p>
      </div>

      {session.theirs.length > 0 && (
        <div className="panel">
          <h2>Against</h2>
          <div className="mon-badges">
            {session.theirs.map((s) => (
              <MonBadge key={s} name={s}
                        extra={session.theirLikely?.[s] != null
                          ? `${(session.theirLikely[s] * 100).toFixed(0)}%`
                          : undefined} />
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <h2>Turn-by-turn — not built yet</h2>
        <p className="small dim">
          This page will take what you saw each turn and answer with the current win probability and
          your actions ranked by expected WP. What it needs first:
        </p>
        <ul className="small dim" style={{ margin: 0, paddingLeft: 18 }}>
          <li>
            <b>State reconstruction.</b> Turning manual input — who is on the field, HP, status,
            boosts, weather, terrain, what was revealed — into a Showdown state the evaluator can
            clone and step. PLAN.md calls this the hard part, and it needs parity tests: the
            reconstructed state must offer the same legal actions and the same damage rolls as the
            true one, or nothing built on it means anything.
          </li>
          <li>
            <b>Expected WP per action</b> (Phase 6): search over joint actions against a belief
            about their sets, with intervals and the worst-case reply — not a single WP number.
          </li>
          <li>
            <b>A belief tracker</b> (Phase 5), so an unrevealed item is uncertain rather than
            guessed, and each reveal narrows it.
          </li>
        </ul>
        <p className="small dim" style={{ marginBottom: 0 }}>
          Until then the WP model only answers at team preview, which is what the Battle page does.
        </p>
      </div>
    </>
  );
}
