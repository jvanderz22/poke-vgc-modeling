/** A Pokémon name as a Bootstrap badge.
 *
 *  `lead` marks the two that start on the field. Leads are shown solid and first, because which
 *  two you send out is a different decision from which four you bring, and reading a flat list of
 *  four names hides it. */
export function MonBadge({ name, lead = false, extra }: { name: string; lead?: boolean; extra?: string }) {
  return (
    <span className={`badge rounded-pill mon-badge ${lead ? "text-bg-primary" : "text-bg-secondary"}`}>
      {name}
      {extra && <span className="mon-badge-extra">{extra}</span>}
    </span>
  );
}

/** The four brought, leads first and marked. `back` is whatever the API did not call a lead. */
export function BringBadges({ leads, back }: { leads: string[]; back: string[] }) {
  return (
    <span className="mon-badges">
      {leads.map((n) => <MonBadge key={n} name={n} lead />)}
      <span className="bring-sep" aria-hidden="true">·</span>
      {back.map((n) => <MonBadge key={n} name={n} />)}
    </span>
  );
}
