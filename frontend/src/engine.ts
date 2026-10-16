import { useEffect, useState } from "react";
import { api, type EngineAnswer, type LiveView } from "./api";

/** The engine's answer for this position, asked again until the search and its guesses at the
 *  hidden bulk have landed. `null` while the first answer is on its way.
 *
 *  The search deepens in the background, one turn at a time. A doubles answer has seconds, not
 *  minutes, so it is asked for often enough to show it as it lands. */
export function useEngine(id: string, reg: string, view: LiveView, enabled = true): EngineAnswer | null {
  const [ans, setAns] = useState<EngineAnswer | null>(null);
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
  }, [id, reg, view, enabled]);
  return ans;
}
