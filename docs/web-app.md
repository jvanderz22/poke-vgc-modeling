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

- **Open sheet** — enter what you were shown at the start of a Bo3 game, as it is shown: six cards,
  each with species, ability, nature, item and four moves (no Stat Points, which a sheet does not
  show). Every field searches as you type over what is legal for that Pokémon, ordered by how often
  people run it (`GET /api/species/{name}`), and ↵ picks and moves on to the next empty field. The
  one ability a species can have is filled in; nothing else is guessed. **import text** fills the
  form from Showdown or PokéPaste text. The sheet is kept in the browser until cleared, and Preview
  and the new-battle form share it. This is exactly what the models were trained on and is the only
  mode whose answers are as good as the model gets.
- **Closed** — pick their six species with search-as-you-type (ordered by how often each is
  actually played, so the names you want are at the top of an empty box). See the next section for
  what this costs.

**Rank my brings** then scores all 90 options — 15 ways to bring 4 of 6, times 6 lead pairs —
showing the best lead for each set of four, plus which four the opponent is likely to bring.

Both sheets go through the real simulator's validator, so an illegal one comes back with
Showdown's own message naming the offending Pokémon.

**The number during a turn** stays where the turn started, dimmed, with a line saying so. Once a
replacement for a fainted Pokémon has been logged, it moves to the moment before that replacement.
When no slot is still waiting, it moves to the position the next turn starts from. The models were
trained on decision points only: team preview, the start of a turn, and replacements at its end.
Inside a turn the rest of the turn is already chosen, and nothing in an observation says who has
acted. So a position read after the faster side's hit and before the slower side's reply leans
towards whoever moved first (`live.decision_point`). The engine's answer is asked about the same
point, because a search from inside a turn would have every Pokémon choose again. A tap inside a
turn leaves both alone, and a search under way keeps deepening. The set panel still updates on
every tap. Walking back to a turn mark shows the number at the end of that turn, the same position
the trajectory scores. The trajectory's last bar is the live number.

### Watch

Someone else's game on YouTube, beside the match. Paste a link (with `t=` if you like) and the page
takes the full width: the video on the left half, kept in view while you scroll, and the match on
the right. The URL carries the video (`/watch?v=<id>&t=<seconds>`), so a reload or a shared link
opens it again.

Before the battle, the right half is both players' sheets and names. **Read sheets from the video**
asks to share the tab (Chrome or Edge) and captures only the player, through Region Capture. The
page's own forms are text the reader would read back, so without Region Capture it refuses rather
than capture the whole tab. A new picture is sent at most every 1.5 seconds and read like a
screenshot. A frame counts as a sheet only if at least four Pokémon on it have moves under them, so
a battle's nameplates or team preview don't. The first team read goes to player 1 and the next to
player 2, with the broadcast's name for each. A later read of the same team only fills what is
still empty. Where it disagrees with a field already set, the form keeps its value and the page
lists the disagreement. Reading stops once both sheets are full. **Start watching** makes a
Watching battle (`/watch/<id>`), with the two names on its setup, and the page becomes the battle
beside the video.

Once the battle exists the page is laid out to be read at a glance on a laptop, with nothing below
the fold at 1512×860: the video takes two thirds of the width, both sixes sit under it a row each
(the player on the left of the screen first, once the reader has seen which that is), and the
battle is a column beside it (`WatchBoard.tsx`). Top to bottom:

- **The number**, one, said as the question it answers. Where the engine leads (two or fewer a
  side) it is "with best play from both sides", and the model's stays beside it as "games like
  this", with its gate verdict. Elsewhere the model's is the number. ⓘ says how each was made.
  Under it, the model's number at each turn so far, with the engine's as a dot at the end; a point
  walks back to that turn.
- **The field**: the four on it with everything known about each (HP, boosts, item, ability,
  nature, moves; on a hidden sheet "Unknown" for a move not yet seen, with the likeliest a hover
  away), and what being on the field says about its next move (`live.on_field`): locked into a
  move by a Choice item, Fake Out ready on its first turn out, or a Protect that may fail after one
  held the turn before. Then the field and side conditions with the turns they have left, counting
  the one being played (`live.timers`, from what put each up and the turn it counts from, as the
  replay records them), and Speed as ranges on one axis. A hatched range is all the nature and
  Stat Points allow; a solid one has been narrowed by turn order. Each pair under it is decided or
  open.
- **The turn so far and the one before it**, each line with the video time it was read at; a time
  puts the video there.
- Both sides in full (`BattleState`), folded away.

Items, abilities and moves are shown by name (from the pool), not by id. Entering taps by hand puts
the stepper at the top of the column.

**Read the battle** (while capturing, once the battle exists) sends the player a frame every
quarter second while the video plays, and none while it is paused, to the battle reader
(`POST /api/battles/{id}/read`, `vgc.video.reading`). What it settles is appended to the battle's
journal, each entry stamped with the `video_s` it was read at, and the battle half refreshes as it
lands. A frame goes only once the last has been answered, so a slow server reads fewer frames
rather than falling behind; under 2 a second the page warns that a short message can be missed,
at the top of the battle column and under the player, with where an average frame's time went
(taking the picture, drawing and encoding it, finding its text, OCR on the GPU, the server, the
network; the server's share is `server_ms` on each reply). A game starts once both sides' leads
are read. A send-out missed (read too slowly, or the game opened late) is made up from the plates at
the first decision point, and noted in the review log, rather than leaving the game unstarted.
The line under the player says how far it has read, the last message, and how many things it
couldn't read (each with its time, under "What it couldn't read"). The journal stays yours:
correct an entry by hand (undo, re-enter) and the reader takes the journal as it then stands and
reads on from there. Rewinding skips frames until the video is past where the reading had got to,
so nothing is read twice, unless it goes back to more than 2.5 s after the last thing written: that
stretch is read again, so a second pass picks up what the first missed. Skipping ahead leaves a gap
that isn't read; the page lists it and the review log keeps it. **Stop reading the battle** writes out what the reader still held (a move
waiting on its results). A reading lives in the server's memory: a restart ends it, and reading
again on the same battle carries on from its journal. The reader knows one broadcast layout
(Louisville's, [video-findings](video-findings.md)) and is unscored until PLAN-video step 3.

An entry made by hand on this page carries the player's time as `video_s` too, so a game entered
by hand beside its video is a transcription the reader can be scored against:
`vgc video score READ HAND` lines a read journal up with a hand one turn by turn and reports
PLAN-video's gates (`vgc.video.score`). READ and HAND are each a battle id, a battle's JSON, or a
`vgc video read --json`.

The player is the YouTube IFrame API's, so the page knows the video's own time. The capture asks for
8 frames a second, twice what the readers sample, and at most 1600 across, the most any reader
takes. Both are asked for when the capture starts, since later limits save little; the frames are
encoded in a worker (`frameWorker.ts`), off the page's thread. Without them the video stuttered
([video-findings](video-findings.md#in-the-browser-2026-10-15)). While capturing, **Record clip**
saves the player as `<video>-<time>.webm`, at the capture's 8 frames a second, with a `.json`
beside it: the player's time sampled every 200 ms. **Save frame** saves one PNG named by its video
time. These are the fixtures the battle reader is built and scored on (PLAN-video steps 1–3). Keep them in
`data/video/`, which is gitignored: a battle is a few hundred MB. `vgc video frames CLIP` turns a
clip into PNGs named by clip time and video time (`vgc.video.clip`, decoded by PyAV).

**⚑ Flag this moment** saves the video's time and a note to the review log
(`POST /api/video/flag`): anything the page got wrong or didn't catch. The same log takes what the
battle reader missed and every message it read by a provisional phrasing (`vgc.video.review`).
`vgc video review` (or `GET /api/video/review`) lists them grouped, with where to look. The log is
`data/review/video.jsonl`, on the volume when deployed.

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
what the step did to the number — `42% / 58% → 40% / 60%`. The turn the game ended on shows the
result alone, because an arrow from the model's last call to it would read as the model updating
to 0%, and it did not: the game finished, which is not a prediction it made.

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
it and you get the final position on its own, without the turn that produced it above it, and
the number alone rather than the change: it is the end state, not a step.

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

That team is only for the simulator. Before the model sees the position, those sets are hidden
again, so it is shown what a closed sheet shows: species, and nothing about their sets. The Battle
page does the same all game. Its number is the position as shown, with what the battle has not
revealed left unknown. On 1,025 held-out closed-sheet games that beat both alternatives: taking
the most common set as fact, and averaging over sets drawn from other players' sheets
(phase8-findings, "which number leads on a closed sheet"). The draws are still made on the Battle
page, and their spread is the band: how much what they are holding could matter here.

The most common sets are still listed on the preview with the share of that Pokémon's sheets each
accounts for, for reference. Incineroar's most common set is **15%** of its 4,895 appearances;
Rillaboom's is 38%. A Pokémon under a quarter, or with no usage data, is flagged as wide open.
Closed-sheet answers are still weaker than open ones, so prefer an open sheet when you have one.

## What it does not do yet, and why

**Turn-by-turn move advice.** The pieces are built, but the evidence says not to show it yet
(PLAN-v5 step 4).
- **The adapter is built.** The Phase 9 policy reads a battle entered here as it reads a log
  (`vgc.policy.view.EntryView`). A parity test and a sweep over 400 replays hold the two to the same
  solver positions (`tests/test_policy_entry.py`, `scripts/analysis/entry_parity.py`).
- **The check against human games failed.** The policy's ranking of one choice over another did not
  predict how human games went (phase9-findings, "the policy's numbers against human games"). An
  action table would claim exactly that, so there is none.
- **What the page cannot enter, which a position for advice would need:** volatiles (confusion,
  Throat Chop, Encore, Perish Song), a fainted Pokémon brought back (Revival Blessing), and the
  order end-of-turn effects resolve in (Speed evidence). Ally Switch is now an entry (`swap`), but
  there is no button for it yet.

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
| `GET /api/battles`, `POST /api/battles`, `GET/DELETE /api/battles/{id}` | Saved battles; start one (yours against an open or closed sheet, or watching someone else's game); one as it stands, with its WP and gate verdicts |
| `POST /api/battles/{id}/entries`, `POST /api/battles/{id}/undo`, `GET /api/battles/{id}/at/{index}` | Append taps, pop the last, or see the battle after its first `index` taps |
| `POST /api/battles/{id}/next` | The next game of the battle's Bo3, made if it is not there yet (ending a game of one already makes it) |
| `GET /api/species/{name}` | What a sheet can say about one species: types, legal abilities and moves, and the items and natures people run, each ordered by usage |
| `POST /api/battles/{id}/read`, `DELETE /api/battles/{id}/read` | A watched battle read off its video: one JPEG frame (`video_s`, `video`) → what it appended and where the reading stands; stop, writing out what was held. A page that read the frame itself puts its lines first (`x-ocr-lines`: their length in bytes) and the server runs no OCR |
| `GET /api/video/ocr`, `GET /api/video/ocr/{det,rec}.onnx` | What the watch page needs to read frames on its GPU: the reader's model files (cached for good under their hash), its characters, layout and change threshold |
| `POST /api/video/flag`, `GET /api/video/review` | A moment marked for review; the review log, grouped |
| `GET /api/battles/{id}/trajectory` | The WP at every turn so far, with the band over drawn sets |
| `GET /api/battles/{id}/solve` | In a 1v1, the engine's answer with best play: the first call starts a background search, later calls show it deepening (depth, `leaf_mass`, the sets solved, what it assumes). With two or fewer a side on open sheets, the same call answers within 5 s: `kind`, depth 0 (quick) then 1 (searched), any forced win, and `guesses`: the answer under three guesses at their bulk, the last two landing after it |

### The number above two a side: the model and the policy combined

From your seat on open sheets, once a Pokémon has fainted and while either side has more than two
left, the Battle page leads with **Model + policy** (`vgc.web.solving.PolicySolve`). It combines
the WP model's number and the policy's one-turn value of the position by weights fitted on past
games (`POLICY_STACK`). On held-out human games after the first faint the combination beat the
model alone by 0.03–0.04 nats, including 939 games no fit had seen (phase9-findings, "the policy's
value of a position"). The model's number stays underneath, and both inputs are shown beside the
combination. It answers within 5 s through the warm solver processes. At 4v4 the model alone leads,
because the combination was not distinguishable there.

It needs **your four**: the policy's position counts the Pokémon in your back, and nothing on the
page can see them. The Your four panel logs a `bring` tap. That does what a player's request does
in the simulator: the four wait in reserve until they come out, the other two are marked not
brought, and the switch menu offers only the four. It also puts the model's input where its
player rows were trained. Until the four are marked, the page says that is what it is waiting for.

### An agent over the same API (MCP)

`python -m vgc.mcp` is an MCP server whose tools are these endpoints (`vgc.mcp.server`, `pip
install -e '.[mcp]'`). It is registered for Claude Code in `.mcp.json`: `vgc` talks to the deployed
app (the password comes from `VGC_WEB_PASSWORD` or `.env`), and `vgc-local` to
`http://127.0.0.1:8001`. An agent sees what the page sees and quotes numbers from tool results. It
is also how a deploy is checked. `solve` waits for the engine's answer to settle and returns when
each part landed (`timeline`, with the server's own `elapsed`), which is the doubles answer timed
on the machine that serves it.

### A Bo3

A new battle is a single game unless **Bo3** is chosen. Then it is game 1 of a series, and logging
its end sets up the next game: the same two teams, nothing to enter again. The series stops once a
side has two wins. Every game is an ordinary battle carrying `series: {id, game, best_of, name}`;
the score is their results.

A team is locked for the series, so the next game starts out knowing what the earlier ones showed:

- **The races.** A turn-order bound on their Stat Points from game 1 holds in game 2, so the Speed
  read, the belief panel and the engine all start from it (`belief.speed.series_pairs`), and so
  does the WP model: its evidence of who is at least as fast as whom (`belief.speed.orderings`)
  includes the earlier games' orders, except where a mover held a different Speed-bearing item then
  (a Scarf knocked off in game 1 is held again in game 2). The model's gates were scored on games
  whose evidence was one game's, so they say nothing yet about how it reads a series' worth.
- **On a hidden sheet, what was revealed**: each Pokémon's item as it started the game (a berry
  eaten in game 1 is held again), its ability, the moves it used, and the abilities it was shown not
  to have, marked `earlier game`. These do reach the model, as a reveal in the game would.

What a game carries is computed from the games before it and written onto its setup
(`web.live.carry`), so its replay is still a function of its setup and journal. A tap, an undo or a
deletion in an earlier game rewrites the later games' carry, so fixing a mis-tap in game 1 reaches
game 3. Undoing the end of a game takes the next game away again while nothing has been logged in it.

### Watching someone else's game

The third mode in the new-battle form, **Watching (both open)**, is for a game you are not in: on
stream, at an event, or a replay. Enter both players' open sheets instead of choosing a team. Both
sides are then entered the way theirs always were: HP as a percentage, Stat Points unknown. The
number is player 1's chance. Each sheet has an optional name field, and the page names each side
by it, or Player 1 and Player 2 without one. The names go on the battle's setup, so the later games
of a Bo3 keep them. In your own games the sides are You and Your opponent. It runs the open-sheet model,
and that model's gate is scored from exactly this seat (spectator rows). The Speed read and the
engine's answer in a 1v1 average over both players' hidden spreads.

**A sheet can be read off a screenshot.** Every open-sheet form has *from screenshot*: paste (⌘V),
drop or choose an image of the sheet, such as a broadcast's team list, and the form is filled from
it (`POST /api/sheet/read`, `vgc.video.sheet`, OCR by RapidOCR). Each line is matched only against
the regulation's own names. A field that doesn't read clearly is left empty and its text listed,
never filled with the nearest name, so an empty field is the one to check. On one broadcast frame
it read all 42 fields from 720p up; below that, heavy compression leaves fields empty but never
wrong. That is one broadcast's layout; other sources haven't been tried.

### The engine's answer in a 1v1

When each side has one Pokémon left, the Battle page leads with the engine's number: the value of
the position under best play on both sides, from a search over the pinned simulator
(`vgc.wp.endgame`, `vgc.web.solving`). The model's number sits underneath as the second opinion.
The two are labelled **Engine** and **Model**. The model says how positions like this have gone in
human games. The engine says what best play is worth. On 192 held-out human 1v1s the engine
predicted the winner better than the model: when it said 95% or more, that side won 104 times in 111
(phase8-findings, "the engine against how human 1v1s end" and "solver speed, built").

The search deepens one turn at a time and the page shows the deepest finished answer, with how
much of it still rests on "whoever has more HP left". A shallow answer is not faded, because in
the same check the answers resting about half on HP share still beat the model. When the two
numbers are more than 20 points apart, the page says so. On a closed sheet the
engine solves their three likeliest sets, weighted by what the battle's turn order allows, and
says how much of the belief was left unsolved. Positions it cannot set up (Substitute, Encore,
sleep, bad poison) say so instead.

### The engine's answer with two or fewer a side

Once neither side has more than two Pokémon left (a 2v2, 2v1 or 1v2) on open sheets, the Battle
page leads with the engine there too (`vgc.wp.doubles`, `vgc.web.solving`). It has 5 seconds:

- **At once** (about a second): a check for a win either side can force on this very turn, against
  every reply and through Protect, and otherwise a damage race between what is left, calibrated on
  past games. The page says "quick read".
- **Then**, side by side, a one-turn search over the two or three choices a Pokémon would consider,
  with the race past it. Each move order it finishes within 5 seconds replaces its quick value, and
  the page says "searched one turn".
- **After it**, on 5 seconds of its own: the same move orders solved again with the guessed
  spreads' non-Speed points refilled HP-first and then defences-first, their Speed kept. The page
  shows the three answers ("attacking stat first (shown) · HP first · defences first") and draws
  their range on the bar. The guess moves the answer by 0.04–0.07 on average, and on held-out games
  no shape scored better than another, or than their mean (phase8-findings, "the assumed non-Speed
  spread in doubles"). So the number stays the first guess, and the other two say how much rests
  on it.

The heaviest three move orders are solved and the rest is reported as unsolved. A forced win is
named, with its odds when a crit or a flinch is the only way out. On 674 held-out open-sheet games
this answer predicted the winner better than the model: log loss 0.384 against 0.513 (phase8-findings,
"a horizon that knows a count lead"). With a closed sheet the page says the engine has not yet beaten the model there.

Validation is recomputed whenever a team is saved and never trusted from the file: the
regulation's legality snapshot can change under a team that was legal when it was written.
