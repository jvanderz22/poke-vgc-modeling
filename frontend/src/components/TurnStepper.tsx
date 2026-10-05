import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { api, sideNames, type EndOfTurn, type Entry, type LiveView, type MoveOption, type Question } from "../api";
import { across, screenOrder, type Pos, type Side } from "../screen";
import {
  CANT_REASONS, actedSet, active, benchFor, endOfTurn, loser, moveName, posKey, toId, turnLines, turnStart,
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

type MoveChoice = Pick<MoveOption, "id" | "name" | "target" | "category"> & Pick<MoveOption, "follows" | "chance">;

/** One answer on screen. Options are numbered left to right for the keyboard, in this order. */
/** One answer on screen. Its look says what a click does: `next` (the default) acts at once and
 *  moves on; `commit` logs what has been selected; `toggle` only switches something on or off,
 *  and is drawn as a checkbox. */
type Opt = {
  label: ReactNode; pick: () => void; title?: string;
  tone?: "ghost" | "danger" | "commit" | "toggle"; pressed?: boolean; disabled?: boolean;
};

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
  /** What ↵ does, where it is not picking an option: confirming a batch. */
  enter?: () => void;
  /** What a digit does, where the numbered things are not the options: picking in a card. */
  digit?: (n: number) => void;
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
  /** One per target done: the damage it logs, or how the move did no damage to it. */
  results: Outcome[];
  crit: boolean;
  /** Targets its chance effect happened to (a Scald burn, a Moonblast drop), by position. */
  chanced?: string[];
  /** The journal index of a move already logged (by the entry bar, say) whose results are owed. */
  logged?: number;
};

/** What a move did to one target: HP to log, or which no-damage it was (`hit` when it hit and
 *  the HP was not caught). */
type Outcome = { entry: Entry } | { result: "hit" | "miss" | "protected" | "immune" | "failed" };

type Extra = "status" | "boost" | "consume" | "switch" | "faint" | "hp" | "field";

type Sub =
  | { kind: "extra"; what: Extra; pos: Pos | null }
  | { kind: "residual"; r: EndOfTurn };

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

/** The label rides along so "turn so far" can say what was answered; replay reads only `option`. */
function answerEntry(q: Question, option: number | null): Entry {
  return { kind: "answer", question: q.id, option, species: q.species,
           label: option === null ? null : q.options[option].label };
}

function optClass(o: Opt, isDefault: boolean): string {
  const role = o.tone === "toggle" ? "choice check" : o.tone === "commit" ? "commit" : `next ${o.tone ?? ""}`;
  return `stage-opt ${role}${isDefault ? " is-default" : ""}`;
}

/** The Turn Stepper (docs/battle-entry-flows.md).
 *
 *  The cartridge narrates a turn as a fixed sequence of sentences. This asks for the next one,
 *  offers the possible answers as large buttons directly under the question, and skips any
 *  question with one possible answer. It writes the same entries the entry bar writes, and reads
 *  where the turn has got to off the view (`turn_progress`), so it keeps nothing the backend does
 *  not have beyond the half-finished action in front of you.
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
  // Targets of an already-logged move that were answered with no damage. The journal has nowhere
  // to put that after the fact, so they are only skipped here.
  const [skipped, setSkipped] = useState<string[]>([]);

  // Answers selected but not yet logged, in the order they were selected: that order is the order
  // the app records, so it is the order you saw them in. `openAfter` is what the backend says
  // would still be open once they are in, which is where their knock-on questions come from.
  const [staged, setStaged] = useState<{ q: Question; option: number | null }[]>([]);
  const [openAfter, setOpenAfter] = useState<Question[] | null>(null);
  // Anything else, and the start of the turn: entries selected, logged together on Confirm.
  const [pending, setPending] = useState<{ key: string; text: string; entries: Entry[] }[]>([]);

  useEffect(() => { setFast(readFast(id)); }, [id]);
  // Anything logged settles whatever was selected against the old journal.
  useEffect(() => { setStaged([]); setOpenAfter(null); setPending([]); }, [view.entries]);
  useEffect(() => {
    if (!staged.length) { setOpenAfter(null); return; }
    let live = true;
    api.previewEntries(id, staged.map((x) => answerEntry(x.q, x.option)), reg).then((r) => {
      if (!live) return;
      // An answer to a knock-on question whose cause was changed no longer has a question to answer.
      const bad = new Set(r.errors.map((e) => e.index));
      if (bad.size) setStaged((st) => st.filter((_, i) => !bad.has(i)));
      else setOpenAfter(r.questions);
    }).catch(() => {});
    return () => { live = false; };
  }, [staged, id, reg]);
  // A new turn starts with nothing half-entered.
  useEffect(() => { setAct(null); setSub(null); }, [view.turn]);

  const toggleFast = useCallback(() => {
    setFast((f) => { writeFast(id, !f); return !f; });
  }, [id]);

  const log = async (entries: Entry[]) => { await onLog(entries); };

  // --- what has happened this turn ---------------------------------------------------------
  const acted = useMemo(() => actedSet(view), [view]);
  const moved = view.turn_progress?.moved ?? false;
  const lines = useMemo(() => turnLines(view, names), [view, names]);
  const actives = screenOrder().filter((p) => view.menu[p.side].actives[p.slot]);
  const hasActed = (p: Pos) => acted.has(`${p.side}:${toId(view.menu[p.side].actives[p.slot]!.species)}`);
  const toAct = actives.filter((p) => !hasActed(p));
  const mon = (p: Pos) => active(view, p);
  const nameAt = (p: Pos) => mon(p)?.forme ?? view.menu[p.side].actives[p.slot]?.species ?? "—";
  const where = (p: Pos) => `${names[p.side]} · ${across(p.side, p.slot)}`;
  const monButton = (p: Pos) => <>{nameAt(p)} <span className="opt-sub">{where(p)}</span></>;

  // --- logging an action -------------------------------------------------------------------
  const commit = async (a: Act, results: Outcome[]) => {
    const targets = a.targets ?? [];
    const damage = results.flatMap((r) => ("entry" in r ? [r.entry] : []));
    const none = targets.flatMap((p, i) => (results[i] && "result" in results[i]
      ? [{ side: p.side, slot: p.slot, result: (results[i] as { result: string }).result }] : []));
    setAct(null);
    if (a.logged != null) {
      setSkipped((x) => [...x, ...none.map((r) => `${a.logged}:${posKey(r)}`)]);
      if (damage.length) await log(damage);
      upd({ after: !fast });
      return;
    }
    const single = !a.spread && targets.length === 1 ? targets[0] : null;
    // How the move did no damage: on the move itself for one target, one per target for a spread.
    const how = single ? (none.length ? { result: none[0].result } : {})
      : none.length ? { results: none } : {};
    await log([{
      kind: "move", side: a.pos.side, slot: a.pos.slot, move: a.move!.id,
      target: single ? { side: single.side, slot: single.slot } : null, spread: a.spread, ...how,
      ...(a.chanced?.length ? { chance: targets.filter((p) => a.chanced!.includes(posKey(p)))
                                                 .map((p) => ({ side: p.side, slot: p.slot })) } : {}),
    }, ...damage]);
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

  /** The entry for an HP read after a move: a percentage for theirs, a real number for yours. */
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

  /** The entry for HP that changed for any reason but a move: weather, Leftovers, recoil. It is a
   *  `heal` whichever way the HP went, because `heal` sets HP alone, where `damage` is put down to
   *  the last move used and would tell the damage belief that move hits harder than it does. */
  const hpSet = (p: Pos, v: number, eot?: string): Entry[] => {
    const m = mon(p)!;
    const mine = p.side === own && m.hp_max != null;
    if (v <= 0) return [{ kind: "faint", side: p.side, slot: p.slot }];
    return [{ kind: "heal", side: p.side, slot: p.slot, ...(mine ? { hp: v } : { pct: Math.min(100, v) }),
              ...(eot ? { eot } : {}) }];
  };

  const record = (a: Act, r: Outcome) => {
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
      if (a.logged != null) return;           // already in the journal: undo is the way back
      if (a.targets && a.move && !auto) return setAct({ ...a, targets: null, aiming: a.move.target === null });
      if (a.move || a.cant) return setAct({ ...a, move: null, cant: false, targets: null, aiming: false });
      setAct(null);
    };
    const eot: Opt = { label: "End of turn →", tone: "ghost", pick: () => { setAct(null); upd({ eot: true }); } };

    if (a.cant) {
      return {
        id: `cant:${posKey(a.pos)}`,
        prompt: <>Why couldn't {a.species} move?</>,
        options: CANT_REASONS.map(([reason, label]) => ({
          label,
          pick: async () => {
            setAct(null);
            await log([{ kind: "cant", side: a.pos.side, slot: a.pos.slot, reason }]);
            upd({ after: !fast });
          },
        })),
        back,
      };
    }

    if (!a.move) {
      const menu = view.menu[a.pos.side].actives[a.pos.slot];
      const known = menu?.moves ?? [];
      const likely = (menu?.likely ?? []).filter((m) => !known.some((k) => k.id === m.id));
      const legal = menu?.legal ?? [];
      // A typed move has to be one this Pokémon can have, matched however it was spelled, so its
      // target and category come with it and the target question is asked only when it should be.
      const match = (text: string) => {
        const want = toId(text);
        return legal.find((m) => m.id === want || toId(m.name) === want) ?? null;
      };
      const submitTyped = () => {
        const text = typed.trim();
        if (!text) return;
        const found = match(text);
        if (found) { chooseMove(a, found); setTyped(""); return; }
        if (!legal.length) { chooseMove(a, { id: text, name: text, target: null, category: null }); setTyped(""); }
      };
      const unknown = !!typed.trim() && legal.length > 0 && !match(typed);
      return {
        id: `move:${posKey(a.pos)}`,
        prompt: auto ? <>{a.species} is the last to act. Which move?</> : <>Which move did {a.species} use?</>,
        options: [
          ...known.map((m) => ({ label: m.name, pick: () => chooseMove(a, m) })),
          ...likely.map((m) => ({
            label: <>{m.name} <span className="opt-sub">{Math.round(m.share * 100)}% run it</span></>,
            tone: "ghost" as const, title: "not seen yet: how many of the sets still possible run it",
            pick: () => chooseMove(a, m),
          })),
          { label: "Couldn't move", tone: "ghost" as const, pick: () => setAct({ ...a, cant: true }) },
          ...(auto ? [eot] : []),
        ],
        back: auto ? undefined : back,
        body: (legal.length > 0 || !known.length) ? (
          <form className="row typed-move" onSubmit={(e) => { e.preventDefault(); submitTyped(); }}>
            <input type="text" value={typed} list={`legal-${posKey(a.pos)}`} autoComplete="off"
                   placeholder={legal.length ? `Any move ${a.species} can have…` : "Type the move"}
                   aria-invalid={unknown} onChange={(e) => setTyped(e.target.value)} />
            {legal.length > 0 && (
              <datalist id={`legal-${posKey(a.pos)}`}>
                {legal.map((m) => <option key={m.id} value={m.name} />)}
              </datalist>
            )}
            <button className="ghost" disabled={busy || !typed.trim() || unknown}>Use</button>
            {unknown && <span className="tiny err">Not a move {a.species} can have.</span>}
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
    const how = (result: "hit" | "miss" | "protected" | "immune" | "failed") => () => record(a, { result });
    const ko = () => record(a, { entry: hpEntry(p, 0, a.crit) });
    // The move's chance effect on this target, if it has one, as something to tick; what follows
    // on its own is said, so it is not entered again under "Anything else".
    const chances = a.logged == null ? a.move.chance ?? [] : [];
    const ticked = (a.chanced ?? []).includes(posKey(p));
    const tick = () => setAct({ ...a, chanced: ticked ? (a.chanced ?? []).filter((k) => k !== posKey(p))
                                                       : [...(a.chanced ?? []), posKey(p)] });
    const extra: Opt[] = [
      ...(chances.length ? [{ label: `${chances.map((c) => `${c.label} (${c.chance}%)`).join(" · ")}`,
                              tone: "toggle" as const, pressed: ticked, pick: tick,
                              title: "the move's added effect: tick it if it happened" }] : []),
      { label: "KO", tone: "danger", pick: ko },
      { label: "Missed", tone: "ghost", pick: how("miss") },
      { label: "Protected", tone: "ghost", pick: how("protected") },
      { label: "No effect", tone: "ghost", pick: how("immune") },
      { label: "Failed", tone: "ghost", pick: how("failed") },
      { label: "Crit", tone: "toggle", pressed: a.crit, pick: () => setAct({ ...a, crit: !a.crit }) },
      ...(fast ? [{ label: "Didn't catch it", tone: "ghost" as const, pick: how("hit") }] : []),
    ];
    const follows = a.move.follows ?? [];
    const st = hpStage(p, {
      id: `result:${posKey(a.pos)}:${i}`,
      lead: <>{a.species} used {a.move.name}{n > 1 ? ` (${i + 1} of ${n})` : ""}. </>,
      submit: (v) => record(a, { entry: hpEntry(p, v, a.crit) }),
      empty: fast ? how("hit") : undefined,
      extra,
      keys: { k: ko, m: how("miss"), p: how("protected"), n: how("immune"), c: () => setAct({ ...a, crit: !a.crit }) },
      back,
    });
    return follows.length && i === 0
      ? { ...st, pre: <div className="tiny dim follows">Follows on its own once it hits: {follows.join("; ")}.</div> }
      : st;
  }

  function extraStage(s: Extract<Sub, { kind: "extra" }>): Stage {
    const done = () => setSub(null);
    // Nothing here is logged yet: each detail joins the list under "Anything else?", and the list
    // is logged together when you confirm it.
    const add = (text: string, entries: Entry[]) =>
      setPending((ps) => [...ps, { key: `x${Date.now()}${ps.length}`, text, entries }]);
    const logAndClose = (e: Entry[], text: string) => { setSub(null); add(text, e); };
    if (s.what === "field") {
      return {
        id: "extra:field", prompt: "Weather, terrain or screens: what changed?",
        options: [{ label: "Done", pick: done }], dflt: 0, back: done,
        body: <FieldPad view={view} busy={busy} onLog={(e) => e.forEach((x) =>
          add(`${String(x.value ?? x.condition ?? x.what)} ${x.on === false ? "ended" : "started"}${x.side ? ` (${names[x.side as Side]})` : ""}`, [x]))} />,
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
            ...STATUSES.map(([k, label]) => ({ label, pick: () => logAndClose([{ kind: "status", side: p.side, slot: p.slot, status: k }], `${m.forme}: ${label.toLowerCase()}`) })),
            { label: "Cured", tone: "ghost" as const, pick: () => logAndClose([{ kind: "status", side: p.side, slot: p.slot, status: null }], `${m.forme}: cured`) },
          ],
        };
      case "boost":
        return {
          id: `extra:boost:${posKey(p)}`, prompt: <>Which of {m.forme}'s stats changed? Add each change as it happened.</>,
          options: [{ label: "Done", pick: done }], dflt: 0, back,
          body: (
            <div className="boost-grid">
              {STATS.map(([k, label]) => (
                <div key={k} className="row">
                  <span className="stepper-label">{label}</span>
                  {[-2, -1, 1, 2].map((n) => (
                    <button key={n} className="ghost" disabled={busy}
                            onClick={() => add(`${m.forme}: ${label} ${n > 0 ? "+" : ""}${n}`, [{ kind: "boost", side: p.side, slot: p.slot, stat: k, stages: n }])}>
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
          options: known.map((it) => ({ label: it, pick: () => logAndClose([{ kind: "consume", side: p.side, species: m.species, item: it }], `${m.forme} used up ${it}`) })),
          body: (
            <form className="row typed-move" onSubmit={(e) => {
              e.preventDefault();
              if (typed.trim()) { logAndClose([{ kind: "consume", side: p.side, species: m.species, item: typed.trim() }], `${m.forme} used up ${typed.trim()}`); setTyped(""); }
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
            pick: () => logAndClose([{ kind: "switch", side: p.side, slot: p.slot, species: b.species }], `${m.forme} switched to ${b.species}`),
          })),
        };
      case "faint":
        return {
          id: `extra:faint:${posKey(p)}`, prompt: <>{m.forme} fainted?</>, back, dflt: 0,
          options: [{ label: "Fainted", tone: "danger", pick: () => logAndClose([{ kind: "faint", side: p.side, slot: p.slot }], `${m.forme} fainted`) }],
        };
      case "hp":
        return hpStage(p, {
          id: `extra:hp:${posKey(p)}`, back,
          submit: (v) => logAndClose(hpSet(p, v), `${m.forme} on ${v}${p.side === own && m.hp_max != null ? " HP" : "%"}`),
          extra: [{ label: "KO", tone: "danger", pick: () => logAndClose(hpSet(p, 0), `${m.forme} fainted`) }],
          keys: { k: () => logAndClose(hpSet(p, 0), `${m.forme} fainted`) },
        });
    }
  }

  const anythingElse = (): Stage => {
    const open = (what: Extra) => () => setSub({ kind: "extra", what, pos: null });
    const confirm = async () => {
      const entries = pending.flatMap((x) => x.entries);
      upd({ after: false });
      if (entries.length) await log(entries);
    };
    return {
      id: "after",
      prompt: "Anything else?",
      pre: pending.length > 0 ? (
        <ol className="pending-list">
          {pending.map((x) => (
            <li key={x.key}>
              {x.text}
              <button className="link" aria-label={`remove ${x.text}`}
                      onClick={() => setPending((ps) => ps.filter((y) => y.key !== x.key))}>remove</button>
            </li>
          ))}
        </ol>
      ) : undefined,
      options: [
        { label: pending.length ? `Confirm ${pending.length}` : "Nothing else", tone: "commit", pick: () => void confirm() },
        { label: "Status…", tone: "ghost", pick: open("status") },
        { label: "Stat change…", tone: "ghost", pick: open("boost") },
        { label: "HP changed…", tone: "ghost", pick: open("hp") },
        { label: "Item used up…", tone: "ghost", pick: open("consume") },
        { label: "Switched out…", tone: "ghost", pick: open("switch") },
        { label: "Fainted…", tone: "ghost", pick: open("faint") },
        { label: "Weather · terrain · screens…", tone: "ghost", pick: open("field") },
      ],
      dflt: 0,
      keys: { ".": () => void confirm() },
    };
  };

  const promptOf = (x: Question) => <>{x.prompt}{x.source && <span className="dim"> · from {x.source.species}</span>}</>;

  /** Every question open now, or opened by an answer selected: the selected ones stay on screen with
   *  their answer, and the knock-on ones appear under them before anything is logged. */
  const shownQuestions = (): Question[] => {
    const all = new Map<string, Question>();
    for (const x of staged) all.set(x.q.id, x.q);
    for (const q of openAfter ?? view.questions) all.set(q.id, q);
    const key = (id: string) => id.slice(1).split(".").map(Number);
    return [...all.values()].sort((a, b) => {
      const [x, y] = [key(a.id), key(b.id)];
      return x[0] - y[0] || x[1] - y[1];
    });
  };
  const choose = (q: Question, option: number | null) => setStaged((st) => {
    const i = st.findIndex((x) => x.q.id === q.id);
    if (i < 0) return [...st, { q, option }];
    if (st[i].option === option) return st.filter((_, j) => j !== i);      // a second click clears it
    return st.map((x, j) => (j === i ? { q, option } : x));
  });
  const confirmAnswers = () => { if (staged.length) void log(staged.map((x) => answerEntry(x.q, x.option))); };

  const questionCards = () => {
    const qs = shownQuestions();
    return (
      <div className="q-batch">
        {qs.map((x) => {
          const at = staged.findIndex((s) => s.q.id === x.id);
          const picked = at >= 0 ? staged[at].option : undefined;
          const opts: [number | null, ReactNode, string | undefined][] = [
            ...x.options.map((o, i) => [i, <>{o.label}{o.conditional &&
              <span className="q-maybe" title="announces only sometimes, so not picking it proves nothing">?</span>}</>,
              o.ability ? `proves its ability is ${o.ability}` : undefined] as [number, ReactNode, string | undefined]),
            [null, "Not sure", "concludes nothing"],
          ];
          // A question raised by a selected answer rather than by the journal: its id names the
          // position that answer would take, so the card can say which one it follows from.
          const from = Number(x.id.slice(1).split(".")[0]) - view.entries;
          const cause = from >= 0 ? staged[from] : undefined;
          return (
            <div key={x.id} className={`q-card${at >= 0 ? " answered" : ""}${cause ? " knock-on" : ""}`}>
              {cause && (
                <div className="tiny dim">
                  follows from answer {from + 1}: {cause.option === null ? "not sure" : cause.q.options[cause.option].label.split(" — ")[0]}
                </div>
              )}
              <div className="q-prompt">
                {at >= 0 && <span className="order-badge" title="the order you selected answers in">{at + 1}</span>}
                {promptOf(x)}
              </div>
              <div className="q-options" role="radiogroup">
                {opts.map(([i, label, title]) => (
                  <button key={String(i)} className="choice radio" role="radio" aria-checked={picked === i}
                          title={title} disabled={busy} onClick={() => choose(x, i)}>{label}</button>
                ))}
              </div>
            </div>
          );
        })}
        <div className="row q-confirm">
          <button className="commit" disabled={busy || !staged.length} onClick={confirmAnswers}>
            {staged.length ? `Confirm ${staged.length} answer${staged.length === 1 ? "" : "s"}` : "Confirm"}
          </button>
          <span className="tiny dim">
            {qs.length > 1 ? "Select what you saw in the order you saw it: that order is what tells the app who is faster."
              : "Select what you saw, then confirm."}
          </span>
        </div>
      </div>
    );
  };

  /** Digits pick in the first question still without an answer: 1 is its first option. */
  const digitInCards = (n: number) => {
    const q = shownQuestions().find((x) => !staged.some((s) => s.q.id === x.id));
    if (!q) return;
    if (n >= 1 && n <= q.options.length) choose(q, n - 1);
    else if (n === q.options.length + 1) choose(q, null);
  };

  function questionStage(): Stage {
    return {
      id: "questions",
      prompt: "What announced?",
      options: [],
      body: questionCards(),
      enter: confirmAnswers,
      digit: digitInCards,
    };
  }

  function stage(): Stage {
    if (view.ended) {
      return { id: "ended", prompt: <>The game is over{view.winner ? `: ${names[view.winner as Side]} won` : ""}.</>, options: [] };
    }
    const base = baseStage();
    if (!view.questions.length && !staged.length) return base;
    if (base.arrival) return { ...base, pre: <>{questionCards()}{base.pre}</>, digit: digitInCards,
                                enter: staged.length ? confirmAnswers : undefined };
    return questionStage();
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

    // A move logged elsewhere (the entry bar, a reload between taps) whose results are still owed.
    const aw = view.turn_progress?.awaiting;
    const owed = aw?.targets.filter((x) => !skipped.includes(`${aw.index}:${posKey(x)}`)) ?? [];
    if (aw && owed.length) {
      return actStage({
        pos: { side: aw.side, slot: aw.slot }, species: aw.species,
        move: { id: aw.move, name: moveName(view, aw.move), target: null, category: null },
        cant: false, aiming: false, targets: owed, spread: owed.length > 1, results: [], crit: false,
        logged: aw.index,
      }, false);
    }
    if (t.after) return anythingElse();

    const lost = loser(view);
    if (lost) {
      const winner = other(lost);
      return {
        id: "over", prompt: <>{names[lost] === "You" ? "You have" : `${names[lost]} has`} nothing left. Log the result?</>,
        options: [{ label: `${names[winner]} won`, pick: () => void log([{ kind: "end", winner }]) }], dflt: 0,
      };
    }

    // 1. Start of turn: switches, then Mega Evolution. Selected, then logged together in the order
    //    selected, which for switches is a Speed reading.
    // A switch or Mega logged this turn before anyone moved means the start was answered already.
    const startLogged = view.journal.slice(turnStart(view.journal) + 1).some((e) => e.kind === "switch" || e.kind === "mega");
    if (!moved && !t.startDone && !t.eot && !startLogged) {
      const toggle = (key: string, text: string, entries: Entry[]) => setPending((ps) => {
        const i = ps.findIndex((x) => x.key === key);
        if (i >= 0 && ps[i].text === text) return ps.filter((_, j) => j !== i);
        if (i >= 0) return ps.map((x, j) => (j === i ? { key, text, entries } : x));
        return [...ps, { key, text, entries }];
      });
      const go = async () => {
        const entries = pending.flatMap((x) => x.entries);
        upd({ startDone: true });
        if (entries.length) await log(entries);
      };
      const rows = actives.filter((p) => !hasActed(p));
      const order = (key: string) => pending.findIndex((x) => x.key === key);
      return {
        id: "start",
        prompt: <>Turn {view.turn}: did anyone switch or Mega Evolve first?
                 <span className="stage-hint">Select switches in the order you saw them: that order is a Speed reading.</span></>,
        options: [{ label: pending.length ? `Confirm ${pending.length} and go to the moves` : "Nothing: on to the moves",
                    tone: "commit", pick: () => void go() }],
        dflt: 0,
        keys: { ".": () => void go() },
        arrival: true,
        pre: rows.length > 0 ? (
          <div className="batch-rows">
            {rows.map((p) => {
              const menu = view.menu[p.side].actives[p.slot]!;
              const sk = `switch:${posKey(p)}`, mk = `mega:${p.side}`;
              const sw = pending.find((x) => x.key === sk);
              const mega = pending.find((x) => x.key === mk);
              const megaHere = mega?.entries[0]?.species === menu.species;
              return (
                <div key={posKey(p)} className="batch-row">
                  <span className="batch-name">
                    {order(sk) >= 0 && <span className="order-badge">{order(sk) + 1}</span>}
                    {nameAt(p)} <span className="opt-sub">{where(p)}</span>
                  </span>
                  <span className="batch-choices">
                    {benchFor(view, p.side).map((b) => {
                      const text = `${nameAt(p)} switched to ${b.species}`;
                      // One Pokémon cannot come in for both of a side's two.
                      const taken = pending.some((x) => x.key !== sk && x.key.startsWith(`switch:${p.side}`)
                                                      && x.entries[0]?.species === b.species);
                      return (
                        <button key={b.species} className="choice radio" role="radio" aria-checked={sw?.text === text}
                                disabled={busy || taken} onClick={() => toggle(sk, text, [{ kind: "switch", side: p.side, slot: p.slot, species: b.species }])}>
                          → {b.species}
                        </button>
                      );
                    })}
                    {menu.megas.map((x) => {
                      const text = `${menu.species} Mega Evolved into ${x.forme}`;
                      return (
                        <button key={x.forme} className="choice check" role="checkbox" aria-checked={mega?.text === text}
                                disabled={busy || (!!mega && !megaHere)} title="one Mega Evolution a side"
                                onClick={() => toggle(mk, text, [{ kind: "mega", side: p.side, species: menu.species, forme: x.forme, item: x.item }])}>
                          {menu.megas.length > 1 ? x.forme : "Mega Evolved"}
                        </button>
                      );
                    })}
                  </span>
                </div>
              );
            })}
          </div>
        ) : undefined,
      };
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
    const left = endOfTurn(view).filter((r) => !t.eotDone.includes(r.key));
    const label = (r: EndOfTurn) => r.reveal ? `${r.effect}, if it holds them` : r.effect;
    if (sub?.kind === "residual") {
      const r = sub.r;
      const pos = { side: r.side, slot: r.slot };
      const finish = async (e: Entry[]) => {
        setSub(null);
        upd({ eotDone: [...t.eotDone, r.key] });
        // Confirming an unseen item's heal is also seeing the item.
        const shown = r.reveal && e.length && mon(pos)
          ? [{ kind: "reveal", side: r.side, species: mon(pos)!.species, what: "item", value: r.reveal }] : [];
        if (e.length) await log([...e, ...shown]);
      };
      const expect = r.expect.hp ?? r.expect.pct ?? 0;
      const unit = r.expect.hp != null ? ` HP` : "%";
      return hpStage(pos, {
        id: `residual:${r.key}`,
        lead: <>{label(r)}: </>,
        prefill: String(expect),
        submit: (v) => void finish(hpSet(pos, v, r.key)),
        extra: [
          { label: `As expected: ${expect}${unit}`, pick: () => void finish(hpSet(pos, expect, r.key)) },
          { label: "Didn't happen", tone: "ghost", pick: () => void finish([]) },
          { label: "KO", tone: "danger", pick: () => void finish(hpSet(pos, 0, r.key)) },
        ],
        dflt: 0,
        keys: { k: () => void finish(hpSet(pos, 0, r.key)) },
        back: () => setSub(null),
      });
    }
    if (left.length > 0) {
      const none = () => upd({ eotDone: [...t.eotDone, ...left.map((r) => r.key)] });
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
          ...left.map((r) => ({
            label: <>{label(r)}: {r.species} <span className="opt-sub">→ about {r.expect.hp ?? r.expect.pct}{r.expect.hp != null ? " HP" : "%"}</span></>,
            pick: () => setSub({ kind: "residual", r }),
          })),
          { label: "None of the rest happened →", tone: "ghost" as const, pick: none },
        ],
        back: () => upd({ eot: false }),
      };
    }

    // 8. Replacements, one fainted slot at a time.
    const reps = view.turn_progress?.waiting ?? [];
    if (reps.length > 0) {
      const order = screenOrder().map(posKey);
      const { was, ...p } = [...reps].sort((x, y) => order.indexOf(posKey(x)) - order.indexOf(posKey(y)))[0];
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
        // A focused button answers Enter itself, and the HP field submits its own form. A focused
        // choice is the exception: a second click would clear it, so Enter confirms the batch.
        if (st.enter && el?.classList.contains("choice")) { ev.preventDefault(); st.enter(); return; }
        if (inHp || el?.tagName === "BUTTON") return;
        if (st.hp) {
          ev.preventDefault();
          const v = Number(hpText);
          if (hpText && Number.isFinite(v)) st.hp.submit(v);
          else if (st.dflt !== undefined) st.options[st.dflt]?.pick();
          else st.hp.empty?.();
          return;
        }
        if (st.enter) { ev.preventDefault(); st.enter(); return; }
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
        if (st.digit) { ev.preventDefault(); st.digit(n); return; }
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

  // Of the Pokémon on the field now, how many have had their turn (a replacement has had its).
  const progress = !view.started ? "team preview"
    : `turn ${view.turn} · ${actives.length - toAct.length} of ${actives.length} ${actives.length === 1 ? "has" : "have"} acted`;

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
            <button key={i} className={optClass(o, s.dflt === i)}
                    aria-checked={o.tone === "toggle" ? !!o.pressed : undefined}
                    role={o.tone === "toggle" ? "checkbox" : undefined}
                    title={o.title} disabled={busy || o.disabled} onClick={o.pick}>
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

