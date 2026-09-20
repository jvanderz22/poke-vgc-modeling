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
    </div>
  );
}
