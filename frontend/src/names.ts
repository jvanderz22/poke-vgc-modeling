import { useEffect, useState } from "react";
import { api } from "./api";
import { PRETTY, toId } from "./turn";

// A battle's items, abilities and moves arrive as ids (`rockyhelmet`); the pool has every one of
// them as it is written. Asked once per page load: the pool does not change while the app runs.
let cache: Promise<Map<string, string>> | null = null;

function load(reg: string): Promise<Map<string, string>> {
  if (!cache) {
    cache = api.pool(reg).then((p) => {
      const names = new Map<string, string>();
      for (const x of [...p.items, ...p.moves, ...p.species.flatMap((s) => [s.name, ...s.abilities])]) {
        names.set(toId(x), x);
      }
      return names;
    });
    cache.catch(() => { cache = null; });
  }
  return cache;
}

/** An id as the game writes it: Rocky Helmet, not rockyhelmet. Until the pool has come back, and
 *  for anything it does not list, the field and condition names `turn.ts` knows, else the id. */
export function useNames(reg: string): (id: string) => string {
  const [names, setNames] = useState<Map<string, string> | null>(null);
  useEffect(() => { load(reg).then(setNames, () => {}); }, [reg]);
  return (id: string) => names?.get(toId(id)) ?? PRETTY[toId(id)] ?? id;
}
