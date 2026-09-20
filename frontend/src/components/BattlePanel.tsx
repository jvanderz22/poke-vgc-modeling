import { useEffect, useState } from "react";
import { api, ApiError, type Health, type InferredSet, type PreviewResult, type SavedTeam, type Species } from "../api";
import { GateBanner } from "./GateBanner";
import { MyTeamInput } from "./MyTeamInput";
import { OpponentInput, type SheetMode } from "./OpponentInput";

/** Team preview: which four to bring, and which two to lead. */
export function BattlePanel({
  reg, health, teams, active, onActive, pool,
}: {
  reg: string;
  health: Health | null;
  teams: SavedTeam[];
  active: SavedTeam | null;
  onActive: (t: SavedTeam) => void;
  pool: Species[];
}) {
  const teamSize = health?.team_size ?? 6;
  const [paste, setPaste] = useState(false);
  const [myText, setMyText] = useState("");
  const [mode, setMode] = useState<SheetMode>("open");
  const [theirText, setTheirText] = useState("");
  const [theirSpecies, setTheirSpecies] = useState<(string | null)[]>(() => Array(teamSize).fill(null));
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setTheirSpecies((s) => (s.length === teamSize ? s : Array(teamSize).fill(null)));
  }, [teamSize]);

  const mine = paste ? myText : active?.text ?? "";
  const chosen = theirSpecies.filter(Boolean) as string[];
  const ready = mine.trim() && (mode === "open" ? theirText.trim() : chosen.length === teamSize);

  async function run() {
    if (!ready) {
      setError(mode === "closed" && mine.trim()
        ? `choose all ${teamSize} of their Pokémon`
        : "a team on each side is required");
      return;
    }
    setBusy(true); setError(""); setResult(null);
    try {
      const opponent = mode === "open" ? { their_team: theirText } : { their_species: chosen };
      setResult(await api.preview(mine, opponent, reg));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(false); }
  }

  const best = result?.best_by_bring ?? [];
  const top = best[0]?.wp ?? 1;
  const spread = best.length ? best[0].wp - best[best.length - 1].wp : 0;

  return (
    <>
      <GateBanner gates={result?.gates ?? null} />

      <div className="panel">
        <div className="grid2">
          <MyTeamInput
            teams={teams} activeId={active?.id ?? null} onPick={onActive}
            text={myText} onText={setMyText} paste={paste} onPaste={setPaste}
          />
          <OpponentInput
            mode={mode} onMode={setMode}
            text={theirText} onText={setTheirText}
            species={theirSpecies} onSpecies={setTheirSpecies}
            pool={pool} teamSize={teamSize}
          />
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button onClick={run} disabled={busy || !ready}>{busy ? "Simulating…" : "Rank my brings"}</button>
          {error && <span className="err small">{error}</span>}
          {result && !error && (
            <span className="dim small">{result.n_options} options ranked by {result.version}</span>
          )}
        </div>
      </div>

      {result && (
        <>
          {result.inferred_sets && <InferredNote sets={result.inferred_sets} />}

          <div className="panel">
            <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
              <h2 style={{ margin: 0 }}>Best {health?.bring ?? 4} to bring</h2>
              {result.preview_wp_player != null && (
                <span className="dim small">overall {(result.preview_wp_player * 100).toFixed(1)}%</span>
              )}
            </div>

            {/* A tight spread is the visible symptom of the failing preview gate: the model is
                saying every choice is near-even, which is a fact about the model, not the game. */}
            {spread < 0.05 && best.length > 1 && (
              <div className="small dim" style={{ marginBottom: 8 }}>
                These span {(spread * 100).toFixed(1)} points — the model is not separating them.
              </div>
            )}

            {best.map((o, i) => (
              <div className="opt" key={i}>
                <span className="wp">{(o.wp * 100).toFixed(1)}%</span>
                <span className="bar"><i style={{ width: `${Math.max(2, (o.wp / top) * 100)}%` }} /></span>
                <span className="who">
                  {o.bring.join(", ")}
                  <br />
                  <span className="leads">lead {o.leads.join(" + ")}</span>
                </span>
              </div>
            ))}

            {result.their_likely_bring && (
              <div style={{ marginTop: 16 }}>
                <h2>They are likely to bring</h2>
                {Object.entries(result.their_likely_bring)
                  .sort((a, b) => b[1] - a[1])
                  .map(([s, p]) => <span key={s} className="mon-chip">{s} {(p * 100).toFixed(0)}%</span>)}
              </div>
            )}
          </div>
        </>
      )}

      <div className="panel">
        <h2>Not here yet</h2>
        <p className="small dim" style={{ margin: 0 }}>
          <b>Turn-by-turn move advice</b> needs Phase&nbsp;6 (expected WP and search) plus state
          reconstruction, which needs its own parity tests before anything it suggests is worth
          showing. Closed-sheet answers will improve in Phase&nbsp;5, when a belief over their sets
          replaces the single guess used here.
        </p>
      </div>
    </>
  );
}

/** What was guessed for a closed-sheet opponent, and how thin each guess is. */
function InferredNote({ sets }: { sets: InferredSet[] }) {
  const weak = sets.filter((s) => s.source === "default" || (s.share ?? 0) < 0.25);
  return (
    <div className="panel">
      <h2>Their sets were guessed</h2>
      <p className="small dim" style={{ marginTop: 0 }}>
        A closed sheet shows species only, so each was given the set most often used with it. The
        model then treats that guess as certain — a Choice Scarf read as an Assault Vest is wrong,
        not uncertain.
      </p>
      {sets.map((s, i) => (
        <div className="opt" key={i}>
          <span className="wp small">
            {s.share != null ? `${(s.share * 100).toFixed(0)}%` : "—"}
          </span>
          <span className="who">
            {s.species}{s.item ? <span className="dim"> @ {s.item}</span> : null}
            <br />
            <span className="leads">
              {s.source === "default"
                ? "no usage data — generic legal set"
                : `${s.ability ?? "?"} · ${s.moves.slice(0, 4).join(", ")}`}
            </span>
          </span>
        </div>
      ))}
      {weak.length > 0 && (
        <p className="tiny dim" style={{ marginBottom: 0 }}>
          {weak.length} of {sets.length} are weak guesses (under a quarter of that Pokémon's sheets,
          or no data at all). Switch to an open sheet if you have one.
        </p>
      )}
    </div>
  );
}
