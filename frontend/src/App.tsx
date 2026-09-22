import { useCallback, useEffect, useState } from "react";
import { api, type Health, type ModelRow, type SavedTeam, type Species } from "./api";
import { BattlePanel } from "./components/BattlePanel";
import { BattleSession } from "./components/BattleSession";
import { EndgamesPanel } from "./components/EndgamesPanel";
import { SimulatePanel } from "./components/SimulatePanel";
import { TeamLibrary } from "./components/TeamLibrary";
import { linkProps, TABS, tabRoute, useRoute, type Tab } from "./router";

const REG = "reg_mc";
const LAST_TEAM = `vgc:${REG}:lastTeam`;

const TITLES: Record<Tab, string> = {
  preview: "Preview", battle: "Battle", simulate: "Simulate", endgames: "Endgames", teams: "Teams",
};

/** Which team you used last, remembered across reloads.
 *
 *  Only the id is stored — the team itself lives on disk and is fetched — so editing a team on the
 *  Teams page cannot leave a stale copy behind, and a deleted team simply stops being restored.
 *  Private-mode browsers throw on localStorage, so every access is guarded. */
function readLastTeam(): string | null {
  try { return localStorage.getItem(LAST_TEAM); } catch { return null; }
}
function writeLastTeam(id: string | null) {
  try { id ? localStorage.setItem(LAST_TEAM, id) : localStorage.removeItem(LAST_TEAM); } catch { /* ignore */ }
}

export default function App() {
  const [route, navigate] = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [models, setModels] = useState<{ models: ModelRow[]; default: string | null } | null>(null);
  const [pool, setPool] = useState<Species[]>([]);
  const [teams, setTeams] = useState<SavedTeam[]>([]);
  const [active, setActiveState] = useState<SavedTeam | null>(null);
  const [offline, setOffline] = useState(false);

  const setActive = useCallback((t: SavedTeam | null) => {
    setActiveState(t);
    writeLastTeam(t?.id ?? null);
  }, []);

  const refreshTeams = useCallback(async () => {
    try {
      const r = await api.teams(REG);
      setTeams(r.teams);
      setActiveState((current) => {
        // Keep the current selection if it still exists; otherwise fall back to the remembered one.
        const wanted = current?.id ?? readLastTeam();
        return r.teams.find((t) => t.id === wanted) ?? null;
      });
    } catch { /* the offline banner already covers this */ }
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
          {TABS.map((t) => (
            // Real links, so a tab can be opened in a new tab or copied like any other URL.
            <a key={t} className="tab" aria-selected={route.tab === t}
               {...linkProps(tabRoute(t), navigate)}>
              {TITLES[t]}
            </a>
          ))}
        </nav>
      </header>

      <main>
        {offline && (
          <div className="banner bad">
            Cannot reach the API. Start it with <code>vgc web</code>, or run <code>make web-dev</code> alongside it.
          </div>
        )}

        {route.tab === "preview" && (
          <BattlePanel
            reg={REG} health={health} pool={pool}
            teams={teams} active={active} onActive={setActive}
            onStart={() => navigate(tabRoute("battle"))}
          />
        )}
        {route.tab === "battle" && (
          <BattleSession reg={REG} route={route} navigate={navigate} teams={teams} />
        )}
        {route.tab === "simulate" && <SimulatePanel reg={REG} teams={teams} />}
        {route.tab === "endgames" && <EndgamesPanel reg={REG} route={route} navigate={navigate} />}
        {route.tab === "teams" && (
          <TeamLibrary
            reg={REG} health={health} teams={teams} route={route} navigate={navigate}
            onChanged={refreshTeams}
            onUse={(t) => { setActive(t); navigate(tabRoute("preview")); }}
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
