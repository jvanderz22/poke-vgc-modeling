import { SHEET_LABELS, type Gates, type Verdict } from "../api";

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
      <div className="tiny" style={{ marginTop: 6 }}>
        Once a battle is under way this is a different question, and the model for it is chosen
        by whether team sheets are open — so the WP on the Battle and Simulate pages is not
        covered by the warning above. Those pages show their own model&apos;s verdict.
      </div>
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

/** The verdict for the model behind a battle's WP, in the regime that battle is played in.
 *
 *  Which gate that is comes from the backend (`vgc.wp.models.REGIME_GATES`), so this only decides
 *  how to say it. The other regime's verdict is about a game you are not playing, so it is not
 *  the one shown. */
export function BattleGateBanner({ gates, verdict }: { gates: Gates | null | undefined; verdict: Verdict | undefined }) {
  if (!gates || !verdict) return null;
  if (!gates.known) {
    return <div className="banner bad">No WP model is registered for this regulation.</div>;
  }
  const regime = SHEET_LABELS[verdict.sheets]?.regime ?? verdict.sheets;
  const undecided = Object.values(verdict.undecided ?? {});
  if (verdict.pass == null && verdict.failed.length === 0 && undecided.length > 0) {
    return (
      <div className="banner warn">
        <b>{gates.version}</b> cannot be verified with {regime} yet — {undecided.join("; ")}.
        Nothing says it is wrong here, and nothing could have: read the number as a direction,
        not a percentage.
      </div>
    );
  }
  if (verdict.pass == null && verdict.failed.length === 0) {
    return (
      <div className="banner warn">
        <b>{gates.version}</b> has not been scored with {regime}. The number below is not verified
        for this battle.
      </div>
    );
  }
  if (verdict.pass) {
    return (
      <div className="banner ok tiny">
        <b>{gates.version}</b> beats 50% turn by turn and stays calibrated with {regime}.
      </div>
    );
  }
  const calibrationOnly = verdict.failed.length > 0 && verdict.failed.every((g) => g.includes("ece"));
  const h = verdict.sheets === "closed" ? gates.closed_headline : null;
  return (
    <div className="banner warn">
      <b>{gates.version}</b> fails with {regime}: {verdict.failed.join(", ") || verdict.gate}.{" "}
      {calibrationOnly
        ? "It still beats 50% turn by turn, but it is more confident than it should be — read the number as a direction, not a percentage."
        : "Read the number as a rough guide at best."}
      {h?.n != null && (
        <span className="dim tiny"> Scored on {h.n.toLocaleString()} held-out rows
          {h.logloss != null && `, log loss ${h.logloss.toFixed(4)}`}.</span>
      )}
    </div>
  );
}
