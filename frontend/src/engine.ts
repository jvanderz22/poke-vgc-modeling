import { useEffect, useMemo, useState } from "react";
import { api, type EngineAnswer, type LiveView } from "./api";

/** The engine's answer for this position, asked again until the search and its guesses at the
 *  hidden bulk have landed. `null` while the first answer is on its way.
 *
 *  The search deepens in the background, one turn at a time. A doubles answer has seconds, not
 *  minutes, so it is asked for often enough to show it as it lands. */
export function useEngine(id: string, reg: string, view: LiveView, enabled = true): EngineAnswer | null {
  const [ans, setAns] = useState<EngineAnswer | null>(null);
  const at = usePosition(view);
  useEffect(() => {
    setAns(null);
    if (!enabled) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => api.solve(id, reg).then((a) => {
      if (!live) return;
      setAns(a);
      if (a.eligible && (a.searching != null || a.guesses?.pending)) timer = setTimeout(tick, a.kind ? 500 : 2000);
    }).catch(() => { if (live) timer = setTimeout(tick, 5000); });
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [id, reg, at, enabled]);
  return ans;
}

/** The position the engine searches, as a key: the journal up to its decision point
 *  (`live.at_decision_point`, the `as_of` the model's number carries too). The taps inside a turn
 *  don't move it, so the reader's writes through a turn neither restart the search nor blank the
 *  answer while it is asked for again. */
function usePosition(view: LiveView): string {
  const at = view.wp?.as_of?.at ?? view.entries;
  return useMemo(() => `${at}:${hash(JSON.stringify(view.journal.slice(0, at)))}`, [view.journal, at]);
}

/** FNV-1a over a string's code units: the key stays short however long the journal gets. */
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
  return h >>> 0;
}
