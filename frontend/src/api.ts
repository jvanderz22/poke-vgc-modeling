// The one place that talks to the Python backend. Every type here mirrors a response in
// src/vgc/web/app.py; if one changes, this file is where it is felt.

export type Problem = { severity: "error" | "warning"; code: string; message: string; pokemon: string | null };

export type Mon = {
  species: string;
  nickname: string | null;
  item: string | null;
  ability: string | null;
  moves: string[];
  level: number | null;
  nature: string | null;
  stats: Record<string, number>;
  sp: Record<string, number>;
};

export type Validation = { ok: boolean; legal: boolean; mons: Mon[]; problems: Problem[] };

export type SavedTeam = {
  id: string;
  name: string;
  text: string;
  notes: string;
  archived: boolean;
  created: string;
  updated: string;
  legal: boolean | null;
  problems: Problem[];
};

export type Health = {
  regulation: string; name: string; format: string;
  level: number; bring: number; team_size: number;
  sp_budget: number; sp_per_stat_cap: number;
  tera: boolean; mega: boolean; open_sheets_only: boolean;
};

export type Gates = {
  version: string; known: boolean; evaluated?: boolean;
  all_pass?: boolean | null; failed?: string[];
  preview_gate?: boolean | null; bring_gate?: boolean | null;
  headline?: { n?: number; logloss?: number; brier?: number; ece?: number };
};

export type ModelRow = Gates & { kind: string; created: string };

export type Species = {
  name: string; id: string; types: string[]; abilities: string[]; is_mega: boolean; seen: number;
};

export type Pool = { species: Species[]; items: string[]; moves: string[]; natures: string[] };

/** A set the backend guessed for a closed-sheet opponent. `share` is how often that exact set was
 *  the one actually used, so 0.15 means the guess is one of many. */
export type InferredSet = {
  species: string; item: string | null; ability: string | null; moves: string[];
  source: "usage" | "default"; share: number | null; seen: number;
};

export type BringOption = { bring: string[]; leads: string[]; back: string[]; wp: number };

export type PreviewResult = {
  version: string;
  gates: Gates;
  best_by_bring: BringOption[];
  options: BringOption[];
  n_options: number;
  preview_wp_player: number | null;
  preview_wp_spectator: number | null;
  their_likely_bring: Record<string, number> | null;
  mine: string[];
  theirs: string[];
  inferred_sets: InferredSet[] | null;
};

export type SimEvent = { kind: string; text: string; side: "p1" | "p2" | null };
export type SimTurn = { turn: number; events: SimEvent[]; wp_p1: number | null };

export type SimResult = {
  battle_id: string; seed: number; format: string;
  winner: "p1" | "p2" | null; turns: number; score: number[];
  policies: { p1: string; p2: string };
  invalid_choices: number; seconds: number;
  version: string | null; wp_error: string | null;
  timeline: SimTurn[];
};

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: init?.body ? { "content-type": "application/json" } : undefined,
  });
  const body = await r.json().catch(() => ({}));
  // FastAPI puts the message in `detail`; Showdown's validator messages arrive this way and are
  // worth showing verbatim, since they name the offending Pokémon.
  if (!r.ok) throw new ApiError(typeof body.detail === "string" ? body.detail : r.statusText);
  return body as T;
}

export const api = {
  health: (reg: string) => call<Health>(`/api/health?regulation=${reg}`),
  models: (reg: string) => call<{ models: ModelRow[]; default: string | null }>(`/api/models?regulation=${reg}`),
  validate: (text: string, regulation: string) =>
    call<Validation>("/api/validate", { method: "POST", body: JSON.stringify({ text, regulation }) }),
  teams: (reg: string) => call<{ teams: SavedTeam[] }>(`/api/teams?regulation=${reg}`),
  saveTeam: (t: Partial<SavedTeam> & { name: string; text: string; regulation: string }) =>
    call<{ team: SavedTeam; validation: Validation }>("/api/teams", { method: "POST", body: JSON.stringify(t) }),
  deleteTeam: (id: string, reg: string) =>
    call<{ deleted: string }>(`/api/teams/${id}?regulation=${reg}`, { method: "DELETE" }),
  pool: (reg: string) => call<Pool>(`/api/pool?regulation=${reg}`),
  compose: (species: string[], regulation: string) =>
    call<{ text: string; sets: InferredSet[] }>("/api/compose", {
      method: "POST", body: JSON.stringify({ species, regulation }),
    }),
  simulate: (body: {
    team_a: string; team_b: string; seed: number;
    policy_a: string; policy_b: string; regulation: string;
  }) => call<SimResult>("/api/simulate", { method: "POST", body: JSON.stringify(body) }),
  /** Give `their_team` for an open sheet, or `their_species` (six) for a closed one. */
  preview: (
    my_team: string,
    opponent: { their_team?: string; their_species?: string[] },
    regulation: string,
    limit = 15,
  ) =>
    call<PreviewResult>("/api/preview", {
      method: "POST",
      body: JSON.stringify({ my_team, regulation, limit, ...opponent }),
    }),
};
