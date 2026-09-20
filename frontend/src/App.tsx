import { useCallback, useEffect, useState } from "react";
import { api, type Health, type ModelRow, type SavedTeam, type Species } from "./api";
import { BattlePanel } from "./components/BattlePanel";
import { TeamLibrary } from "./components/TeamLibrary";

const REG = "reg_mc";

export default function App() {
  const [tab, setTab] = useState<"battle" | "teams">("battle");
  const [health, setHealth] = useState<Health | null>(null);
  const [models, setModels] = useState<{ models: ModelRow[]; default: string | null } | null>(null);
  const [pool, setPool] = useState<Species[]>([]);
  const [teams, setTeams] = useState<SavedTeam[]>([]);
  const [active, setActive] = useState<SavedTeam | null>(null);
  const [offline, setOffline] = useState(false);

  // The team list is shared by both pages — the battle page picks from it, the library edits it —
  // so it is owned here and refreshed on demand rather than fetched twice.
  const refreshTeams = useCallback(async () => {
    try {
      const r = await api.teams(REG);
      setTeams(r.teams);
      setActive((a) => (a ? r.teams.find((t) => t.id === a.id) ?? null : a));
    } catch { /* the banner already says the backend is unreachable */ }
  }, []);

  useEffect(() => {
    api.health(REG).then(setHealth).catch(() => setOffline(true));
    api.models(REG).then(setModels).catch(() => {});
    api.pool(REG).then((p) => setPool(p.species)).catch(() => {});
    refreshTeams();
  }, [refreshTeams]);

  const def = models?.models.find((m) => m.version === models.default);

  return (
    <>
      <header className="app-header">
        <h1>VGC Companion</h1>
        <span className="dim tiny">
          {health
            ? `${health.name} · level ${health.level} · bring ${health.bring} of ${health.team_size} · ${health.sp_budget} SP`
            : "backend unreachable — is `vgc web` running?"}
        </span>
        <nav className="tabs">
          <button className="tab" aria-selected={tab === "battle"} onClick={() => setTab("battle")}>Battle</button>
          <button className="tab" aria-selected={tab === "teams"} onClick={() => setTab("teams")}>Teams</button>
        </nav>
      </header>

      <main>
        {offline && (
          <div className="banner bad">
            Cannot reach the API. Start it with <code>vgc web</code>, or run <code>make web-dev</code> alongside it.
          </div>
        )}

        {tab === "battle" ? (
          <BattlePanel
            reg={REG} health={health} pool={pool}
            teams={teams} active={active} onActive={setActive}
          />
        ) : (
          <TeamLibrary
            reg={REG} health={health} teams={teams}
            onChanged={refreshTeams}
            onUse={(t) => { setActive(t); setTab("battle"); }}
          />
        )}

        {def && (
          <p className="tiny dim" style={{ textAlign: "center", marginTop: 20 }}>
            model {def.version} ·{" "}
            {def.evaluated
              ? def.all_pass ? "all gates pass" : `fails ${def.failed?.join(", ")}`
              : "not evaluated"}
          </p>
        )}
      </main>
    </>
  );
}
