# Format facts

What is true of Pokémon Champions VGC (Regulation M-C) that the code depends on. Each entry says
where it comes from: **measured** (from a corpus here, with the number), **confirmed from play**
(stated by Jordan, often because no corpus here can measure it), or **the rules**. A fact
confirmed from play is not wrong just because a corpus doesn't show it. Usually the corpus
cannot referee it.

How the project works is in [principles](principles.md).

## What a sheet reveals

### An Open Team Sheet hides the Stat Points

A sheet shows species, item, ability, moves and nature. It does not show the 66 Stat Points (at
most 32 in a stat). So the opponent's speed order and exact damage are unknown at OTS too, not only
in closed-sheet play. OTS is never full information. The regimes differ only in *how much else* is
hidden.

- **Source:** the rules. Caught 2026-09-20, after PLAN-v2 had been written as though OTS were full
  information.
- **In code:** `unpack_sheet` ("no stats: sheets don't carry them"); the team pool's spreads are
  `impute_sp` guesses from nature and moves. Turn order and damage are the two channels that read
  the spread back ([phase8-findings](phase8-findings.md)).

### At Team Preview Only, an unknown nature is not a neutral one

The cartridge app runs at Team Preview Only, where the nature, item and ability are hidden as well.
Reading a missing nature as neutral is an assumption. A Pokémon at 85 Speed at neutral is anywhere
from 76 to 93.

- **Source:** measured. The Speed channel was 0 silently wrong over 6,000 OTS self-play battles,
  and 296 of 6,884 (4.3%) wrong with natures blanked
  ([phase8-findings](phase8-findings.md#43-wrong-in-the-only-regime-the-app-has)).
- **In code:** `speed.speed_band` spans the natures. See principle 4.

## How spreads are built

### Every build spends all 66 Stat Points

The format only enforces `sum ≤ 66` (`validate_team` errors above it, warns below), but nobody
builds under it. Unlike an EV remainder, a leftover Stat Point can always move into a defensive
stat. HP tuning (Sitrus, Leftovers, breakpoints) is done by shifting points, not dropping them.

- **Source:** confirmed from play, 2026-09-21. Not measurable here: `prior.sample_spread` spends
  every point by construction.
- **In code:** `spend_all=True` in `vgc.belief.sp`, kept as a parameter.

### A stat no move scales off gets 0 Stat Points

This holds universally, so it is a constraint, not a prior. Foul Play reads the target's Attack and
confusion damage reads the holder's own, so 0 is often actively correct.

- **Source:** confirmed from play, 2026-09-21. Measured alongside: 90.9% of weighted sheets have
  such a stat.
- **In code:** `dead_zero=True` in `sp.structural_blocks`; `prior.dead_stats`. Reads
  `offensive_stat`, not the move's category, so Body Press counts towards Defence and Foul Play
  does not rescue a dead Attack. With the previous entry, the two conventions cut the allocation
  space 233×.

### Speed is bought to a specific number, per Pokémon

Speed goes to the minimum that clears a target (to outspeed or at least tie something
consistently), not to 0 or the cap by habit. For example, a base 105 Pokémon may buy only ~4
points, because that is all it needs to sit above everything at base 100. **The frailer the
Pokémon, the further toward the top of its Speed range it is built.**

- **Source:** confirmed from play, 2026-09-21.
- **In code:** `prior.speed_classes` models the cheapest member of each class correctly. What is
  off is the weighting: `speed_prior`'s `extremes=0.35` is one constant split between 0 and the cap
  for every species, and it should depend on the species' defensive base stats. Testable with
  `scripts/analysis/spread_prior.py` against real turn orders. `speed_prior` feeds the solver and
  the endgame, so a change there moves served answers.

### HP before a defensive stat is a tendency, not a rule

Builders usually buy HP first, then Defence or Special Defence, but defence-first builds are real,
as are HP/Defence pairs chosen to survive one named move.

- **Source:** confirmed from play, 2026-09-21.
- **In code:** a hit read backwards gives one equation in HP and the defensive stat, which trade
  off almost exactly. The ordering may weight a prior; it must never prune the feasible region.
  This is why `sp.Block` holds multi-stat blocks and the 2-D region is kept rather than its two
  projections. A physical and a special hit on the same target separate them.

## Turn order

### Switch-in abilities announce in Speed order, and Trick Room inverts it

Intimidate, Drizzle, Grassy Surge and other abilities that fire on arrival announce in Speed order.
Trick Room inverts that exactly as it inverts move order. **A response is not a race:** Defiant
answering Intimidate is adjacent in the log and causally ordered, and reading it as a Speed
comparison makes Incineroar (86) look faster than Kingambit (97).

- **Source:** Speed order measured (453 of 453 pairs over 3,000 self-play battles). The Trick Room
  half is confirmed from play (2026-09-21) and by `Pokemon.getActionSpeed` in the pinned build;
  the corpus has no Trick Room pair to measure it.
- **In code:** `speed.ability_pairs` flips `sign` under Trick Room. Only
  `rules.SWITCH_IN_ABILITIES` counts.

### Lead order is not Speed evidence; start-of-turn switch order is

All four leads arrive together, so the order they are logged in says nothing about Speed. The order
in which their switch-in *abilities* announce still does (above). Start-of-turn switches arrive
in Speed order relative to each other.

- **Source:** confirmed from play, 2026-10-04.
- **In code / UI:** never ask for or read an order from lead taps. Start-of-turn switches are listed
  in screen order with no default and recorded in the order clicked
  ([battle-entry-flows](battle-entry-flows.md)).
