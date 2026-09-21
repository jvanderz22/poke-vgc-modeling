"""L4b — belief over hidden set information (Phase 8).

An Open Team Sheet carries species, item, ability, moves and nature, and **not** the 66 Stat
Points. So in every regime the opponent's speed order and exact damage are undetermined, and
until this package nothing in the pipeline inferred either: `_move_summary` was documented as
"independent of move order" and `_on_damage` wrote the defender's new HP and drew no conclusion.

The two channels this package adds are arithmetic against the pinned calc, not learning:

- `speed` — turn order bounds their Speed investment.
- `damage` — how hard a move hit bounds their offensive investment (and, at closed sheets, their
  item jointly).

`sp` then joins them, because they are not two facts: a spread is one allocation of 66 points over
six stats, so every point one channel proves they bought is a point the other's stat cannot have —
and, the part neither channel can see, a point that is not in their bulk either.

Both consume `vgc.data.observe`'s evidence log, and both are built to **abstain rather than
guess**: an observation the model cannot account for exactly is dropped and counted, because a
belief that excludes the truth is worse than one that stayed wide.
"""
