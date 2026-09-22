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
  /** Separate verdict for a battle in progress — a model can pass this and fail preview. */
  in_battle_pass?: boolean | null;
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

/** One held-out human game the in-battle model called at 90%+ before it ended.
 *  `side` is the side it favoured, `wp` its confidence at the last decision point, and `correct`
 *  whether that side went on to win — the whole point of the set. */
export type Endgame = {
  replay: string; format: string; url: string;
  players: { p1: string; p2: string };
  rating: number | null;
  turns: number; ended_by: "normal" | "forfeit";
  winner: "p1" | "p2"; side: "p1" | "p2";
  wp: number; correct: boolean;
  points: number; locked_point: number; locked_turn: number;
  left: { p1: number; p2: number };
};

export type EndgameIndex = {
  built: string | null;
  version?: string;
  criteria?: { min_wp: number; hold: number; min_turns: number; held_out: boolean; ots: boolean; human_only: boolean };
  counts?: { cached: number; eligible: number; unfinished: number; too_short: number; selected: number };
  gates?: Gates;
  correct?: number;
  /** `total` is the whole set, `matched` what the filter kept, `games` what fitted in the
   *  response — three different numbers, and the header shows all three when they differ. */
  total?: number;
  matched: number;
  games: Endgame[];
  /** Present only when the index has never been built: what to run. */
  hint?: string;
};

/** What a spectator knows about one of the six on a sheet:
 *  `active` on the field · `bench` selected, off the field · `fainted` selected, knocked out ·
 *  `unknown` not seen yet, so it may or may not have been selected · `unselected` known to be
 *  sitting the game out, which only becomes knowable once the other four have shown themselves. */
export type BoardMon = {
  species: string; forme: string;
  state: "active" | "bench" | "fainted" | "unknown" | "unselected";
  position: number | null; hp: number; status: string | null;
  boosts: Record<string, number>;
};

/** `brought_known` is true once all four selected Pokémon have appeared — at which point the
 *  other two are `unselected` and drop off the board entirely. */
export type BoardSide = { left: number; mons: BoardMon[]; brought_known: boolean; conditions: string[] };

/** What stood in one active slot when a step began and when it ended. `started` is the one that
 *  was there, carrying its state at the *end* of the step, so a Pokémon that got knocked out
 *  reads as knocked out; `ended` is whatever is standing there now. They differ on a switch —
 *  chosen or forced — and the UI puts both on one line. */
export type Slot = {
  slot: number;
  started: BoardMon | null;
  ended: BoardMon | null;
  changed: boolean;
};

/** One decision point: the position the model was asked about, its answer, and what followed.
 *  The step is anchored before the action, so `before`/`wp_p1` are the position and the call,
 *  and `events`/`after`/`slots` are what happened. `final` marks the step the game ended on —
 *  that is still just a turn, and its `after` is the final board. */
export type EndgameStep = {
  point: number;
  kind: "preview" | "turn" | "switch";
  turn: number;
  wp_p1: number | null;
  before: { p1: BoardSide; p2: BoardSide };
  events: SimEvent[];
  after: { p1: BoardSide; p2: BoardSide };
  slots: { p1: Slot[]; p2: Slot[] };
  weather: string | null;
  terrain: string | null;
  final: boolean;
  /** The number on the far side of the step: the model's call at the next decision point, or —
   *  on the step the game ended on — what actually happened. */
  wp_after: number | null;
  /** True when `wp_after` is the settled result rather than a prediction. */
  outcome: boolean;
};

export type SheetMon = { species: string; item: string | null; ability: string | null; moves: string[] };

export type EndgameDetail = {
  replay: string; format: string; url: string;
  players: { p1: string; p2: string };
  rating: number | null;
  winner: "p1" | "p2" | null; turns: number; ended_by: string;
  version: string; wp_error: string | null;
  gates: Gates;
  sheets: { p1: SheetMon[]; p2: SheetMon[] };
  steps: EndgameStep[];
};


// --- a battle in progress ---------------------------------------------------------------------
//
// The journal is the battle: every tap is one `Entry` appended to a list, and the state is what
// the backend gets by replaying it. That is why there is no "edit" call — you undo back to the
// tap you want to change. It is also why the client keeps no battle state of its own: whatever
// `LiveView` says is the truth, and anything derived here could disagree with it.

export type Entry = { kind: string; [k: string]: unknown };

/** One answer to a question, and what picking it would prove.
 *
 *  `conditional` marks an ability that announces only *sometimes* here — Static is a 30% chance,
 *  Supreme Overlord needs a fallen ally. Picking it still pins the ability; what it means is that
 *  its *silence* proves nothing, so it is still on the list next time. */
export type Option = {
  label: string;
  effects: { kind: string; stat?: string; stages?: number; value?: string }[];
  ability: string | null;
  excludes: string[];
  item: string | null;
  consumed: boolean;
  conditional: boolean;
};

/** Something the app cannot work out on its own. `forced` means there is one possible answer and
 *  it is asked anyway, because *when* it happened is evidence — a switch-in ability announces in
 *  Speed order. The UI renders those as one button, not a menu of one. */
export type Question = {
  id: string;
  kind: "switch_in" | "stat_drop" | "on_hit" | "terrain";
  prompt: string;
  side: "p1" | "p2";
  species: string;
  slot: number | null;
  source: { side: string; slot: number | null; species: string } | null;
  forced: boolean;
  options: Option[];
};

export type LiveMon = {
  species: string; forme: string;
  state: "unrevealed" | "active" | "bench" | "fainted" | "not_brought";
  slot: number | null;
  hp: number; hp_max: number | null; hp_exact: number | null;
  status: string | null;
  boosts: Record<string, number>;
  volatiles: string[];
  mega: boolean;
  item: string | null; item_source: string | null; lost_item: string | null;
  ability: string | null; ability_source: string | null;
  moves: string[]; moves_used: string[];
  nature: string | null; stats: Record<string, number> | null;
  ability_unknown: string[];
};

export type MoveOption = { id: string; name: string; target: string | null; category: string | null };

export type SideMenu = {
  actives: ({ slot: number; species: string; moves: MoveOption[]; moves_known: boolean } | null)[];
  bench: { species: string; hp: number; state: string }[];
};

/** Whether you move first, as a bound rather than a guess. `undecided` is reported as often as it
 *  is true: their range spans both the investment and the nature, because an open sheet hides the
 *  spread as surely as a closed one does. */
export type SpeedRead = {
  mine: string; my_slot: number; theirs: string; their_slot: number;
  verdict: "faster" | "slower" | "undecided" | "unknown";
  trick_room: boolean;
  my_speed: [number, number] | null;
  their_speed: [number, number] | null;
};

export type SPBelief = {
  side: string; species: string; nature: string | null;
  bounds: Record<string, [number, number]>;
  allocations: number; narrowed: number;
  dead: Record<string, string>; sources: Record<string, string>;
  speed_used: number; damage_used: number; bulk_used: number;
  contradicted: string | null;
};

/** `wp` is an average over `k` complete opponents drawn from the belief — each draw is a
 *  fully-known position, which is the only kind any model was trained on — and `lo`/`hi` are the
 *  10th and 90th percentile of those draws, so the width is what their hidden sets are worth
 *  here. `wp_open` is the true position with the unknowns left unknown: no model has seen one,
 *  so it is a diagnostic rather than an answer. */
export type LiveWP = {
  version?: string;
  wp?: number; lo?: number; hi?: number; k?: number;
  wp_open?: number;
  kind?: string;
  /** What is still open about each of their six, and how concentrated the belief is. */
  belief?: {
    species: string; sets: number; off_meta: boolean;
    concentration: number; evidence: string[];
  }[];
  regime?: string;
  error?: string;
};

export type LiveView = {
  id: string; name: string;
  turn: number; started: boolean; ended: boolean; winner: string | null;
  entries: number;
  journal: Entry[];
  sides: Record<"p1" | "p2", { mons: LiveMon[]; conditions: Record<string, number>; sheet: boolean }>;
  field: { weather: string | null; terrain: string | null; pseudo: Record<string, number> };
  menu: Record<"p1" | "p2", SideMenu>;
  questions: Question[];
  derived: string[];
  errors: string[];
  speed: SpeedRead[];
  beliefs: SPBelief[];
  /** Evidence that cannot all be true. Not a discovery about the opponent — they did have some
   *  spread — so it means something logged here is wrong, and the fix is a tap. */
  contradictions: { species: string; channel: string; note: string }[];
  wp?: LiveWP;
  /** Only on `/at/<index>`: which prefix this is, out of how many taps. */
  at?: number;
  entries_total?: number;
};

export type BattleRow = {
  id: string; name: string; created: string; updated: string;
  turn: number; entries: number; theirs: string[]; result: string | null;
};

export type TrajectoryRow = {
  index: number; turn: number; wp: number; lo: number; hi: number; draws: number;
  left: Record<"p1" | "p2", number>;
  active: Record<"p1" | "p2", string[]>;
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
  endgames: (reg: string, only: "all" | "played_out" | "misses" = "all") =>
    call<EndgameIndex>(`/api/endgames?regulation=${reg}&only=${only}`),
  endgame: (replay: string, reg: string) =>
    call<EndgameDetail>(`/api/endgames/${encodeURIComponent(replay)}?regulation=${reg}`),
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
  battles: (reg: string) => call<{ battles: BattleRow[] }>(`/api/battles?regulation=${reg}`),
  newBattle: (body: {
    name?: string; my_team?: string; team_id?: string;
    their_species?: string[]; their_team?: string; regulation: string;
  }) => call<LiveView>("/api/battles", { method: "POST", body: JSON.stringify(body) }),
  battle: (id: string, reg: string) => call<LiveView>(`/api/battles/${id}?regulation=${reg}`),
  /** Several entries in one call when they belong together — a spread move and both its damage
   *  numbers — so the screen never renders the half-applied state in between. */
  log: (id: string, entries: Entry[], reg: string) =>
    call<LiveView>(`/api/battles/${id}/entries`, {
      method: "POST", body: JSON.stringify({ entries, regulation: reg }),
    }),
  undo: (id: string, reg: string, count = 1) =>
    call<LiveView>(`/api/battles/${id}/undo?regulation=${reg}&count=${count}`, { method: "POST" }),
  battleAt: (id: string, index: number, reg: string) =>
    call<LiveView>(`/api/battles/${id}/at/${index}?regulation=${reg}`),
  trajectory: (id: string, reg: string) =>
    call<{ version: string; gates: Gates; turns: TrajectoryRow[] }>(
      `/api/battles/${id}/trajectory?regulation=${reg}`),
  deleteBattle: (id: string, reg: string) =>
    call<{ deleted: string }>(`/api/battles/${id}?regulation=${reg}`, { method: "DELETE" }),
};
