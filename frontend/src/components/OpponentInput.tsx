import type { Species } from "../api";
import { SpeciesPicker } from "./SpeciesPicker";

export type SheetMode = "open" | "closed";

/** How you tell the app what the opponent has.
 *
 *  **Open** (Bo3): you are shown their full sheet, so paste it. The models were trained on exactly
 *  this, and it is the only mode whose answers are as good as the model gets.
 *
 *  **Closed**: you only see six species at preview. The backend fills each with its most common
 *  real set so there is something to simulate, which is a guess — and the share it reports says
 *  how thin a guess. Phase 5 replaces this with a belief that updates as the battle reveals things. */
export function OpponentInput({
  mode, onMode, text, onText, species, onSpecies, pool, teamSize,
}: {
  mode: SheetMode;
  onMode: (m: SheetMode) => void;
  text: string;
  onText: (t: string) => void;
  species: (string | null)[];
  onSpecies: (s: (string | null)[]) => void;
  pool: Species[];
  teamSize: number;
}) {
  const chosen = species.filter(Boolean) as string[];
  // Species Clause: the same Pokémon cannot appear twice, so hide what is already taken.
  const taken = new Set(chosen);

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <label style={{ margin: 0 }}>Opponent</label>
        <div className="toggle" role="group" aria-label="Team sheet format">
          <button className="seg" aria-pressed={mode === "open"} onClick={() => onMode("open")}>Open sheet</button>
          <button className="seg" aria-pressed={mode === "closed"} onClick={() => onMode("closed")}>Closed</button>
        </div>
      </div>

      {mode === "open" ? (
        <>
          <textarea value={text} placeholder="Paste their team sheet…" onChange={(e) => onText(e.target.value)} />
          <p className="tiny dim" style={{ margin: "6px 2px 0" }}>
            The sheet you are shown at the start of a Bo3 game. This is what the model was trained on.
          </p>
        </>
      ) : (
        <>
          <div className="slots">
            {Array.from({ length: teamSize }, (_, i) => (
              <SpeciesPicker
                key={i}
                pool={species[i] ? pool : pool.filter((s) => !taken.has(s.name))}
                value={species[i] ?? null}
                placeholder={`Pokémon ${i + 1}`}
                onPick={(name) => {
                  const next = [...species];
                  next[i] = name;
                  onSpecies(next);
                }}
              />
            ))}
          </div>
          <p className="tiny dim" style={{ margin: "8px 2px 0" }}>
            {chosen.length}/{teamSize} chosen. Their sets are unknown in a closed-sheet game, so each
            Pokémon is given the set most often used with it. That is a guess, and the result below
            says how common each one was.
          </p>
        </>
      )}
    </div>
  );
}
