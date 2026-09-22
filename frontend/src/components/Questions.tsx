import type { Entry, Question } from "../api";

/** What the rules could not settle, put where it cannot be missed.
 *
 *  Three shapes, and the difference matters to how fast a turn goes:
 *
 *  * **A confirm.** One possible answer, asked anyway because *when* it happened is evidence — a
 *    switch-in ability announces in Speed order, so the tap that costs nothing is the tap that
 *    carries the information. One button, saying what happened.
 *  * **A menu.** Several answers, each pinning a different ability. This is where a battle is
 *    actually won: tapping "Defiant" the moment Kingambit answers an Intimidate is both the
 *    boost and the reveal, and the app never has to guess again.
 *  * **Not sure.** Always available, always last, and it concludes nothing. Under a timer people
 *    mistap, and a belief that excludes the truth because of one is worse than one that stayed
 *    wide — so the honest answer has to be as easy to reach as the confident ones.
 *
 *  Answering in the order you *saw* things is what makes a batch of arrivals into Speed evidence,
 *  so the cards stay in the order the backend raised them and say so.
 */
export function Questions({ questions, onAnswer, busy }: {
  questions: Question[];
  onAnswer: (entry: Entry) => void;
  busy: boolean;
}) {
  if (!questions.length) return null;
  return (
    <div className="panel q-panel">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>What happened?</h2>
        {questions.length > 1 && (
          <span className="tiny dim">
            answer in the order you saw them — that order is what tells the app who is faster
          </span>
        )}
      </div>
      {questions.map((q) => (
        <div key={q.id} className="q-card">
          <div className="q-prompt">
            {q.prompt}
            {q.source && <span className="dim"> · from {q.source.species}</span>}
          </div>
          <div className="q-options">
            {q.options.map((o, i) => (
              <button
                key={i}
                className={o.label.startsWith("nothing announced") ? "ghost" : ""}
                disabled={busy}
                onClick={() => onAnswer({ kind: "answer", question: q.id, option: i })}
                title={o.ability ? `proves its ability is ${o.ability}` : undefined}
              >
                {o.label}
                {o.conditional && <span className="q-maybe" title="announces only sometimes — so not picking it proves nothing">?</span>}
              </button>
            ))}
            <button className="link" disabled={busy}
                    onClick={() => onAnswer({ kind: "answer", question: q.id, option: null })}>
              Not sure
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
