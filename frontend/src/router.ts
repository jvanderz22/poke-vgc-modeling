/** URLs for the whole app, so every view can be linked, bookmarked and reloaded.
 *
 *  Hand-rolled rather than a router dependency: there are five pages and two of them take a
 *  parameter, and the whole route space fits in one discriminated union that TypeScript can check
 *  exhaustively. What matters is that `parseRoute` and `routeUrl` are pure inverses of each other
 *  — every route the app can be in has a URL, and every URL parses back to it.
 *
 *  Paths, all served by the Python app (see the SPA fallback in `web/app.py`):
 *
 *    /preview                          rank your brings
 *    /battle                           the in-battle companion
 *    /simulate                         play two teams against each other
 *    /endgames                         the decided-endgame set        ?filter=played_out|misses
 *    /endgames/<replay>                one game, opened where it was decided
 *    /endgames/<replay>/<step>         one game at a given step, 1-based as the UI counts them
 *    /teams                            the library
 *    /teams/<id>                       one team, open for editing
 *
 *  Anything unrecognised resolves to /preview, and the app rewrites the bar to match rather than
 *  leaving a URL on screen that does not describe what is being shown.
 */

import { useCallback, useEffect, useState, type MouseEvent } from "react";

export type Tab = "preview" | "battle" | "simulate" | "endgames" | "teams";
export type EndgameFilter = "all" | "played_out" | "misses";

export const TABS: Tab[] = ["preview", "battle", "simulate", "endgames", "teams"];
const FILTERS: EndgameFilter[] = ["all", "played_out", "misses"];

export type Route =
  | { tab: "preview" | "battle" | "simulate" }
  | { tab: "endgames"; replay: string | null; step: number | null; filter: EndgameFilter }
  | { tab: "teams"; team: string | null };

/** The route a tab lands on when you click it in the header: the page, nothing selected. */
export function tabRoute(tab: Tab): Route {
  if (tab === "endgames") return { tab, replay: null, step: null, filter: "all" };
  if (tab === "teams") return { tab, team: null };
  return { tab };
}

export function parseRoute(url: URL): Route {
  const [first, second, third] = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const tab = (TABS as string[]).includes(first) ? (first as Tab) : "preview";

  if (tab === "endgames") {
    const step = Number(third);
    const filter = url.searchParams.get("filter") as EndgameFilter | null;
    return {
      tab,
      replay: second || null,
      // A step only means something inside a game, and it is 1-based because that is how the
      // stepper counts out loud ("step 12 of 16"); an unusable one is dropped, not clamped here.
      step: second && Number.isInteger(step) && step > 0 ? step : null,
      filter: filter && FILTERS.includes(filter) ? filter : "all",
    };
  }
  if (tab === "teams") return { tab, team: second || null };
  return { tab };
}

export function routeUrl(route: Route): string {
  if (route.tab === "endgames") {
    let path = "/endgames";
    if (route.replay) {
      path += `/${encodeURIComponent(route.replay)}`;
      if (route.step) path += `/${route.step}`;
    }
    return route.filter === "all" ? path : `${path}?filter=${route.filter}`;
  }
  if (route.tab === "teams") {
    return route.team ? `/teams/${encodeURIComponent(route.team)}` : "/teams";
  }
  return `/${route.tab}`;
}

export type Navigate = (route: Route, options?: { replace?: boolean }) => void;

function currentUrl(): string {
  return window.location.pathname + window.location.search;
}

/** The current route, and a way to change it.
 *
 *  `replace` is for a change that refines what you are already looking at rather than moving you
 *  somewhere new — stepping through a game's turns is the case that matters. Those belong in the
 *  URL so a turn can be linked, but pushing each one would turn the back button into a rewind
 *  key and bury the page you arrived from under twenty entries.
 */
export function useRoute(): [Route, Navigate] {
  const [route, setRoute] = useState<Route>(() => parseRoute(new URL(window.location.href)));

  useEffect(() => {
    const onPop = () => setRoute(parseRoute(new URL(window.location.href)));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback<Navigate>((next, options) => {
    const url = routeUrl(next);
    if (url !== currentUrl()) {
      window.history[options?.replace ? "replaceState" : "pushState"]({}, "", url);
    }
    setRoute(next);
  }, []);

  // Canonicalise on arrival: "/", a typo or a stale link should leave the bar showing the page
  // actually on screen, without adding a history entry for the URL that was never valid.
  useEffect(() => {
    const url = routeUrl(route);
    if (url !== currentUrl()) window.history.replaceState({}, "", url);
  }, [route]);

  return [route, navigate];
}

/** Props for an `<a>` that navigates in-app but is still a real link: middle-click, ⌘-click and
 *  "copy link address" all keep working, which is most of the point of having URLs at all. */
export function linkProps(to: Route, navigate: Navigate, options?: { replace?: boolean }) {
  return {
    href: routeUrl(to),
    onClick: (e: MouseEvent) => {
      // Let the browser handle anything that means "not here": a new tab, a new window, a save.
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      navigate(to, options);
    },
  };
}
