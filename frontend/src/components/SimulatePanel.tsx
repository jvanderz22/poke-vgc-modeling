import { useState } from "react";
import { api, ApiError, type SavedTeam, type SimResult } from "../api";
import { TeamChooser } from "./TeamChooser";

const POLICIES = [
  { id: "heuristic", label: "Heuristic" },
  { id: "random", label: "Random" },
];

/** Play two teams against each other and read the battle back turn by turn.
 *
 *  The same seeded runner that generates the training data, so a seed replays exactly and a turn
 *  is worth pointing at. Both sides are driven by Phase 2's heuristic — it beats random 98.4% of
 *  the time and that is the whole claim. What it plays is not what a strong player would play, so
 *  read this as "what happens between these teams under a simple policy", not as advice. */
export function SimulatePanel({ reg, teams }: { reg: string; teams: SavedTeam[] }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [seed, setSeed] = useState(1);
  const [policyA, setPolicyA] = useState("heuristic");
  const [policyB, setPolicyB] = useState("heuristic");
  const [result, setResult] = useState<SimResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(nextSeed = seed) {
    if (!a.trim() || !b.trim()) { setError("two teams are required"); return; }
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await api.simulate({
        team_a: a, team_b: b, seed: nextSeed, policy_a: policyA, policy_b: policyB, regulation: reg,
      }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="panel">
        <div className="grid2">
          <TeamChooser label="Side A (p1)" teams={teams} text={a} onText={setA} />
          <TeamChooser label="Side B (p2)" teams={teams} text={b} onText={setB} />
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label htmlFor="seed">Seed</label>
            <input id="seed" type="number" value={seed} style={{ width: 96 }}
                   onChange={(e) => setSeed(Number(e.target.value) || 0)} />
          </div>
          <div>
            <label htmlFor="pa">A plays</label>
            <select id="pa" className="form-select form-select-sm" value={policyA}
                    onChange={(e) => setPolicyA(e.target.value)}>
              {POLICIES.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="pb">B plays</label>
            <select id="pb" className="form-select form-select-sm" value={policyB}
                    onChange={(e) => setPolicyB(e.target.value)}>
              {POLICIES.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <div style={{ alignSelf: "flex-end" }}>
            <button onClick={() => run()} disabled={busy}>{busy ? "Playing…" : "Play battle"}</button>
          </div>
          {result && (
            <div style={{ alignSelf: "flex-end" }}>
              <button className="ghost" disabled={busy}
                      onClick={() => { const s = seed + 1; setSeed(s); run(s); }}>
                Next seed
              </button>
            </div>
          )}
          {error && <span className="err small" style={{ alignSelf: "flex-end" }}>{error}</span>}
        </div>

        <p className="tiny dim" style={{ margin: "10px 0 0" }}>
          A battle is a pure function of (seed, teams, policies), so the same seed replays exactly.
          Both sides use Phase&nbsp;2's heuristic unless you change it — decent, not strong, so a
          line it takes is not evidence the line is good.
        </p>
      </div>

      {result && <Timeline result={result} />}
    </>
  );
}

function Timeline({ result }: { result: SimResult }) {
  const winnerLabel = result.winner === "p1" ? "Side A wins" : result.winner === "p2" ? "Side B wins" : "Tie";
  return (
    <>
      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>{winnerLabel}</h2>
          <span className="dim small">
            {result.turns} turns · seed {result.seed} · {result.seconds}s
            {result.invalid_choices > 0 && <span className="err"> · {result.invalid_choices} invalid choices</span>}
          </span>
        </div>
        {result.wp_error && (
          <p className="small err" style={{ margin: "8px 0 0" }}>
            Win probability unavailable: {result.wp_error}
          </p>
        )}
      </div>

      {result.timeline.map((t) => (
        <div className="panel turn" key={t.turn}>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}>{t.turn === 0 ? "Lead" : `Turn ${t.turn}`}</h2>
            {t.wp_p1 != null && (
              <span className="wp-chip" title="Spectator win probability for Side A at this decision point">
                A {(t.wp_p1 * 100).toFixed(0)}%
                <span className="wp-track"><i style={{ width: `${t.wp_p1 * 100}%` }} /></span>
              </span>
            )}
          </div>
          <ul className="events">
            {t.events.map((e, i) => (
              <li key={i} className={e.side ? `ev ev-${e.side}` : "ev"}>
                {e.side && <span className="badge rounded-pill text-bg-dark ev-side">{e.side === "p1" ? "A" : "B"}</span>}
                <span>{e.text}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  );
}
