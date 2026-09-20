import type { SavedTeam } from "../api";

/** Your side. Picking a saved team is the normal path — the library is the thing you curate —
 *  and pasting is the escape hatch for a team you have not saved (someone else's, or a test). */
export function MyTeamInput({
  teams, activeId, onPick, text, onText, paste, onPaste,
}: {
  teams: SavedTeam[];
  activeId: string | null;
  onPick: (t: SavedTeam) => void;
  text: string;
  onText: (t: string) => void;
  paste: boolean;
  onPaste: (v: boolean) => void;
}) {
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <label style={{ margin: 0 }}>Your team</label>
        <div className="toggle" role="group" aria-label="Team source">
          <button className="seg" aria-pressed={!paste} onClick={() => onPaste(false)}>Saved</button>
          <button className="seg" aria-pressed={paste} onClick={() => onPaste(true)}>Paste</button>
        </div>
      </div>

      {paste ? (
        <textarea value={text} placeholder="Paste a team…" onChange={(e) => onText(e.target.value)} />
      ) : teams.length === 0 ? (
        <div className="empty" style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 14 }}>
          No saved teams yet — add one on the <b>Teams</b> page, or switch to Paste.
        </div>
      ) : (
        <div className="team-list">
          {teams.map((t) => (
            <button key={t.id} className="team-row" aria-current={t.id === activeId} onClick={() => onPick(t)}>
              <span className={`dot ${t.legal === null ? "unknown" : t.legal ? "ok" : "bad"}`} />
              <span className="name">{t.name}</span>
              {t.legal === false && <span className="tiny err">illegal</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
