import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Health, type SavedTeam, type Validation } from "../api";
import { linkProps, type Navigate, type Route } from "../router";

type TeamsRoute = Extract<Route, { tab: "teams" }>;

const BLANK = { id: "", name: "", text: "", notes: "" };

/** The team library. Teams live on disk under `data/library/<regulation>.json`, written by the
 *  Python side — nothing is kept in the browser, so the CLI and the app see the same teams and a
 *  cleared browser loses nothing. */
export function TeamLibrary({ reg, health, teams, route, navigate, onChanged, onUse }: {
  reg: string;
  health: Health | null;
  teams: SavedTeam[];
  route: TeamsRoute;
  navigate: Navigate;
  onChanged: () => void | Promise<void>;
  onUse: (t: SavedTeam) => void;
}) {
  const [draft, setDraft] = useState<typeof BLANK>(BLANK);
  const [check, setCheck] = useState<Validation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Which team the URL is pointing at becomes the draft — but only when the id it names actually
  // changes, or every refresh of the list would throw away whatever is half-typed in the form.
  // The teams arrive after the first render, so a deep link waits for the one it named.
  const loaded = useRef<string | null>(null);
  useEffect(() => {
    const want = route.team ?? "";
    if (loaded.current === want) return;
    const team = route.team ? teams.find((t) => t.id === route.team) : null;
    if (route.team && !team) return;
    loaded.current = want;
    setDraft(team ? { id: team.id, name: team.name, text: team.text, notes: team.notes } : BLANK);
    setCheck(null);
  }, [route.team, teams]);

  // Validation is the backend's answer, never a guess made here: it is the regulation's legality
  // snapshot plus the same validator the CLI uses.
  async function validate(text: string) {
    if (!text.trim()) { setCheck(null); return; }
    try { setCheck(await api.validate(text, reg)); } catch (e) { setError(String((e as Error).message)); }
  }

  async function save() {
    if (!draft.name.trim() || !draft.text.trim()) { setError("a name and a team are required"); return; }
    setBusy(true); setError("");
    try {
      const r = await api.saveTeam({ ...draft, regulation: reg });
      setCheck(r.validation);
      setDraft({ ...draft, id: r.team.id });
      // A team that did not have a URL now has one, and the draft is already what it holds.
      loaded.current = r.team.id;
      if (route.team !== r.team.id) navigate({ ...route, team: r.team.id }, { replace: true });
      await onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(false); }
  }

  async function remove(t: SavedTeam) {
    if (!confirm(`Delete “${t.name}”? This cannot be undone.`)) return;
    try {
      await api.deleteTeam(t.id, reg);
      if (route.team === t.id) navigate({ ...route, team: null }, { replace: true });
      if (draft.id === t.id) { loaded.current = ""; setDraft(BLANK); setCheck(null); }
      await onChanged();
    } catch (e) { setError(String((e as Error).message)); }
  }

  return (
    <div className="split">
      <div className="panel">
        <h2>Saved teams <span className="dim tiny">({teams.length})</span></h2>
        {teams.length === 0 && <div className="empty">Nothing saved yet.</div>}
        {teams.map((t) => (
          <div key={t.id} className="team-row" aria-current={draft.id === t.id}>
            <span className={`dot ${t.legal === null ? "unknown" : t.legal ? "ok" : "bad"}`}
                  title={t.legal === null ? "not checked" : t.legal ? "legal" : "illegal"} />
            <a className="link name" {...linkProps({ ...route, team: t.id }, navigate)}>{t.name}</a>
            <button className="link tiny" onClick={() => onUse(t)}>use</button>
            <button className="link tiny err" onClick={() => remove(t)}>×</button>
          </div>
        ))}
        <a className="btn-link ghost" style={{ marginTop: 8 }}
           {...linkProps({ ...route, team: null }, navigate)}>
          New team
        </a>
      </div>

      <div className="panel">
        <h2>{draft.id ? "Edit team" : "New team"}</h2>
        <div className="row" style={{ marginBottom: 10 }}>
          <div style={{ flex: 1, minWidth: 180 }}>
            <label htmlFor="tname">Name</label>
            <input id="tname" type="text" value={draft.name} placeholder="Trick Room Hisuian Arcanine"
                   onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </div>
          <div style={{ flex: 1, minWidth: 180 }}>
            <label htmlFor="tnotes">Notes</label>
            <input id="tnotes" type="text" value={draft.notes} placeholder="optional"
                   onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
          </div>
        </div>
        <label htmlFor="ttext">Showdown / PokéPaste export</label>
        <textarea id="ttext" value={draft.text} placeholder="Paste the team here…"
                  onChange={(e) => setDraft({ ...draft, text: e.target.value })}
                  onBlur={(e) => validate(e.target.value)} />
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
          <button className="ghost" onClick={() => validate(draft.text)}>Validate</button>
          {draft.id && <button className="danger" onClick={() => {
            const t = teams.find((x) => x.id === draft.id); if (t) remove(t);
          }}>Delete</button>}
          {error && <span className="err small">{error}</span>}
        </div>

        {check && <ValidationView check={check} health={health} />}
      </div>
    </div>
  );
}

function ValidationView({ check, health }: { check: Validation; health: Health | null }) {
  const order = ["hp", "atk", "def", "spa", "spd", "spe"];
  return (
    <div style={{ marginTop: 14 }}>
      <div className="small">
        {check.legal
          ? <span className="ok">Legal{health ? ` for ${health.name}` : ""} · {check.mons.length} Pokémon</span>
          : <span className="err">Illegal{health ? ` for ${health.name}` : ""}</span>}
      </div>
      {check.problems.length > 0 && (
        <ul className="problems">
          {check.problems.map((p, i) => (
            <li key={i} className={p.severity}>{p.pokemon ? `${p.pokemon}: ` : ""}{p.message}</li>
          ))}
        </ul>
      )}
      {check.mons.length > 0 && (
        <table className="stats">
          <thead>
            <tr>
              <th>Pokémon</th>
              {order.map((s) => <th key={s}>{s.toUpperCase()}</th>)}
              <th>SP</th>
            </tr>
          </thead>
          <tbody>
            {check.mons.map((m, i) => {
              const used = Object.values(m.sp ?? {}).reduce((a, b) => a + (b || 0), 0);
              return (
                <tr key={i}>
                  <td>{m.species}{m.item ? <span className="dim"> @ {m.item}</span> : null}</td>
                  {order.map((s) => <td key={s}>{m.stats?.[s] ?? "–"}</td>)}
                  <td className={health && used > health.sp_budget ? "err" : "dim"}>{used}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
