import type { Gates } from "../api";

/** PLAN.md's rule is that a model failing a gate is never presented as calibrated. This banner is
 *  how the app honours it: the verdict sits above the numbers it qualifies, not in a tooltip. */
export function GateBanner({ gates }: { gates: Gates | null }) {
  if (!gates) return null;
  if (!gates.known) {
    return <div className="banner bad">No WP model is registered for this regulation.</div>;
  }
  if (!gates.evaluated) {
    return (
      <div className="banner warn">
        <b>{gates.version}</b> has not been evaluated — run <code>vgc wp eval</code>. Nothing below is verified.
      </div>
    );
  }
  if (gates.all_pass) {
    return (
      <div className="banner ok">
        <b>{gates.version}</b> passes every Phase&nbsp;4 gate.
        {gates.headline?.logloss != null && (
          <span className="dim"> · held-out human log loss {gates.headline.logloss.toFixed(4)}</span>
        )}
        <RegimeNote gates={gates} />
      </div>
    );
  }
  const previewBad = gates.preview_gate === false;
  return (
    <div className={`banner ${previewBad ? "bad" : "warn"}`}>
      <b>{gates.version}</b> fails: {(gates.failed ?? []).join(", ")}.{" "}
      {previewBad ? (
        <>
          <b>The preview gate is one of them.</b> Before turn 1 this model does not beat answering
          50%, on the 2,899 held-out preview rows it was scored on — and no model has, from this
          corpus. Use the ranking below to explore, not to decide.
        </>
      ) : (
        <>The failing gates are not the preview gate, but read the ranking as approximate.</>
      )}
      {gates.in_battle_pass && (
        <div className="tiny" style={{ marginTop: 6 }}>
          Once a battle is under way this is a different question, and a model that passes the
          in-battle gates is used for it — so the WP track on the Battle and Simulate pages is not
          covered by the warning above.
        </div>
      )}
      <RegimeNote gates={gates} />
    </div>
  );
}

/** Which information regime the verdict above was reached in.
 *
 *  Every other gate here is scored on the Bo3 ladder, where both team sheets are shown. A game
 *  on a cartridge shows neither, and a model fit only on open sheets has never had an unknown
 *  opponent in a training row — so "passes the in-battle gates" was a claim about a format this
 *  app is never used in. This is the same question asked on the held-out Team Preview Only
 *  shard, and the number of rows is part of the answer: it is a small set and says so. */
function RegimeNote({ gates }: { gates: Gates }) {
  const n = gates.closed_headline?.n;
  if (gates.closed_sheet_pass == null) {
    return (
      <div className="tiny" style={{ marginTop: 6 }}>
        Not scored with team sheets hidden. Everything above is an Open Team Sheets number, which
        is the Bo3 ladder and not a cartridge game — re-run <code>vgc wp eval</code> on a dataset
        built since the closed-sheet held-out set was added.
      </div>
    );
  }
  return (
    <div className="tiny" style={{ marginTop: 6 }}>
      With team sheets hidden — the regime you are actually in —{" "}
      {gates.closed_sheet_pass ? "this model still beats 50% turn by turn and stays calibrated"
                               : <b>this model does not hold up turn by turn</b>}
      {gates.closed_headline?.logloss != null && (
        <span className="dim"> (log loss {gates.closed_headline.logloss.toFixed(4)}
          {n != null && `, ${n.toLocaleString()} held-out rows`})</span>
      )}
      .
    </div>
  );
}
