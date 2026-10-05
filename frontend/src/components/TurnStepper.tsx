import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { api, sideNames, type Entry, type LiveView, type MoveOption, type Question } from "../api";
import { across, screenOrder, type Pos, type Side } from "../screen";
import {
  CANT_REASONS, actedThisTurn, active, benchFor, loser, movedThisTurn, posKey, replacements, residuals,
  toId, turnLines, type Residual,
} from "../turn";
import { FieldPad } from "./EntryBar";

/** Moves aimed at one chosen Pokémon. `randomNormal` (Outrage) is here too: nobody chose its
 *  target, but somebody has to say whose bar drained. */
const AIMED = new Set(["normal", "adjacentFoe", "any", "randomNormal"]);
const AT_ALLY = new Set(["adjacentAlly", "adjacentAllyOrSelf"]);
const SPREADS = new Set(["allAdjacentFoes", "allAdjacent"]);

const HP_STEPS = [95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5];
const STATUSES: [string, string][] = [
  ["brn", "Burned"], ["par", "Paralysed"], ["psn", "Poisoned"], ["tox", "Badly poisoned"],
  ["slp", "Asleep"], ["frz", "Frozen"],
];
const STATS: [string, string][] = [["atk", "Atk"], ["def", "Def"], ["spa", "SpA"], ["spd", "SpD"], ["spe", "Spe"]];

type MoveChoice = Pick<MoveOption, "id" | "name" | "target" | "category">;

/** One answer on screen. Options are numbered left to right for the keyboard, in this order. */
type Opt = { label: ReactNode; pick: () => void; tone?: "ghost" | "danger" | "primary"; pressed?: boolean; title?: string };

/** One question. Every stage of a turn is this shape, so the question area looks and behaves the
 *  same from stage to stage, and the keyboard reads it the same way. */
type Stage = {
  /** Changes when the question does: it resets the HP field. */
  id: string;
  prompt: ReactNode;
  options: Opt[];
  /** What ↵ accepts. Left out wherever the answer is evidence: "Who moved?" has no default,
   *  because a default there would record an order nobody saw. */
  dflt?: number;
  body?: ReactNode;
  /** Shown between the question and its answers: the ability questions waiting beside an arrival. */
  pre?: ReactNode;
  /** Letter shortcuts on this stage. */
  keys?: Record<string, () => void>;
  /** An HP stage: typed digits go into its number field, and ↵ logs them. */
  hp?: { submit: (v: number) => void; prefill?: string; empty?: () => void };
  back?: () => void;
  /** Clicking a Pokémon on the field answers the question. */
  field?: (p: Pos) => void;
  /** Pokémon arriving together: leads, start-of-turn switches, replacements. Their ability
   *  questions wait beside the stage instead of in front of it, so every arrival is entered
   *  before any is answered, and the answers go in the order they were seen. */
  arrival?: boolean;
};

/** One Pokémon's action, between choosing who moved and logging it. Browser state on purpose:
 *  nothing is written until the results are in, so the move and its damage land in one call and
 *  the page never shows the half-applied state in between. */
type Act = {
  pos: Pos;
  species: string;
  move: MoveChoice | null;
  cant: boolean;
  /** Typed moves have no target type to go by, so the target stage offers every shape. */
  aiming: boolean;
  targets: Pos[] | null;
  spread: boolean;
  /** One per target done: the entry it logs, or null for a miss, a Protect, no effect. */
  results: (Entry | null)[];
  crit: boolean;
};

type Extra = "status" | "boost" | "consume" | "switch" | "faint" | "hp" | "field";

type Sub =
  | { kind: "switch-out"; pos: Pos }
  | { kind: "mega"; pos: Pos }
  | { kind: "extra"; what: Extra; pos: Pos | null }
  | { kind: "residual"; r: Residual };

/** What the browser remembers about the turn in front of it. Everything else is in the journal. */
type TurnUi = { turn: number; startDone: boolean; eot: boolean; eotYes: boolean; eotDone: string[]; after: boolean };
const freshTurn = (turn: number): TurnUi => ({ turn, startDone: false, eot: false, eotYes: false, eotDone: [], after: false });

function readFast(id: string): boolean {
  try { return localStorage.getItem(`vgc.fast.${id}`) === "1"; } catch { return false; }
}
function writeFast(id: string, on: boolean) {
  try { localStorage.setItem(`vgc.fast.${id}`, on ? "1" : "0"); } catch { /* private window */ }
}

const other = (s: Side): Side => (s === "p1" ? "p2" : "p1");

/** The Turn Stepper (docs/battle-entry-flows.md).
 *
 *  The cartridge narrates a turn as a fixed sequence of sentences. This asks for the next one,
 *  offers the possible answers as large buttons directly under the question, and skips any
 *  question with one possible answer. It writes the same entries the entry bar writes, and works
 *  out where the turn has got to from the journal, so it keeps nothing the backend does not have
 *  beyond the half-finished action in front of you.
 */
export function TurnStepper({ id, reg, view, busy, onLog, onUndo, onWalk, onSomethingElse, fieldPick, preview }: {
  id: string;
  reg: string;
  view: LiveView;
  busy: boolean;
  onLog: (entries: Entry[]) => Promise<void> | void;
  onUndo: () => void;
  onWalk: (index: number) => void;
  onSomethingElse: () => void;
  fieldPick: MutableRefObject<((p: Pos) => void) | null>;
  preview: ReactNode;
}) {
  const names = sideNames(view.perspective);
  const own: Side | null = view.perspective === "spectator" ? null : "p1";
  const [ui, setUi] = useState<TurnUi>(() => freshTurn(view.turn));
  const t = ui.turn === view.turn ? ui : freshTurn(view.turn);
  const upd = (patch: Partial<TurnUi>) => setUi({ ...t, ...patch, turn: view.turn });
  const [act, setAct] = useState<Act | null>(null);
  const [sub, setSub] = useState<Sub | null>(null);
  const [fast, setFast] = useState(() => readFast(id));
  const [help, setHelp] = useState(false);
  const [hpText, setHpText] = useState("");
  const [typed, setTyped] = useState("");
  // Null until the pool answers. The end-of-turn list waits for it, because a row that drops
  // out when the types arrive would move every button after it under the pointer.
  const [types, setTypes] = useState<Map<string, string[]> | null>(null);

  useEffect(() => { setFast(readFast(id)); }, [id]);
  useEffect(() => {
    api.pool(reg).then((p) => setTypes(new Map(p.species.map((s) => [toId(s.name), s.types]))))
      .catch(() => setTypes(new Map()));
  }, [reg]);
  // A new turn starts with nothing half-entered.
  useEffect(() => { setAct(null); setSub(null); }, [view.turn]);

  const toggleFast = useCallback(() => {
    setFast((f) => { writeFast(id, !f); return !f; });
  }, [id]);

  const log = async (entries: Entry[]) => { await onLog(entries); };

  // --- what has happened this turn ---------------------------------------------------------
  const journal = view.journal;
  const acted = useMemo(() => actedThisTurn(journal), [journal]);
  const moved = useMemo(() => movedThisTurn(journal), [journal]);
  const lines = useMemo(() => turnLines(view, names), [view, names]);
  const actives = screenOrder().filter((p) => view.menu[p.side].actives[p.slot]);
  const hasActed = (p: Pos) => acted.has(`${p.side}:${toId(view.menu[p.side].actives[p.slot]!.species)}`);
  const toAct = actives.filter((p) => !hasActed(p));
  const mon = (p: Pos) => active(view, p);
  const nameAt = (p: Pos) => mon(p)?.forme ?? view.menu[p.side].actives[p.slot]?.species ?? "—";
  const where = (p: Pos) => `${names[p.side]} · ${across(p.side, p.slot)}`;
  const monButton = (p: Pos) => <>{nameAt(p)} <span className="opt-sub">{where(p)}</span></>;

  // --- logging an action -------------------------------------------------------------------
  const commit = async (a: Act, results: (Entry | null)[]) => {
    const single = !a.spread && a.targets?.length === 1 ? a.targets[0] : null;
    const entries: Entry[] = [{
      kind: "move", side: a.pos.side, slot: a.pos.slot, move: a.move!.id,
      target: single ? { side: single.side, slot: single.slot } : null, spread: a.spread,
    }, ...results.filter((r): r is Entry => r !== null)];
    setAct(null);
    await log(entries);
    upd({ after: !fast });
  };

  const foesOf = (p: Pos) => actives.filter((q) => q.side === other(p.side));
  const allyOf = (p: Pos) => actives.filter((q) => q.side === p.side && q.slot !== p.slot);

  const chooseMove = (a: Act, m: MoveChoice) => {
    const foes = foesOf(a.pos);
    let targets: Pos[] | null = null;
    let spread = false;
    let aiming = false;
    if (m.target === null) {
      aiming = true;                                   // typed: ask what shape it had
    } else if (AIMED.has(m.target)) {
      targets = foes.length <= 1 ? foes : null;
    } else if (AT_ALLY.has(m.target)) {
      const ally = allyOf(a.pos);
      targets = ally.length <= 1 ? ally : null;
    } else if (SPREADS.has(m.target)) {
      spread = true;
      targets = m.target === "allAdjacent" ? [...foes, ...allyOf(a.pos)] : foes;
    } else {
      targets = [];
    }
    const next: Act = { ...a, move: m, targets, spread, aiming, results: [] };
    if (targets !== null && (m.category === "Status" || targets.length === 0)) return commit(next, []);
    setAct(next);
  };

  const chooseTargets = (a: Act, targets: Pos[], spread: boolean) => {
    const next = { ...a, targets, spread, aiming: false, results: [] };
    if (a.move?.category === "Status" || targets.length === 0) return commit(next, []);
    setAct(next);
  };

  /** The damage entry for an HP read: a percentage for theirs, a real number for yours. */
  const hpEntry = (p: Pos, v: number, crit: boolean): Entry => {
    const m = mon(p)!;
    const mine = p.side === own && m.hp_max != null;
    if (v <= 0) return { kind: "damage", side: p.side, slot: p.slot, fainted: true, crit };
    const now = mine ? m.hp_exact ?? 0 : Math.round(m.hp * 100);
    const value = mine ? { hp: v } : { pct: Math.min(100, v) };
    return v > now
      ? { kind: "heal", side: p.side, slot: p.slot, ...value }
      : { kind: "damage", side: p.side, slot: p.slot, ...value, crit };
  };

  const record = (a: Act, r: Entry | null) => {
    const results = [...a.results, r];
    if (results.length >= (a.targets?.length ?? 0)) return commit(a, results);
    setAct({ ...a, results, crit: false });
  };

  // --- the stages --------------------------------------------------------------------------

  /** The HP question, shared by a move's result, an end-of-turn effect and "HP changed". */
  const hpStage = (p: Pos, opts: {
    id: string; lead?: ReactNode; submit: (v: number) => void; extra: Opt[]; prefill?: string;
    keys?: Record<string, () => void>; empty?: () => void; back?: () => void; dflt?: number;
  }): Stage => {
    const m = mon(p);
    const mine = p.side === own && m?.hp_max != null;
    const now = mine ? `${m!.hp_exact}/${m!.hp_max}` : `${Math.round((m?.hp ?? 0) * 100)}%`;
    return {
      id: opts.id,
      prompt: <>{opts.lead}{nameAt(p)} is on {now}. What is it on now?</>,
      options: opts.extra,
      dflt: opts.dflt,
      keys: opts.keys,
      back: opts.back,
      hp: { submit: opts.submit, prefill: opts.prefill, empty: opts.empty },
      body: (
        <div className="hp-entry">
          <form className="row" onSubmit={(e) => {
            e.preventDefault();
            const v = Number(hpText);
            if (hpText.trim() && Number.isFinite(v)) opts.submit(v);
            else opts.empty?.();
          }}>
            <input type="text" inputMode="numeric" data-hp="1" value={hpText} className="hp-field"
                   placeholder={mine ? `HP of ${m!.hp_max}` : "%"} aria-label={mine ? "HP left" : "percent left"}
                   onChange={(e) => setHpText(e.target.value.replace(/[^0-9]/g, ""))} />
            {/* Not disabled when empty: a form whose only submit button is disabled ignores ↵,
                and an empty ↵ is how Fast mode says "didn't catch it". */}
            <button disabled={busy}>Log</button>
          </form>
          {!mine && (
            <div className="pad hp-pad">
              {HP_STEPS.filter((x) => x < Math.round((m?.hp ?? 0) * 100)).map((x) => (
                <button key={x} className="ghost" disabled={busy} onClick={() => opts.submit(x)}>{x}%</button>
              ))}
            </div>
          )}
        </div>
      ),
    };
  };

  const pickMon = (prompt: ReactNode, pick: (p: Pos) => void, back: () => void, from = actives): Stage => ({
    id: `pick:${String(prompt)}`,
    prompt,
    options: from.map((p) => ({ label: monButton(p), pick: () => pick(p) })),
    back,
    field: (p) => { if (from.some((q) => posKey(q) === posKey(p))) pick(p); },
  });

  function actStage(a: Act, auto: boolean): Stage {
    const back = () => {
      if (a.results.length) return setAct({ ...a, results: a.results.slice(0, -1) });
      if (a.targets && a.move && !auto) return setAct({ ...a, targets: null, aiming: a.move.target === null });
      if (a.move || a.cant) return setAct({ ...a, move: null, cant: false, targets: null, aiming: false });
      setAct(null);
    };
    const eot: Opt = { label: "End of turn →", tone: "ghost", pick: () => { setAct(null); upd({ eot: true }); } };

    if (a.cant) {
      return {
        id: `cant:${posKey(a.pos)}`,
        prompt: <>Why couldn't {a.species} move?</>,
        options: CANT_REASONS.map((r) => ({
          label: r[0].toUpperCase() + r.slice(1),
          pick: async () => {
            setAct(null);
            await log([{ kind: "note", text: `${a.species} couldn't move (${r})`,
                         cant: { side: a.pos.side, slot: a.pos.slot, reason: r } }]);
            upd({ after: !fast });
          },
        })),
        back,
      };
    }

    if (!a.move) {
      const menu = view.menu[a.pos.side].actives[a.pos.slot];
      const theirs = a.pos.side !== own;
      return {
        id: `move:${posKey(a.pos)}`,
        prompt: auto ? <>{a.species} is the last to act. Which move?</> : <>Which move did {a.species} use?</>,
        options: [
          ...(menu?.moves ?? []).map((m) => ({ label: m.name, pick: () => chooseMove(a, m) })),
          { label: "Couldn't move", tone: "ghost" as const, pick: () => setAct({ ...a, cant: true }) },
          ...(auto ? [eot] : []),
        ],
        back: auto ? undefined : back,
        body: (theirs || !menu?.moves.length) ? (
          <form className="row typed-move" onSubmit={(e) => {
            e.preventDefault();
            if (typed.trim()) { chooseMove(a, { id: typed.trim(), name: typed.trim(), target: null, category: null }); setTyped(""); }
          }}>
            <input type="text" value={typed} placeholder={menu?.moves_known ? "…or type a move" : "Type the move: nothing of theirs is known yet"}
                   onChange={(e) => setTyped(e.target.value)} />
            <button className="ghost" disabled={busy || !typed.trim()}>Use</button>
          </form>
        ) : undefined,
      };
    }

    if (a.targets === null) {
      const foes = foesOf(a.pos);
      const ally = allyOf(a.pos);
      const aimAt = a.move.target && AT_ALLY.has(a.move.target) ? ally : foes;
      // Screen order, whoever is on which side: left is left for every choice of target.
      const anyone = actives.filter((q) => posKey(q) !== posKey(a.pos));
      const options: Opt[] = (a.aiming ? anyone : aimAt).map((p) => ({
        label: monButton(p), pick: () => chooseTargets(a, [p], false),
      }));
      if (a.aiming) {
        options.push({ label: "Both foes (spread)", tone: "ghost", pick: () => chooseTargets(a, foes, true) });
        options.push({ label: "No target", tone: "ghost", pick: () => chooseTargets(a, [], false) });
      }
      return {
        id: `target:${posKey(a.pos)}`,
        prompt: <>{a.species} used {a.move.name}. At which one?</>,
        options, back,
        field: (p) => { if ((a.aiming ? anyone : aimAt).some((q) => posKey(q) === posKey(p))) chooseTargets(a, [p], false); },
      };
    }

    // The result, once per Pokémon hit.
    const i = a.results.length;
    const p = a.targets[i];
    const n = a.targets.length;
    const none = () => record(a, null);
    const ko = () => record(a, hpEntry(p, 0, a.crit));
    const extra: Opt[] = [
      { label: "KO", tone: "danger", pick: ko },
      { label: "Missed", tone: "ghost", pick: none },
      { label: "Protected", tone: "ghost", pick: none },
      { label: "No effect", tone: "ghost", pick: none },
      { label: "Crit", tone: "ghost", pressed: a.crit, pick: () => setAct({ ...a, crit: !a.crit }) },
      ...(fast ? [{ label: "Didn't catch it", tone: "ghost" as const, pick: none }] : []),
    ];
    return hpStage(p, {
      id: `result:${posKey(a.pos)}:${i}`,
      lead: <>{a.species} used {a.move.name}{n > 1 ? ` (${i + 1} of ${n})` : ""}. </>,
      submit: (v) => record(a, hpEntry(p, v, a.crit)),
      empty: fast ? none : undefined,
      extra,
      keys: { k: ko, m: none, p: none, n: none, c: () => setAct({ ...a, crit: !a.crit }) },
      back,
    });
  }

  function extraStage(s: Extract<Sub, { kind: "extra" }>): Stage {
    const done = () => setSub(null);
    const logAndClose = async (e: Entry[]) => { setSub(null); await log(e); };
    if (s.what === "field") {
      return {
        id: "extra:field", prompt: "Weather, terrain or screens: what changed?",
        options: [{ label: "Done", pick: done }], dflt: 0, back: done,
        body: <FieldPad view={view} onLog={(e) => void log(e)} busy={busy} />,
      };
    }
    if (!s.pos) {
      const from = s.what === "switch" ? actives.filter((p) => benchFor(view, p.side).length > 0) : actives;
      const verb = { status: "had its status change", boost: "had a stat change", consume: "used up an item",
                     switch: "switched out", faint: "fainted", hp: "had its HP change" }[s.what];
      return pickMon(`Which Pokémon ${verb}?`, (p) => setSub({ ...s, pos: p }), done, from);
    }
    const p = s.pos;
    const m = mon(p)!;
    const back = () => setSub({ ...s, pos: null });
    switch (s.what) {
      case "status":
        return {
          id: `extra:status:${posKey(p)}`, prompt: <>What is {m.forme} now?</>, back,
          options: [
            ...STATUSES.map(([k, label]) => ({ label, pick: () => logAndClose([{ kind: "status", side: p.side, slot: p.slot, status: k }]) })),
            { label: "Cured", tone: "ghost" as const, pick: () => logAndClose([{ kind: "status", side: p.side, slot: p.slot, status: null }]) },
          ],
        };
      case "boost":
        return {
          id: `extra:boost:${posKey(p)}`, prompt: <>Which of {m.forme}'s stats changed? Click each change as it happened.</>,
          options: [{ label: "Done", pick: done }], dflt: 0, back,
          body: (
            <div className="boost-grid">
              {STATS.map(([k, label]) => (
                <div key={k} className="row">
                  <span className="stepper-label">{label}</span>
                  {[-2, -1, 1, 2].map((n) => (
                    <button key={n} className="ghost" disabled={busy}
                            onClick={() => void log([{ kind: "boost", side: p.side, slot: p.slot, stat: k, stages: n }])}>
                      {n > 0 ? `+${n}` : n}
                    </button>
                  ))}
                  {m.boosts[k] ? <span className="tiny dim">now {m.boosts[k] > 0 ? "+" : ""}{m.boosts[k]}</span> : null}
                </div>
              ))}
            </div>
          ),
        };
      case "consume": {
        const known = m.item && m.item !== "" ? [m.item] : [];
        return {
          id: `extra:consume:${posKey(p)}`, prompt: <>Which item did {m.forme} use up?</>, back,
          options: known.map((it) => ({ label: it, pick: () => logAndClose([{ kind: "consume", side: p.side, species: m.species, item: it }]) })),
          body: (
            <form className="row typed-move" onSubmit={(e) => {
              e.preventDefault();
              if (typed.trim()) { void logAndClose([{ kind: "consume", side: p.side, species: m.species, item: typed.trim() }]); setTyped(""); }
            }}>
              <input type="text" value={typed} placeholder="Type the item (Sitrus Berry, Focus Sash…)" onChange={(e) => setTyped(e.target.value)} />
              <button className="ghost" disabled={busy || !typed.trim()}>Log</button>
            </form>
          ),
        };
      }
      case "switch":
        return {
          id: `extra:switch:${posKey(p)}`, prompt: <>Who came in for {m.forme}?</>, back,
          options: benchFor(view, p.side).map((b) => ({
            label: <>{b.species} <span className="opt-sub">{Math.round(b.hp * 100)}%</span></>,
            pick: () => logAndClose([{ kind: "switch", side: p.side, slot: p.slot, species: b.species }]),
          })),
        };
      case "faint":
        return {
          id: `extra:faint:${posKey(p)}`, prompt: <>{m.forme} fainted?</>, back, dflt: 0,
          options: [{ label: "Fainted", tone: "danger", pick: () => logAndClose([{ kind: "faint", side: p.side, slot: p.slot }]) }],
        };
      case "hp":
        return hpStage(p, {
          id: `extra:hp:${posKey(p)}`, back,
          submit: (v) => void logAndClose([hpEntry(p, v, false)]),
          extra: [{ label: "KO", tone: "danger", pick: () => void logAndClose([hpEntry(p, 0, false)]) }],
          keys: { k: () => void logAndClose([hpEntry(p, 0, false)]) },
        });
    }
  }

  const anythingElse = (): Stage => {
    const open = (what: Extra) => () => setSub({ kind: "extra", what, pos: null });
    const nothing = () => upd({ after: false });
    return {
      id: "after",
      prompt: "Anything else?",
      options: [
        { label: "Nothing else", pick: nothing },
        { label: "Status…", tone: "ghost", pick: open("status") },
        { label: "Stat change…", tone: "ghost", pick: open("boost") },
        { label: "HP changed…", tone: "ghost", pick: open("hp") },
        { label: "Item used up…", tone: "ghost", pick: open("consume") },
        { label: "Switched out…", tone: "ghost", pick: open("switch") },
        { label: "Fainted…", tone: "ghost", pick: open("faint") },
        { label: "Weather · terrain · screens…", tone: "ghost", pick: open("field") },
      ],
      dflt: 0,
      keys: { ".": nothing },
    };
  };

  // The label rides along so "turn so far" can say what was answered; replay reads only `option`.
  const answer = (q: Question, option: number | null) =>
    void log([{ kind: "answer", question: q.id, option, species: q.species,
                label: option === null ? null : q.options[option].label }]);
  const optionsOf = (x: Question): Opt[] => [
    ...x.options.map((o, i) => ({
      label: <>{o.label}{o.conditional && <span className="q-maybe" title="announces only sometimes, so not picking it proves nothing">?</span>}</>,
      tone: o.label.startsWith("nothing announced") ? "ghost" as const : undefined,
      title: o.ability ? `proves its ability is ${o.ability}` : undefined,
      pick: () => answer(x, i),
    })),
    { label: "Not sure", tone: "ghost", pick: () => answer(x, null) },
  ];
  const promptOf = (x: Question) => <>{x.prompt}{x.source && <span className="dim"> · from {x.source.species}</span>}</>;

  /** Every pending question as an equal card. None is first: the order you answer them in is the
   *  order the app records, so the page does not suggest one. */
  const questionCards = (qs: Question[]) => (
    <div className="q-rest">
      {qs.map((x) => (
        <div key={x.id} className="q-card">
          <div className="q-prompt">{promptOf(x)}</div>
          <div className="q-options">
            {optionsOf(x).map((o, i) => (
              <button key={i} className={o.tone ?? ""} title={o.title} disabled={busy} onClick={o.pick}>{o.label}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );

  function questionStage(qs: Question[]): Stage {
    if (qs.length === 1) {
      const q = qs[0];
      return { id: `q:${q.id}`, prompt: promptOf(q), options: optionsOf(q), dflt: q.forced ? 0 : undefined };
    }
    return {
      id: `q:${qs.map((q) => q.id).join(",")}`,
      prompt: "What announced? Answer in the order you saw them: that order is what tells the app who is faster.",
      options: [],
      body: questionCards(qs),
    };
  }

  function stage(): Stage {
    if (view.ended) {
      return { id: "ended", prompt: <>The game is over{view.winner ? `: ${names[view.winner as Side]} won` : ""}.</>, options: [] };
    }
    const base = baseStage();
    const qs = view.questions;
    if (!qs.length) return base;
    if (base.arrival) return { ...base, pre: questionCards(qs) };
    return questionStage(qs);
  }

  function baseStage(): Stage {
    if (!view.started) {
      const ready = (["p1", "p2"] as Side[]).every((s) => view.menu[s].actives.some(Boolean));
      return {
        id: "preview",
        prompt: ready ? "Leads are in." : "Team preview: who led?",
        options: ready ? [{ label: "Start turn 1 →", pick: () => void log([{ kind: "turn", n: 1 }]) }] : [],
        dflt: ready ? 0 : undefined,
        body: <div className="stage-preview">{preview}</div>,
        arrival: true,
      };
    }

    if (sub?.kind === "extra") return extraStage(sub);
    if (act) return actStage(act, false);
    if (t.after) return anythingElse();

    const lost = loser(view);
    if (lost) {
      const winner = other(lost);
      return {
        id: "over", prompt: <>{names[lost] === "You" ? "You have" : `${names[lost]} has`} nothing left. Log the result?</>,
        options: [{ label: `${names[winner]} won`, pick: () => void log([{ kind: "end", winner }]) }], dflt: 0,
      };
    }

    // 1. Start of turn: switches, then Mega Evolution, in the order the game shows them.
    if (!moved && !t.startDone && !t.eot) {
      if (sub?.kind === "switch-out") {
        const p = sub.pos;
        return {
          id: `switch-out:${posKey(p)}`, prompt: <>Who came in for {nameAt(p)}?</>, back: () => setSub(null),
          options: benchFor(view, p.side).map((b) => ({
            label: <>{b.species} <span className="opt-sub">{Math.round(b.hp * 100)}%</span></>,
            pick: async () => { setSub(null); await log([{ kind: "switch", side: p.side, slot: p.slot, species: b.species }]); },
          })),
          arrival: true,
        };
      }
      if (sub?.kind === "mega") {
        const p = sub.pos;
        const menu = view.menu[p.side].actives[p.slot]!;
        return {
          id: `mega:${posKey(p)}`, prompt: <>Which Mega did {menu.species} become?</>, back: () => setSub(null),
          options: menu.megas.map((x) => ({
            label: <>{x.forme} <span className="opt-sub">{x.item}</span></>,
            pick: async () => { setSub(null); await log([{ kind: "mega", side: p.side, species: menu.species, forme: x.forme, item: x.item }]); },
          })),
        };
      }
      const start = () => upd({ startDone: true });
      const options: Opt[] = [{ label: "Nothing: on to the moves", pick: start }];
      for (const p of actives.filter((q) => !hasActed(q) && benchFor(view, q.side).length > 0)) {
        options.push({ label: <>{nameAt(p)} switched out <span className="opt-sub">{where(p)}</span></>, tone: "ghost",
                       pick: () => setSub({ kind: "switch-out", pos: p }) });
      }
      for (const p of actives) {
        const menu = view.menu[p.side].actives[p.slot]!;
        if (!menu.megas?.length) continue;
        options.push({
          label: <>{nameAt(p)} Mega Evolved <span className="opt-sub">{where(p)}</span></>, tone: "ghost",
          pick: menu.megas.length === 1
            ? () => void log([{ kind: "mega", side: p.side, species: menu.species, forme: menu.megas[0].forme, item: menu.megas[0].item }])
            : () => setSub({ kind: "mega", pos: p }),
        });
      }
      return { id: "start", prompt: <>Turn {view.turn}: did anyone switch or Mega Evolve first?
                 <span className="stage-hint">Log switches in the order you saw them: that order is a Speed reading.</span></>, options, dflt: 0,
               keys: { ".": start }, arrival: true };
    }

    // 2–6. Who moved, until everyone has or you say the turn is over.
    if (!t.eot && toAct.length > 0) {
      const begin = (p: Pos): Act => ({
        pos: p, species: nameAt(p), move: null, cant: false, aiming: false, targets: null, spread: false, results: [], crit: false,
      });
      if (toAct.length === 1) return actStage(begin(toAct[0]), true);
      // Screen order, always, and no default: the order you click in is the Speed reading.
      return {
        id: "who",
        prompt: "Who moved next?",
        options: [
          ...toAct.map((p) => ({ label: monButton(p), pick: () => setAct(begin(p)) })),
          { label: "End of turn →", tone: "ghost" as const, pick: () => upd({ eot: true }) },
        ],
        field: (p) => { if (toAct.some((q) => posKey(q) === posKey(p))) setAct(begin(p)); },
      };
    }

    // 7. End of turn: confirm each effect that happened, in the order you saw them.
    if (types === null) return { id: "eot-wait", prompt: "End of turn…", options: [] };
    const pending = residuals(view, own, types).filter((r) => !t.eotDone.includes(r.key));
    if (sub?.kind === "residual") {
      const r = sub.r;
      const finish = async (e: Entry[]) => { setSub(null); upd({ eotDone: [...t.eotDone, r.key] }); if (e.length) await log(e); };
      const expect = r.expect.hp ?? r.expect.pct ?? 0;
      const unit = r.expect.hp != null ? ` HP` : "%";
      return hpStage(r.pos, {
        id: `residual:${r.key}`,
        lead: <>{r.label}: </>,
        prefill: String(expect),
        submit: (v) => void finish([hpEntry(r.pos, v, false)]),
        extra: [
          { label: `As expected: ${expect}${unit}`, pick: () => void finish([hpEntry(r.pos, expect, false)]) },
          { label: "Didn't happen", tone: "ghost", pick: () => void finish([]) },
          { label: "KO", tone: "danger", pick: () => void finish([hpEntry(r.pos, 0, false)]) },
        ],
        dflt: 0,
        keys: { k: () => void finish([hpEntry(r.pos, 0, false)]) },
        back: () => setSub(null),
      });
    }
    if (pending.length > 0) {
      const none = () => upd({ eotDone: [...t.eotDone, ...pending.map((r) => r.key)] });
      if (fast && !t.eotYes) {
        return {
          id: "eot-gate", prompt: "Anything change at the end of the turn?",
          options: [{ label: "No", pick: none }, { label: "Yes", tone: "ghost", pick: () => upd({ eotYes: true }) }],
          dflt: 0, keys: { ".": none },
        };
      }
      return {
        id: "eot",
        prompt: "End of turn: what happened next?",
        options: [
          ...pending.map((r) => ({
            label: <>{r.label}: {r.species} <span className="opt-sub">→ about {r.expect.hp ?? r.expect.pct}{r.expect.hp != null ? " HP" : "%"}</span></>,
            pick: () => setSub({ kind: "residual", r }),
          })),
          { label: "None of the rest happened →", tone: "ghost" as const, pick: none },
        ],
        back: () => upd({ eot: false }),
      };
    }

    // 8. Replacements, one fainted slot at a time.
    const reps = replacements(view);
    if (reps.length > 0) {
      const { pos: p, was } = reps[0];
      return {
        id: `replace:${posKey(p)}`,
        prompt: <>Who came in {was ? <>for {was}</> : <>on {names[p.side]}'s {across(p.side, p.slot)}</>}?</>,
        options: benchFor(view, p.side).map((b) => ({
          label: <>{b.species} <span className="opt-sub">{Math.round(b.hp * 100)}%</span></>,
          pick: () => void log([{ kind: "switch", side: p.side, slot: p.slot, species: b.species }]),
        })),
        arrival: true,
      };
    }

    return {
      id: "next",
      prompt: `Turn ${view.turn} is done.`,
      options: [
        { label: `Start turn ${view.turn + 1} →`, pick: () => void log([{ kind: "turn", n: view.turn + 1 }]) },
        { label: "Anything else…", tone: "ghost", pick: () => upd({ after: true }) },
      ],
      dflt: 0,
    };
  }

  const s = stage();
  const stageRef = useRef(s);
  stageRef.current = s;
  fieldPick.current = s.field ?? null;

  // A new question gets a fresh HP field, and an HP question takes focus so digits land in it.
  const hpRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setHpText(stageRef.current.hp?.prefill ?? "");
    if (stageRef.current.hp) {
      requestAnimationFrame(() => hpRef.current?.querySelector<HTMLInputElement>("input[data-hp]")?.focus());
    }
  }, [s.id]);

  // --- the keyboard ---------------------------------------------------------------------------
  const helpRef = useRef(help);
  helpRef.current = help;
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const st = stageRef.current;
      const el = ev.target as HTMLElement | null;
      const inHp = el?.dataset?.hp === "1";
      const inText = !inHp && !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "z" && !ev.shiftKey) {
        if (inText) return;
        ev.preventDefault();
        onUndo();
        return;
      }
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        if (helpRef.current) setHelp(false);
        else onSomethingElse();
        return;
      }
      if (inText) return;
      if (ev.key === "Enter") {
        // A focused button answers Enter itself, and the HP field submits its own form.
        if (inHp || el?.tagName === "BUTTON") return;
        if (st.hp) {
          ev.preventDefault();
          const v = Number(hpText);
          if (hpText && Number.isFinite(v)) st.hp.submit(v);
          else if (st.dflt !== undefined) st.options[st.dflt]?.pick();
          else st.hp.empty?.();
          return;
        }
        if (st.dflt !== undefined) { ev.preventDefault(); st.options[st.dflt]?.pick(); }
        return;
      }
      if (ev.key === "Backspace") {
        if (inHp && hpText) return;
        if (st.back) { ev.preventDefault(); st.back(); }
        return;
      }
      if (/^[0-9]$/.test(ev.key)) {
        if (st.hp) {
          if (inHp) return;
          ev.preventDefault();
          setHpText((x) => x + ev.key);
          hpRef.current?.querySelector<HTMLInputElement>("input[data-hp]")?.focus();
          return;
        }
        const n = Number(ev.key);
        if (n >= 1 && st.options[n - 1]) { ev.preventDefault(); st.options[n - 1].pick(); }
        return;
      }
      if (ev.key === "?") { ev.preventDefault(); setHelp((h) => !h); return; }
      if (ev.key === "f") { ev.preventDefault(); toggleFast(); return; }
      const k = st.keys?.[ev.key];
      if (k) { ev.preventDefault(); k(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hpText, onUndo, onSomethingElse, toggleFast]);

  // --- the shortcuts popover: closed by a second click, a click outside it, ? or Esc ----------
  const popRef = useRef<HTMLDivElement>(null);
  const iconRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!help) return;
    const away = (e: MouseEvent) => {
      const n = e.target as Node;
      if (!popRef.current?.contains(n) && !iconRef.current?.contains(n)) setHelp(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [help]);

  const total = acted.size + toAct.length;
  const progress = !view.started ? "team preview"
    : `turn ${view.turn} · ${Math.min(acted.size, total)} of ${total} ${total === 1 ? "has" : "have"} moved`;

  return (
    <div className="panel turn-stepper">
      <div className="stepper-head">
        <b>{progress[0].toUpperCase() + progress.slice(1)}</b>
        <div className="row stepper-tools">
          {fast && view.started && !t.eot && toAct.length > 0 && (
            <button className="link" onClick={() => { setAct(null); setSub(null); upd({ eot: true, after: false }); }}>
              Skip to end of turn
            </button>
          )}
          <button className="seg" aria-pressed={fast} onClick={toggleFast} title="Fast mode: fewer questions when the stream is running away from you">
            Fast {fast ? "●" : "○"}
          </button>
          <button className="ghost small-btn" onClick={onUndo} disabled={busy || view.entries === 0}>Undo</button>
          <button ref={iconRef} className="icon-btn" aria-label="Keyboard shortcuts" title="Keyboard shortcuts"
                  aria-expanded={help} aria-controls="stepper-shortcuts" onClick={() => setHelp(!help)}>ⓘ</button>
        </div>
      </div>
      {help && <Shortcuts popRef={popRef} />}

      {lines.length > 0 && (
        <ol className="turn-lines">
          {lines.map((l) => (
            <li key={l.index}>
              <button className={`turn-line${l.error ? " bad" : ""}`} onClick={() => onWalk(l.index)}
                      title="Walk the battle back to just before this">
                <span className="turn-check">{l.error ? "!" : "✓"}</span> {l.text}
                {l.error && <span className="turn-err"> {l.error}</span>}
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="stage" ref={hpRef}>
        <div className="stage-prompt">{s.prompt}</div>
        {s.pre}
        {s.hp && s.body}
        <div className="stage-opts">
          {s.options.map((o, i) => (
            <button key={i} className={`stage-opt ${o.tone ?? ""}${s.dflt === i ? " is-default" : ""}`}
                    aria-pressed={o.pressed} title={o.title} disabled={busy} onClick={o.pick}>
              {help && i < 9 && <span className="key-badge">{i + 1}</span>}
              {o.label}
            </button>
          ))}
        </div>
        {!s.hp && s.body}
        <div className="stage-foot">
          {s.back && <button className="link" onClick={s.back}>← back</button>}
          <button className="link" onClick={onSomethingElse}>something else…</button>
        </div>
      </div>
    </div>
  );
}

function Shortcuts({ popRef }: { popRef: MutableRefObject<HTMLDivElement | null> }) {
  const rows: [string, string][] = [
    ["1–9", "Pick an option, numbered left to right and top to bottom"],
    ["↵", "Accept the highlighted default, if the stage has one"],
    ["digits, ↵", "On an HP question: 45% for theirs, 45 HP for yours"],
    ["k · m · p · n", "KO · Missed · Protected · No effect"],
    ["c", "Toggle crit"],
    [".", "Nothing else: go to the next stage"],
    ["Backspace", "Back one stage within this action. Logs nothing, undoes nothing"],
    ["⌘Z", "Undo the last entry"],
    ["Esc", "Something else: the free-form entry bar"],
    ["f", "Toggle Fast mode"],
    ["?", "Open or close this list"],
  ];
  return (
    <div className="help-pop" id="stepper-shortcuts" ref={popRef} role="dialog" aria-label="Keyboard shortcuts">
      <table>
        <tbody>
          {rows.map(([k, d]) => <tr key={k}><td><kbd>{k}</kbd></td><td>{d}</td></tr>)}
        </tbody>
      </table>
    </div>
  );
}

