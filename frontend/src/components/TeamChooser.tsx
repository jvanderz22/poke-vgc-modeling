import { useState } from "react";
import type { SavedTeam } from "../api";

/** Pick a saved team or paste one. Used wherever a team is an input rather than the subject. */
export function TeamChooser({
  label, teams, text, onText,
}: {
  label: string;
  teams: SavedTeam[];
  text: string;
  onText: (t: string) => void;
}) {
  const [pickedId, setPickedId] = useState("");

  return (
    <div>
      <label>{label}</label>
      <select
        className="form-select form-select-sm"
        value={pickedId}
        onChange={(e) => {
          setPickedId(e.target.value);
          const t = teams.find((x) => x.id === e.target.value);
          if (t) onText(t.text);
        }}
        style={{ marginBottom: 7 }}
      >
        <option value="">— paste below, or pick a saved team —</option>
        {teams.map((t) => (
          <option key={t.id} value={t.id}>{t.name}{t.legal === false ? " (illegal)" : ""}</option>
        ))}
      </select>
      <textarea value={text} placeholder="Paste a team…"
                onChange={(e) => { onText(e.target.value); setPickedId(""); }} />
    </div>
  );
}
