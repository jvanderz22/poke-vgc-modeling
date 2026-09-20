# The battle companion

A local web app for use *during* a game. Champions is played in the game itself rather than on
Showdown, so there is no log to read: you tell it what you see, and it answers using the same
functions the CLI uses.

Everything stays on this machine. The simulator, the calc sidecar and the ONNX models all run
here, nothing is hosted, and no team or result is sent anywhere.

## Running it

```bash
pip install -e '.[web]'      # once: fastapi + uvicorn
vgc web                      # http://127.0.0.1:8001
```

| Flag | Default | |
| --- | --- | --- |
| `--port` | `8001` | Not 8000: that is the local Showdown server's port (`vgc server start`). |
| `--host` | `127.0.0.1` | This machine only. See "Using it from your phone" below before changing it. |
| `--reload` | off | Restart on source changes, for development. |

Stop it with ctrl-c.

## What it does today

1. **Paste your team** in Showdown/PokéPaste export format and **Validate**. You get the SP
   budget, clauses, pool and Tera rules checked against the active regulation, per-Pokémon
   level-50 stats, and any problem named with the Pokémon that caused it.
2. **Paste the opponent's open team sheet** — the sheet you are shown at the start of a Bo3 game.
3. **Rank my brings** scores all 90 options (15 ways to bring 4 of 6, times 6 lead pairs),
   showing the best lead for each set of four, plus which four the opponent is likely to bring.

Both sheets are validated by the real simulator, exactly as a Bo3 game would present them. An
illegal sheet comes back with Showdown's own message naming the offending Pokémon.

## What it does not do yet, and why

**Closed sheets.** Every WP model is trained with the opponent's items, abilities, moves and
spreads visible (`info_regime: "ots"`). In a closed-sheet game you know six species and nothing
else, and there is no team to hand the simulator — so this is a different model, not a display
mode. It needs the set prior from **Phase 5** ("what does a Flutter Mane usually run?").

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
| `POST /api/preview` | `{my_team, their_team}` → ranked brings, their likely four, gate verdicts |
| `GET/POST /api/teams`, `DELETE /api/teams/{id}` | The saved team library (`data/library/<reg>.json`) |

Validation is recomputed whenever a team is saved and never trusted from the file: the
regulation's legality snapshot can change under a team that was legal when it was written.
