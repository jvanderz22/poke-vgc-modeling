import { useCallback, useEffect, useState } from "react";

/** Light or dark, or whatever the system says.
 *
 *  The choice is the viewer's, kept in this browser; "system" follows the OS setting live. The
 *  resolved theme goes on <html> as `data-theme` (the app's tokens, styles.css) and
 *  `data-bs-theme` (Bootstrap's own controls). index.html applies the same rule before the first
 *  paint, so a light-mode viewer never sees a dark flash. Private windows throw on localStorage,
 *  so every access is guarded. */
export type ThemePref = "system" | "light" | "dark";

const KEY = "vgc:theme";
const ORDER: ThemePref[] = ["system", "light", "dark"];
const media = () => window.matchMedia("(prefers-color-scheme: light)");

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch { return "system"; }
}

function apply(pref: ThemePref) {
  const theme = pref === "system" ? (media().matches ? "light" : "dark") : pref;
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-bs-theme", theme);
}

export function useTheme(): [ThemePref, () => void] {
  const [pref, setPref] = useState<ThemePref>(readPref);

  useEffect(() => {
    apply(pref);
    if (pref !== "system") return;
    const m = media();
    const follow = () => apply("system");
    m.addEventListener("change", follow);
    return () => m.removeEventListener("change", follow);
  }, [pref]);

  const cycle = useCallback(() => setPref((p) => {
    const next = ORDER[(ORDER.indexOf(p) + 1) % ORDER.length];
    try { next === "system" ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, next); } catch { /* private window */ }
    return next;
  }), []);

  return [pref, cycle];
}
