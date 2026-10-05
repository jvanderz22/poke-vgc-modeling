# Battle entry flows: the Turn Stepper — Spec

_Written 2026-10-04._ A redesign of how a turn is entered on the Battle page, for the two seats
it serves: **watching** someone else's game, and **playing** your own. It replaces the free-form
entry bar (`frontend/src/components/EntryBar.tsx`) as the default way in, and keeps it as the
escape hatch.

**Status (2026-10-04):** build-order steps 1 and 2 are in: the stepper with today's entries
(`frontend/src/components/TurnStepper.tsx`, its turn logic in `frontend/src/turn.ts`, left-is-left
in `frontend/src/screen.ts`) and the two-column page with the battle state below it
(`BattleState.tsx`). Steps 3–5 (Plan, and the backend items) are not built.

---

## The problem

Today's entry bar is **noun-first**: tap a Pokémon, choose Move / Switch / HP, fill it in. Every
action is available at every moment, so nothing tells you what comes next, and translating the
cartridge's text box into the app's vocabulary is left to you, in real time.

- **A move and its damage start from two different places**: the name button, then the HP bar.
- **Ability questions sit in their own panel** (`Questions.tsx`), away from the moment they popped.
- **There is no sense of where you are in a turn**: who has acted, who hasn't, whether the end of
  the turn has been dealt with, or whether a fainted slot still needs filling.
- **The pointer travels all over the page.** The name, the HP bar, the questions panel and the
  turn button are in four different places, so each action means hunting for the next small
  target. The stepper keeps every answer in one place, as large buttons, directly under the
  question.

## The idea

Make it **event-first**. The cartridge narrates a turn as a fixed sequence of sentences — *X used
Y!*, a bar drains, *It's super effective!*, an ability banner, *X fainted!*, then the end of the
turn. The page asks for the next sentence, offers the possible answers, and skips any question
that has only one possible answer. Entering a turn becomes answering a short series of questions
in the order the game asked them.

Both flows are the same stage machine. They differ in one extra stage (Plan, when you are
playing) and in what each stage defaults to.

The journal does not change shape. The stepper writes the same entries the entry bar writes
(`vgc.battle.entry.ENTRY_KINDS`), so replay, undo, the timeline, the MCP tools and the video
mode in [PLAN-video.md](PLAN-video.md) are all unaffected.

---

## The Turn Stepper

```
┌ Turn 4 · 2 of 4 have moved ──────────────────────── Fast ○ · Undo · ⓘ ┐
│ ✓ Incineroar used Fake Out → Amoonguss. Amoonguss on 88%.             │  turn so far, as sentences;
│ ✓ Amoonguss flinched.                                                 │  click a line to edit it
├───────────────────────────────────────────────────────────────────────┤
│  Who moved next?                                                      │  one question at a time
│  [ Rillaboom ]  [ Urshifu-Rapid-Strike ]  [ Gholdengo ]               │  only those yet to act
│  [ End of turn → ]                            something else…         │
└───────────────────────────────────────────────────────────────────────┘
```

It sits where the entry bar sits now, under the field. The field and the WP bar stay above it.
The answers are large buttons directly under the question, and the question area keeps the same
size and position from stage to stage, so the pointer does not have to travel to find the next
answer.

### The stages of a turn

| # | Stage | Asks | Skipped when |
| --- | --- | --- | --- |
| 0 | **Plan** (playing only) | Your choice for each slot. See below. | Spectating |
| 1 | **Start of turn** | Any switches, then any Mega Evolution, in the order shown. Switches go first, as they do in the game. The order of the switches is a Speed reading, relative to each other. | Default is "nothing": one click |
| 2 | **Who moved?** | Which Pokémon acted next. | One Pokémon left to act |
| 3 | **Which move?** | The move, or that it **couldn't move** (flinch, full paralysis, sleep, freeze, recharge). | Battle mode, when you confirm the plan |
| 4 | **Which target?** | The target. | It can only be one (as today) |
| 5 | **Result** | Once per Pokémon hit: HP left, **KO**, **Missed**, **Protected**, **No effect**. **Crit** is a toggle on the same screen. | Status moves with no target HP |
| 6 | **Anything else?** | Status, stat change, a consumed item, and the **backend's ability questions, here**, at the point in the turn where they popped up. | Nothing is pending; default is "nothing else" |
| → | back to 2 | | everyone has acted |
| 7 | **End of turn** | A checklist of effects that could apply: weather, terrain heal, Leftovers, burn, poison. Confirm each **in the order shown**. | None could apply |
| 8 | **Replacements** | For each fainted slot with a bench: "Who came in for X?" Required. | No faint, or nobody left |
| → | **Next turn** | Logs `turn`. | |

A spread move runs stage 5 once for each Pokémon it hit, one after another, in the order the bars
drained.

"Something else…" is always available and opens today's entry bar unchanged. Dancer,
Instruct, redirection, Revival Blessing and anything the stepper does not model are still entered
somewhere, and never block the turn.

### The order is evidence, so the stepper does not suggest it

The order moves are entered in is a **Speed reading** (`MoveEvent.order_*`, `vgc.belief.speed`),
and so are the order of start-of-turn switches and the order switch-in abilities fire in. The order
the **leads** are logged in is not: all four arrive together. If the stepper sorted "Who
moved?" by its own prediction and highlighted the first option as the default, a tired user
clicking the obvious button, or pressing Enter, would record an order the app made up, which is exactly what `entry.py` rule 2 forbids.

So:

- **"Who moved?" is listed in screen position**, always: the top row left to right, then the
  bottom row left to right, as the viewer sees the TV (see "Left is left" below). It is never
  sorted by predicted Speed. **No button is highlighted as the default on this stage, and Enter does nothing.** The
  buttons stay in the same place from turn to turn, so the mouse is never tricked by a reordering.
- The Speed read stays in the speed strip above, where it already is, as information rather than
  a pre-filled answer.
- The same applies to stage 7: end-of-turn effects are listed in a fixed order, and you confirm
  them in the order you saw them.

### Left is left

Positions are **always as the viewer sees the screen**. Option `1` is the Pokémon on the left, and
the opponent's two are labelled left and right as they appear on the TV, not by the side's own
field slot. The page draws the field the same way, so the Pokémon on the left of the TV is on the
left of the page, the same for every choice of targets, and the same when watching.

The journal still records field slots (`{side, slot}`). Converting between a screen position and
a slot is done in one function shared by the stepper and the field. It is the only place to check
against footage, and if the cartridge mirrors the opponent's side, only that function changes.

### Mouse first, keyboard too

**The mouse is the primary input.** Every stage can be completed with clicks alone, and nothing
is ever reachable only from the keyboard. Every button also has a keyboard shortcut, for when a
hand is free or the stream is moving quickly.

- **The Result stage** shows the HP pad as buttons, as it does today. Beside it is a number field,
  and typing digits anywhere on the page goes into it, so `45↵` logs 45% for their Pokémon and
  45 HP for yours without clicking the field first.
- **Buttons carry no key labels**, so the stepper stays easy to scan with the mouse. The shortcuts
  are in a popover instead.

#### The shortcuts popover

An **ⓘ icon button** at the right of the stepper's header, after Fast and Undo, opens a popover
attached to it that lists the shortcuts:

| Key | Does |
| --- | --- |
| `1`–`9` | Pick an option, numbered left to right and top to bottom |
| `↵` | Accept the highlighted default, if the stage has one |
| digits, then `↵` on a Result stage | Type the HP: 45% for theirs, 45 HP for yours |
| `k` · `m` · `p` · `n` | KO · Missed · Protected · No effect |
| `c` | Toggle crit |
| `.` | "Nothing else": go to the next stage |
| `Backspace` | Back one stage within the current action. Logs nothing, undoes nothing |
| `⌘Z` | Undo the last entry (today's Undo) |
| `Esc` | Something else: the free-form entry bar |
| `f` | Toggle Fast mode |
| `?` | Open or close this popover |

How it behaves:

- **Opening and closing.** It opens on a click of the icon or on `?`. It closes on a second
  click, a click outside it, `?` or `Esc`. While the popover is open, `Esc` only closes it and
  does not also open the entry bar.
- **It does not interrupt the turn.** The popover is non-modal and does not take focus away from
  the stepper, so the shortcuts keep working while it is open and you can try one as you read it.
  It sits below the icon, over the "turn so far" list, never over the answer buttons.
- **It teaches the numbering.** While it is open, each answer button shows its number in a small
  badge. The badges disappear when it closes.
- **Accessibility.** The icon is a real `<button>` with `aria-label="Keyboard shortcuts"` and
  `aria-expanded`, and the popover is linked to it with `aria-controls`. The icon has a "Keyboard
  shortcuts" tooltip on hover.
- **Rules for the keys.** They are ignored while a text field has focus, except `↵` and `Esc`.
  The HP field on the Result stage is the exception: it is where typed digits are meant to go.

### The "turn so far" list

Each entry logged this turn is shown as a sentence, written the way the cartridge would write it
("Rillaboom used Grassy Glide → Gholdengo. Gholdengo on 61%.").

- **Clicking a line** walks the battle back to just before it (`battle_at`), and the first new
  entry continues from there, as the timeline does today.
- **A contradiction** (`view.contradictions`) is shown on the line that caused it, rather than as
  a banner at the top of the page.

---

## Page layout

The Battle page is built for a laptop screen, so it uses the width. Today everything is one
column, at most 1040px wide (`main` in `styles.css`), stacked in this order: the header and the
number, the gate banner, the questions, the field, the entry bar, Your four, Leads, the timeline,
the belief and "Worked out for you". You scroll past the board to reach the place where you
enter things, and past both to reach the history.

### Where things go

```
┌ Bo3 vs Kai · open sheets · turn 4 ───────────────────────────────── Undo · All battles ┐
├──────────────────────────────────────────────┬─────────────────────────────────────────┤
│ TURN STEPPER                   Fast · Undo ⓘ │ WIN CHANCE                               │
│ ✓ Incineroar used Fake Out → Amoonguss.      │  You  62%   Model + policy · rough guide ⓘ│
│   Amoonguss on 88%.                          │  ▁▂▃▅▆▅▆▇  each turn so far (click one)   │
│ ✓ Amoonguss flinched.                        │                                           │
│                                              │ ON THE FIELD                              │
│ Who moved next?                              │  Gholdengo  61%       Amoonguss  88%  slp │
│ [ Rillaboom ] [ Urshifu ] [ Gholdengo ]      │  ── Rillaboom → Gholdengo: not decided ── │
│ [ End of turn → ]          something else…   │  Rillaboom 143/207    Incineroar  100%    │
├──────────────────────────────────────────────┴─────────────────────────────────────────┤
│ BATTLE STATE                                                                            │
│  Your four                              │  Their six                                    │
│  each: HP, item, ability, boosts,       │  each: seen / brought / KO / not brought,     │
│  status, moves used                     │  item ?, ability ?, what is still open        │
│  your side's screens, Tailwind          │  their side's screens, Tailwind · weather …   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

- **The top row is where you work.** The stepper is on the left, the widest column, where your eye
  lands first. On the right are the number, the WP curve by turn, and a compact view of the four
  Pokémon on the field with their HP and the Speed read. The stepper's questions already name the
  Pokémon and HP they ask about ("Amoonguss is on 88%. What is it on now?"), so nothing below the
  top row is needed to answer them.
- **The full battle state moves down**, into one wide row with two halves: your side and theirs.
  It holds what changes slowly or matters when planning rather than entering: both full rosters,
  what is known about each set, boosts, status, the side conditions, and what is still open about
  their six (today's "What is still open about their six" panel, folded into their half).
- **Clicking a Pokémon on the field answers the stepper.** On "Who moved?" it picks that Pokémon,
  and on "Which target?" it picks the target. This replaces today's click-to-select on the field,
  which started the free-form entry bar.
- **Team preview uses the same spot.** Before turn 1, the stepper's place is taken by two preview
  stages: Your four, then Leads. They are today's two panels, moved into the place where you work.
- **Arrivals are entered before their questions are answered.** Leads, start-of-turn switches and
  replacements arrive together, so their ability questions wait beside the stage rather than in
  front of it. Answering each as it was entered would record the entry order as the Speed order.
- **The timeline becomes the curve** in the win chance panel. Clicking a turn walks the battle
  back to it, as the timeline does now.
- **Columns.** On the battle page `main` widens to fill the screen up to about 1600px, with 16px
  gutters. The top row is a grid of `minmax(520px, 3fr) minmax(380px, 2fr)`. Below about 1100px
  it stacks into one column in the order stepper, win chance, battle state, which is still the
  order of what matters.

Watching uses the same layout, with the sides labelled P1 and P2 and the number as P1's chance.

### What is dropped, and what moves behind an ⓘ

The page should show what helps you play or follow the game. How the number was made is for when
you go looking for it. Each number keeps its label and its verdict, because the rule in PLAN.md
does not bend for layout: *a model that misses a gate is never shown without the verdict beside
it.* The verdict becomes a short tag beside the number, and the reasoning moves into a popover.

| Today | New page |
| --- | --- |
| Gate banner: full sentences, log loss, held-out row counts | A tag beside the number: **verified**, **rough guide** or **not verified**. Its ⓘ popover holds today's sentence. Row counts and log loss are dropped |
| WP bar: "averaged over k drawn sets", "band from k draws", "n of their six still open", regime in the tooltip | The number and its band. The band's caption is "depends on their sets". The rest is dropped |
| Engine row: depth, `leaf_mass`, sets solved, what it assumes, "quick read" / "searched one turn" | The engine's number with its label, and "still thinking…" while it deepens. The three bulk guesses stay as the range drawn on the bar, without the text. Depth and the assumptions go in the ⓘ popover |
| Model + policy: the combined number with both inputs beside it | The combined number. The two inputs go in the popover |
| Engine vs model disagreement note | Kept: it changes how much to trust the number, and it is one line |
| Header: "{n} taps", mode label | The mode label only. The tap count is dropped |
| "Worked out for you" panel | Dropped as a panel. Each derived fact appears as a dim line under the entry in "turn so far" that caused it |
| Speed strip with Speed ranges in the tooltip | Kept as it is, inside On the field |

The ⓘ popover beside the number behaves like the shortcuts popover: it is attached to its icon,
non-modal, and closed by a second click, a click outside it or `Esc`.

---

## Flow 1: Watching

You are entering what you see on a stream, at an event or in a replay. Both sides are entered as
percentages, the sides are named P1 and P2, and there is no Plan stage.

**Full detail by default, with a Fast mode.** You watch live and paused games about equally, so
the default asks every stage, and **Fast mode** (the toggle in the header, or `f`; remembered per
battle in the browser) relaxes
it for when the stream is running away from you:

| | Normal | Fast |
| --- | --- | --- |
| Result | Required | A **Didn't catch it** button (or `↵` on an empty field): the move is logged, the HP is not |
| Anything else? | Asked after every action | Asked only when the backend raised a question |
| End of turn | Item-by-item checklist | One question: "Anything change at the end of the turn?" Default is no |
| Catch-up | | **"Skip to end of turn"** marks the rest of the turn unentered and goes to stage 7 |

Skipping a detail is better than falling a turn behind. A move logged without its HP still shows
the move was used and where it came in the order, which matters more than any one HP reading.

## Flow 2: Playing

Each turn has two phases, the same two the game puts you through.

### A. Plan, while you are on the command screen

```
Your choices · turn 4
 Incineroar   [Fake Out] [Flare Blitz] [Parting Shot] [Protect]   [Switch…]   → target
 Rillaboom    [Grassy Glide] [Wood Hammer] [U-turn] [Protect]     [Switch…]   → target
                                                      [ Locked in · watch the turn → ]
```

- Your left slot first, then your right, in the same order as the in-game menu. For each, click
  a move, then a target if the move needs one, or click **Switch…** and pick a Pokémon. Mega is a
  toggle on the slot when it is available. The shortcuts work here too: `1`–`4` for a move, `s`
  for Switch.
- **It is only your choices.** It does not ask what you think the opponent will do.
- **The plan stays in the browser and is never written to the journal.** The journal records what
  happened, and a choice that never happened is not part of the battle. A plan line that does not
  happen is simply never confirmed.
- The Plan stage can be skipped (click **Locked in** with nothing chosen). Resolve then works exactly as watching
  does, except that your own HP is exact.

### B. Resolve, while the turn plays

The stepper runs stages 1–8 as when watching, with two differences.

**Your planned actions are one-click confirms.** On "Who moved?", each of your Pokémon shows its
plan:

```
  Who moved next?
  [ Gholdengo ]   [ Amoonguss ]
  [ Incineroar: Fake Out → Amoonguss ✓ ]   [ Rillaboom: Grassy Glide → Gholdengo ✓ ]
```

Clicking Incineroar logs the planned move and goes straight to Result. If it did not go to plan,
**Didn't go to plan** on the Result stage (or `Backspace`) reopens "Which move?" for that Pokémon,
which offers:

- a different move: Encore, Choice lock, a redirected or retargeted move;
- **couldn't move**: flinch, full paralysis, sleep, freeze, recharge.

A planned switch is confirmed on stage 1, Start of turn.

**Your own HP is a real number.** Result asks for the HP left as the cartridge shows it ("Rillaboom
is on 143/207. What is it on now?"), typed into the number field, which has focus as soon as
the stage opens, and confirmed with `↵` or **Log**. Theirs stays a percentage. This
is the split `HpPad` already makes; the stepper only changes how you get there.

---

## What the backend needs

The first slice can ship without these. Each one makes the stepper's output more complete.

1. **A result on a move.** `move` gets an optional `result: "hit" | "miss" | "protected" |
   "immune" | "failed"`. Today a move with no damage after it already reads as no damage, so the
   first slice logs Missed and Protected as a bare `move`. The field lets the belief tell a miss
   from a resisted hit, and gives the timeline the right sentence.
2. **A `cant` entry** `{side, slot, reason}` for a Pokémon that took its turn without moving
   (flinch, full paralysis, sleep, freeze, recharge). "X couldn't move" appears in the Speed order,
   so it is order evidence just as a move is. To start with it changes no state and only marks the
   Pokémon as having acted. Using it as Speed evidence is a follow-up.
3. **Turn progress in the view.** `BattleSession` keeps no battle state beyond the selection, and
   the stepper should not either. The backend adds `view.turn_progress`: who has acted since the
   last `turn` entry, which slots are fainted and waiting for a replacement, and the move awaiting
   its results. The stage then follows from the view, so a reload resumes mid-turn on the right
   question. Only the Plan, Fast mode and the half-finished action in front of you are browser
   state.
4. **End-of-turn candidates.** `vgc.battle.rules` produces the stage 7 checklist from the state,
   each with the HP it expects: "Sandstorm: Amoonguss 88% → about 82%". The user confirms each one
   (with the expected HP as the default) or corrects it. This is the order of end-of-turn effects
   that [web-app.md](web-app.md) lists as something the page cannot enter yet. Each confirm is an
   ordinary `damage` or `heal` entry.

The ability questions need no backend change. They are already raised in the order things
happened, and only their placement moves into stage 6.

## Build order

1. **The stepper with today's entries.** Stages 1–8, the "turn so far" list, inline questions,
   Fast mode, and the entry bar behind "something else…". Keyboard shortcuts and their popover
   can come in the same slice or straight after it. The stage is worked out in the
   browser from the journal since the last `turn` entry.
2. **The page layout.** The two-column top row, the battle state moved down, the timeline folded
   into the WP curve, and the technical details moved into popovers. This is all frontend work and
   could land before the stepper, with the entry bar in the stepper's place.
3. **The Plan stage.** Browser state only.
4. **Backend items 1–3**, after which the stepper reads its stage from `turn_progress` instead of
   working it out in the browser.
5. **Backend item 4**, the end-of-turn checklist.

## How to tell it is better

Measure it rather than assuming it. `scripts/analysis/entry_parity.py` already turns a replay
into entries. A sibling script can count the **clicks** each design needs to enter the same
held-out replays, along with how far the pointer travels between them. Report the median per turn
and the worst turns. Then enter two real games by hand, one watched and one played, with the mouse
alone, and check that "something else…" was rare. If it keeps being used for the same thing,
that thing needs a stage.

## Open questions

- **Saving plans for review later.** The plan is never saved, by design. If comparing plan against
  outcome would be useful when reviewing a game, that would need a `choice` entry that changes
  nothing, kept like `note`. It is left out until it is asked for.
