# The battle companion

A local web app for use *during* a game. Champions is played in the game itself rather than on
Showdown, so there is no log to read: you tell it what you see, and it answers using the same
functions the CLI uses.

Everything stays on this machine. The simulator, the calc sidecar and the ONNX models all run
here, nothing is hosted, and no team or result is sent anywhere.

## Running it

```bash
pip install -e '.[web]'      # once: fastapi + uvicorn
make web                     # builds the frontend, then serves → http://127.0.0.1:8001
```

`make web` is `npm --prefix frontend install && npm --prefix frontend run build` followed by
`vgc web`. Once built, plain `vgc web` is enough; the build only has to be redone when the
frontend source changes. If you open it unbuilt, the page tells you so instead of 404ing.

For frontend work, `make web-dev` runs Vite on `:5173` with hot reload, proxying `/api` to the
Python app — run `vgc web` alongside it.

| Flag | Default | |
| --- | --- | --- |
| `--port` | `8001` | Not 8000: that is the local Showdown server's port (`vgc server start`). |
| `--host` | `127.0.0.1` | This machine only. See "Using it from your phone" below before changing it. |
| `--reload` | off | Restart on source changes, for development. |

Stop it with ctrl-c.

## The pages

### Teams

Your library. Paste a team in Showdown/PokéPaste format, give it a name and notes, and save.
Validation runs on save and on demand: SP budget, clauses, pool and Tera rules against the active
regulation, plus level-50 stats per Pokémon and any problem named with the Pokémon that caused it.
Teams can be edited, deleted, and sent straight to the battle page with **use**.

Teams live in `data/library/<regulation>.json` — written by the Python side, not the browser, so
the file is yours to back up, diff or edit by hand, and clearing your browser loses nothing. It is
gitignored as personal state; un-ignore it if you want version history for your teams.

Validation is recomputed on every save and never trusted from the file, because the regulation's
legality snapshot can change under a team that was legal when it was written.

### Battle

**Your team**: pick one from the library, or switch to **Paste** for something you have not saved.

**Their team**: two modes.

- **Open sheet** — paste what you were shown at the start of a Bo3 game. This is exactly what the
  models were trained on and is the only mode whose answers are as good as the model gets.
- **Closed** — pick their six species with search-as-you-type (ordered by how often each is
  actually played, so the names you want are at the top of an empty box). See the next section for
  what this costs.

**Rank my brings** then scores all 90 options — 15 ways to bring 4 of 6, times 6 lead pairs —
showing the best lead for each set of four, plus which four the opponent is likely to bring.

Both sheets go through the real simulator's validator, so an illegal one comes back with
Showdown's own message naming the offending Pokémon.

### Endgames

A set of real human games the WP model called at **90%+ before they ended**, to click through.
It exists to be argued with: a 90% number is worth showing only if roughly 90 of every 100 such
positions are actually won, and the way to check that is to look at them.

Every game in the set is:

- **human vs human** — both accounts are people, not the scripted ladder alts, and both teams are
  ones those people built;
- **held out** — the replay's group falls on the held-out side of the frozen split, recomputed
  from `data/splits/<reg>.json` rather than trusted from any file, so the model never trained on
  the game or its series;
- **open team sheets** — every WP model is an OTS model, so a closed-sheet game would be scored
  on inputs it was never given;
- **decided** — it ended with a winner, and the model held 90%+ on one side for the last three
  decision points. A single spike does not qualify; a sustained call does.

The header carries the hit rate *and* what it was drawn from, because "239 out of 240" means
nothing without "out of the 1,114 held-out games that could have qualified". **Model was wrong**
filters to the games the favoured side went on to lose — the ones actually worth reading — and
**Played out** drops the forfeits, where nobody made the winner finish the job.

Picking a game opens it at the decision point where it stopped being in doubt. A step is one
decision, anchored *before* the action: the position the model was asked about and its answer,
then the turn's log, then the position that produced. ← and → walk it, and the controls carry
what the step did to the number — `92% / 8% → 0% / 100%` on the turn that decided this one.

Both boards are shown because the interesting part is usually the difference. The start-of-turn
board is the whole sheet, all six, with a marker for what is known about each:

| | |
| --- | --- |
| `55%` | on the field, or brought and waiting — bold when it is out |
| `KO` | brought, knocked out |
| `?` | not seen yet, so it may or may not have been brought |
| `O` | not brought — knowable only once the other four have appeared |

Nothing is dropped, because what someone chose to leave behind against this opponent is a
decision as much as what they brought. At team preview every row is a `?`; they resolve as the
game reveals them. The end-of-turn board is the active slots only, and a slot whose occupant
changed puts what left and what came in on one line, since a switch is one event rather than two.

The finish is not a separate step: it is the turn the game ended on, and that turn's closing
number is the result, 100% or 0%, flagged as an outcome rather than passed off as a prediction.
Without it a game that swung from 8% to a win would read as though it never resolved. The
scrubber is the whole game at a glance — every tick is split between the two sides, p1 growing up
from the bottom and p2 down from the top, and the outlined tick on the end is the result. Click
it and you get the final position on its own, without the turn that produced it above it.

## URLs

Every view has one, so a position can be linked, bookmarked, reloaded or sent to someone.

| | |
| --- | --- |
| `/preview` | rank your brings |
| `/battle` | the in-battle companion |
| `/simulate` | play two teams against each other |
| `/endgames` | the decided-endgame set — `?filter=played_out` or `?filter=misses` |
| `/endgames/<replay>` | one game, opened where it stopped being in doubt |
| `/endgames/<replay>/<step>` | one game at a given step, counted as the stepper counts it |
| `/teams` | the library |
| `/teams/<id>` | one team, open for editing |

Anything else resolves to `/preview`, and the address bar is rewritten to match rather than left
describing a page that is not on screen. Stepping through a game replaces the history entry
instead of pushing one, so a turn stays linkable without the back button turning into a rewind
key — Back leaves the game you are in.

The Python app serves the built page for any path it does not own itself, which is what makes a
deep link survive a reload. An unmatched `/api/…` path is still a 404, because handing a caller
200 and a page of HTML for a misspelled endpoint is worse than a plain miss.

The set is built offline, because it reads every cached replay:

```bash
vgc wp endgames                            # → data/analysis/<reg>/endgames.json
vgc wp endgames --min-wp 0.95 --hold 5     # a stricter set
```

It uses the model that passes the *in-battle* gates, which is not necessarily the app's default
(see the banner section below) — today that is the GBT baseline, not the newest set encoder.
Until it has been run, the page says so and prints the command instead of showing an empty list.

## What "Closed" actually does

A closed sheet shows species and nothing else, but the simulator needs a complete, legal team. So
each species is filled with **the set most often used with it**, counted across the 15,028 sheets
in the team pool.

That is a guess, and the app shows how thin a guess: every inferred set is listed with the share
of that Pokémon's sheets it accounts for. Incineroar's most common set is **15%** of its 4,895
appearances; Rillaboom's is 38%. Anything under a quarter — or a species with no usage data, which
falls back to a generic legal set — is counted and flagged.

The deeper limitation is not the guess but how the model treats it: **one guessed team, evaluated
with full confidence**, rather than an average over everything the opponent might be holding. A
Choice Scarf you guessed as an Assault Vest is simply wrong, not uncertain. Phase 5 replaces this
with a belief over their sets that updates as the battle reveals things, which is what makes
closed-sheet numbers trustworthy. Until then, prefer an open sheet whenever you have one.

## What it does not do yet, and why

**Turn-by-turn move advice.** That needs **Phase 6** (expected WP and search) and the state
reconstruction work in PLAN.md L5b — turning what you type into a Showdown state the evaluator
can clone and step. That work needs its own parity tests before any recommendation from it is
worth showing.

## Reading the banner at the top

The app names the gate verdicts that `vgc wp eval` recorded for the model it is using. This is
the most important thing on the page.

| Banner | Means |
| --- | --- |
| green | The model passes every Phase 4 gate. |
| amber | It fails gates, but not the preview gate. Bring rankings are approximate. |
| amber, "not evaluated" | Nobody has run `vgc wp eval` on it. Nothing below is verified. |
| **red** | It fails the **preview gate** — on teams it has not seen, its ordering of bring options tracks simulated results no better than chance. |

As of 2026-09-19 the current model (`wp-v1-set-full`) shows **red**. Its predicted win
probabilities at preview are badly under-dispersed: on a sample pairing the top five brings fell
between 51.6% and 53.8%, so it reports near-even odds for every choice. Treat the list as a way
to explore options, not to decide between them. `vgc wp registry` prints the same verdicts.

The rule this follows is in PLAN.md: *a model that misses a gate is not used by the CLI, the web
app or search, and says so in its model card.* The app is allowed to show a failing model's
numbers, but never without the verdict beside them.

## Using it from your phone

Not yet built. PLAN.md L5b describes an opt-in LAN mode with an access token, so a phone next to
the Switch can drive it while the Mac does the work. Until that exists, `--host 0.0.0.0` would
expose an **unauthenticated** app to your whole network — don't.

## The API

The UI is a thin client over a documented API, so the MCP server and the app can share one
surface. Interactive docs are at `/docs` while the server runs.

| Endpoint | |
| --- | --- |
| `GET /api/health` | Regulation, level, bring count, SP budget, which mechanics are on |
| `GET /api/models` | Every registered model with its gate verdicts, and the default |
| `POST /api/validate` | `{text}` → legality, per-Pokémon stats, problems |
| `POST /api/preview` | `{my_team}` plus `their_team` (open) or `their_species` (closed) → ranked brings, their likely four, gate verdicts, and `inferred_sets` when sets were guessed |
| `GET/POST /api/teams`, `DELETE /api/teams/{id}` | The saved team library (`data/library/<reg>.json`) |
| `GET /api/pool` | Legal species ordered by usage, plus items, moves and natures |
| `POST /api/compose` | Six species → a legal team, each with its most common set and that set's share |
| `POST /api/simulate` | Two teams → one seeded battle, narrated turn by turn with spectator WP |
| `GET /api/endgames` | The decided-endgame set: games, criteria, what they were drawn from, gate verdicts. `only=all\|played_out\|misses` |
| `GET /api/endgames/{replay_id}` | One of those games position by position: board, events, WP, both open sheets |

Validation is recomputed whenever a team is saved and never trusted from the file: the
regulation's legality snapshot can change under a team that was legal when it was written.
