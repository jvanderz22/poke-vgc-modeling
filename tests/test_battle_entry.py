"""Entering a battle by hand: the journal is the battle, and each tap's consequences follow.

Four things are worth pinning, and they are the four that would be quietly wrong.

  * **Replay is a pure function of `(setup, journal)`.** Undo, walking back to turn 6 and a WP per
    step are all the same operation — replaying a prefix — so a state that depends on anything
    else takes all three down together.
  * **The Intimidate chain lands exactly once.** The drop is not applied by the Intimidate; it is
    handed to each target, because what the drop *does* depends on an ability nobody has
    established. Applying it eagerly *and* again through the answer is the bug the shape prevents.
  * **A Speed claim is only made about a moment the app was told about.** An outcome the app
    settled on its own has no observed ordering, and recording one would invent evidence.
  * **A question is only asked when there is something to learn.** Every avoidable pop-up is a
    tap taken out of a game being played on a timer, which is the constraint the UI exists under.

The opposing species here are chosen because their ability lists genuinely split in Reg M-C —
Kingambit is Defiant / Pressure / Supreme Overlord, Milotic is Competitive / Cute Charm / Marvel
Scale, Archaludon is Stalwart / Stamina / Sturdy — so the menus are doing real work rather than
confirming a foregone conclusion.
"""

from __future__ import annotations

import json

import pytest

from vgc.battle import entry, rules

# Legal in Reg M-C, and each one's ability list splits on something this module models.
THEIRS = ["Kingambit", "Milotic", "Archaludon", "Torkoal", "Gholdengo", "Pelipper"]


@pytest.fixture
def battle(reg, team_text):
    """Your own six from the team you built — so item, ability, moves and real stats are known —
    against six species and nothing else, which is what Team Preview Only means."""
    return entry.Battle(reg, {"perspective": "p1",
                              "mine": entry.from_team(reg, team_text("valid_basic")),
                              "theirs": [{"species": s} for s in THEIRS]})


def lead(b, *pairs):
    for side, slot, species in pairs:
        b.append({"kind": "lead", "side": side, "slot": slot, "species": species})
    return b.rp


def answer_all(b, option=None):
    for q in list(b.rp.questions):
        b.append({"kind": "answer", "question": q.id, "option": option})
    return b.rp


# --- team preview ---------------------------------------------------------------------------

def test_setup_knows_your_six_and_only_their_species(battle):
    p1, p2 = battle.rp.state.sides["p1"], battle.rp.state.sides["p2"]
    assert [m.species for m in p2.mons] == THEIRS
    assert p1.team_size == p2.team_size == 6
    inc = next(m for m in p1.mons if m.species == "Incineroar")
    assert (inc.ability, inc.item, inc.ability_source) == ("intimidate", "sitrusberry", "own")
    assert inc.stats and inc.hp_max == inc.stats["hp"]
    # Theirs: six names. No item, no ability, no stats.
    assert all(m.ability is None and m.item is None and m.stats is None for m in p2.mons)
    assert not p2.sheet


def test_open_team_sheets_is_the_same_battle_with_more_filled_in(reg, team_text):
    theirs = [{"species": s, "ability": rules.candidate_abilities(reg, s)[0],
               "item": "Sitrus Berry", "moves": ["protect"], "nature": "Adamant"} for s in THEIRS]
    b = entry.Battle(reg, {"perspective": "p1", "mine": entry.from_team(reg, team_text("valid_basic")),
                           "theirs": theirs})
    side = b.rp.state.sides["p2"]
    assert side.sheet and side.mons[0].ability_source == "sheet"
    # ...and the spread is still hidden, which is the whole reason the belief package exists.
    assert all(m.stats is None for m in side.mons)


# --- asking, and not asking -------------------------------------------------------------------

def test_a_known_intimidate_still_asks__because_when_it_fired_is_speed_evidence(battle):
    rp = lead(battle, ("p2", 0, "Gholdengo"), ("p2", 1, "Milotic"), ("p1", 0, "Incineroar"))
    q = [x for x in rp.questions if x.side == "p1"]
    assert len(q) == 1 and q[0].kind == "switch_in"
    # One option, because you built it and know the ability — so the UI shows a confirm, not a
    # menu. It is asked at all because *when* it announced is a Speed comparison.
    assert q[0].forced and len(q[0].outcomes) == 1 and q[0].outcomes[0].ability == "intimidate"
    # And nothing has landed yet: the drop waits for the tap that says when.
    assert rp.state.at("p2", 0).boosts.get("atk", 0) == 0


def test_a_target_whose_ability_cannot_matter_is_never_asked_about(battle):
    """Gholdengo has one legal ability and it does not answer a drop, so the drop simply lands.
    One tap for the Intimidate, none for the target — which is the point."""
    lead(battle, ("p2", 0, "Gholdengo"), ("p1", 0, "Incineroar"))
    q = next(x for x in battle.rp.questions if x.side == "p1")
    rp = battle.append({"kind": "answer", "question": q.id, "option": 0})
    assert rp.state.at("p2", 0).boosts["atk"] == -1
    assert not rp.questions


def test_the_drop_is_handed_to_each_target_and_lands_once(battle):
    rp = lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    q = next(x for x in rp.questions if x.side == "p1" and x.kind == "switch_in")
    rp = battle.append({"kind": "answer", "question": q.id, "option": 0})

    # One question per opposing active — both ability lists split — and no drop applied yet.
    drops = [x for x in rp.questions if x.kind == "stat_drop"]
    assert {x.species for x in drops} == {"Kingambit", "Milotic"}
    assert all(rp.state.at("p2", s).boosts.get("atk", 0) == 0 for s in (0, 1))
    assert all(x.source["species"] == "Incineroar" for x in drops)

    gambit = next(x for x in drops if x.species == "Kingambit")
    plain = next(i for i, o in enumerate(gambit.outcomes) if o.label.startswith("atk -1"))
    rp = battle.append({"kind": "answer", "question": gambit.id, "option": plain})
    assert rp.state.at("p2", 0).boosts["atk"] == -1        # exactly once

    milo = next(x for x in rp.questions if x.species == "Milotic")
    comp = next(i for i, o in enumerate(milo.outcomes) if o.ability == "competitive")
    rp = battle.append({"kind": "answer", "question": milo.id, "option": comp})
    mon = rp.state.at("p2", 1)
    assert mon.boosts == {"atk": -1, "spa": 2} and mon.ability == "competitive"
    assert not [x for x in rp.questions if x.kind == "stat_drop"]


def test_defiant_answers_the_drop_and_pins_the_ability_that_answered(battle):
    lead(battle, ("p2", 0, "Kingambit"), ("p1", 0, "Incineroar"))
    q = next(x for x in battle.rp.questions if x.kind == "switch_in" and x.side == "p1")
    rp = battle.append({"kind": "answer", "question": q.id, "option": 0})
    drop = next(x for x in rp.questions if x.species == "Kingambit" and x.kind == "stat_drop")
    assert any("Defiant" in o.label for o in drop.outcomes)
    defiant = next(i for i, o in enumerate(drop.outcomes) if o.ability == "defiant")
    rp = battle.append({"kind": "answer", "question": drop.id, "option": defiant})
    mon = rp.state.at("p2", 0)
    assert mon.boosts["atk"] == 1                          # −1 from Intimidate, +2 from Defiant
    assert mon.ability == "defiant" and mon.ability_source == "revealed"


def test_not_sure_closes_the_question_and_concludes_nothing(battle):
    lead(battle, ("p2", 0, "Kingambit"), ("p1", 0, "Incineroar"))
    q = next(x for x in battle.rp.questions if x.side == "p1")
    rp = battle.append({"kind": "answer", "question": q.id, "option": None})
    assert q.id not in [x.id for x in rp.questions]
    assert rp.state.at("p2", 0).boosts.get("atk", 0) == 0
    assert rp.state.at("p1", 0).ability_ruled_out == set()
    assert rp.state.at("p1", 0).ability == "intimidate"     # from your own sheet, not from this


def test_silence_settles_the_ability_when_it_leaves_one_candidate(battle):
    """Incineroar is Intimidate or Blaze here. Nothing announcing is not an absence of
    information — Intimidate would have said so — so silence names Blaze outright."""
    lead(battle, ("p2", 0, "Kingambit"))
    b2 = entry.Battle(battle.reg, dict(battle.setup, theirs=[{"species": "Incineroar"}] +
                                       [{"species": s} for s in THEIRS[:5]]))
    b2.append({"kind": "lead", "side": "p2", "slot": 0, "species": "Incineroar"})
    q = b2.rp.questions[0]
    quiet = next(i for i, o in enumerate(q.outcomes) if o.label.startswith("nothing announced"))
    rp = b2.append({"kind": "answer", "question": q.id, "option": quiet})
    assert rp.state.sides["p2"].mons[0].ability == "blaze"


def test_silence_rules_out_rather_than_settles_when_two_candidates_are_left(battle):
    """Torkoal is Drought, Shell Armor or White Smoke. Only Drought announces on arrival, so
    silence rules that one out and leaves two — narrower, not settled."""
    lead(battle, ("p2", 0, "Torkoal"))
    q = battle.rp.questions[0]
    quiet = next(i for i, o in enumerate(q.outcomes) if o.label.startswith("nothing announced"))
    assert battle.rp.questions[0].outcomes[quiet].excludes == frozenset({"drought"})
    rp = battle.append({"kind": "answer", "question": q.id, "option": quiet})
    mon = rp.state.sides["p2"].mons[3]
    assert mon.ability is None and mon.ability_ruled_out == {"drought"}
    assert set(rules.still_possible(battle.reg, mon)) == {"shellarmor", "whitesmoke"}


def test_a_weather_setter_announcing_changes_the_field_and_names_itself(battle):
    lead(battle, ("p2", 0, "Torkoal"))
    q = battle.rp.questions[0]
    drought = next(i for i, o in enumerate(q.outcomes) if o.ability == "drought")
    rp = battle.append({"kind": "answer", "question": q.id, "option": drought})
    assert rp.state.weather == "sunnyday"
    assert rp.state.sides["p2"].mons[3].ability == "drought"


# --- Speed evidence ---------------------------------------------------------------------------

def test_a_switch_in_ability_the_app_was_told_about_is_speed_evidence(battle):
    lead(battle, ("p2", 0, "Gholdengo"), ("p1", 0, "Incineroar"))
    q = next(x for x in battle.rp.questions if x.side == "p1")
    rp = battle.append({"kind": "answer", "question": q.id, "option": 0})
    ev = [e for e in rp.state.ability_log if e.ability == "intimidate"]
    assert len(ev) == 1 and ev[0].switch_in is True


def test_a_response_is_not_a_race(battle):
    """Competitive answers an Intimidate. It is caused by the drop, not racing it, so it must
    never be recorded as a switch-in ordering — `vgc.belief.speed` reads only the first kind."""
    lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Milotic"))
    q = next(x for x in battle.rp.questions if x.side == "p1" and x.kind == "switch_in")
    rp = battle.append({"kind": "answer", "question": q.id, "option": 0})
    drop = next(x for x in rp.questions if x.species == "Milotic" and x.kind == "stat_drop")
    comp = next(i for i, o in enumerate(drop.outcomes) if o.ability == "competitive")
    rp = battle.append({"kind": "answer", "question": drop.id, "option": comp})
    ev = next(e for e in rp.state.ability_log if e.ability == "competitive")
    assert ev.switch_in is False


def test_an_ability_the_app_settled_on_its_own_makes_no_speed_claim(battle):
    """Stamina fires in answer to a hit. Even settled silently, it claims nothing about Speed."""
    lead(battle, ("p1", 0, "Garchomp"), ("p2", 0, "Archaludon"))
    answer_all(battle)
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Earthquake", "spread": True})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 0, "pct": 70})
    hit = next(x for x in rp.questions if x.kind == "on_hit")
    stamina = next(i for i, o in enumerate(hit.outcomes) if o.ability == "stamina")
    rp = battle.append({"kind": "answer", "question": hit.id, "option": stamina})
    mon = rp.state.at("p2", 0)
    assert mon.boosts["def"] == 1 and mon.ability == "stamina"
    assert all(not e.switch_in for e in rp.state.ability_log)


# --- moves and damage ---------------------------------------------------------------------------

def test_a_move_and_the_damage_it_did_become_one_damage_event(battle):
    lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Gholdengo"))
    answer_all(battle)
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Flare Blitz",
                   "target": {"side": "p2", "slot": 0}})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 0, "pct": 45})
    assert len(rp.state.damage_log) == 1
    ev = rp.state.damage_log[0]
    assert (ev.attacker, ev.target, ev.move) == ("Incineroar", "Gholdengo", "flareblitz")
    assert (ev.hp_before, ev.hp_after) == (1.0, 0.45)
    assert ev.attacker_item == "sitrusberry" and ev.attacker_ability == "intimidate"
    assert ev.exact is False            # theirs is a percentage, and the belief must know that
    assert rp.state.moves_log[0].move == "flareblitz"


def test_your_own_hp_is_exact_and_theirs_is_not(battle):
    lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Gholdengo"))
    answer_all(battle)
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "move", "side": "p2", "slot": 0, "move": "Make It Rain",
                   "target": {"side": "p1", "slot": 0}})
    mine = battle.rp.state.at("p1", 0)
    rp = battle.append({"kind": "damage", "side": "p1", "slot": 0, "hp": mine.hp_max - 40})
    assert rp.state.damage_log[0].exact is True
    assert rp.state.damage_log[0].hp_max == mine.hp_max


def test_a_faint_is_right_censored_and_takes_the_pokemon_off_the_field(battle):
    lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Gholdengo"))
    answer_all(battle)
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Flare Blitz",
                   "target": {"side": "p2", "slot": 0}})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 0, "fainted": True})
    assert rp.state.damage_log[0].fainted is True
    assert rp.state.at("p2", 0) is None
    assert next(m for m in rp.state.sides["p2"].mons if m.species == "Gholdengo").state == "fainted"
    # A KO is an inequality, not an equality: nothing may ask it what happened next.
    assert not [x for x in rp.questions if x.kind == "on_hit"]


# --- items ---------------------------------------------------------------------------------

def test_a_grassy_seed_is_offered_when_the_terrain_comes_up(battle):
    lead(battle, ("p2", 0, "Kingambit"))
    answer_all(battle)
    rp = battle.append({"kind": "field", "what": "terrain", "value": "Grassy Terrain", "on": True})
    seed = next(x for x in rp.questions if x.kind == "terrain")
    assert seed.outcomes[0].item == "grassyseed" and seed.outcomes[0].consumed
    rp = battle.append({"kind": "answer", "question": seed.id, "option": 0})
    mon = rp.state.at("p2", 0)
    assert mon.boosts["def"] == 1 and mon.lost_item == "grassyseed" and mon.item == ""


def test_a_seed_is_not_offered_to_a_pokemon_known_to_hold_something_else(battle):
    """Your own Incineroar holds a Sitrus Berry, so there is nothing to ask it."""
    lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Kingambit"))
    answer_all(battle)
    rp = battle.append({"kind": "field", "what": "terrain", "value": "Grassy Terrain", "on": True})
    assert {x.species for x in rp.questions if x.kind == "terrain"} == {"Kingambit"}


def test_a_terrain_setter_arriving_cues_the_seeds_on_everyone(battle):
    lead(battle, ("p2", 0, "Kingambit"), ("p1", 0, "Rillaboom"))
    q = next(x for x in battle.rp.questions if x.side == "p1" and x.kind == "switch_in")
    rp = battle.append({"kind": "answer", "question": q.id, "option": 0})
    assert rp.state.terrain == "grassyterrain"
    assert {x.species for x in rp.questions if x.kind == "terrain"} == {"Kingambit"}


def test_switching_into_standing_terrain_asks_the_arrival_too(battle):
    """Two questions for one arrival: what it announced, and whether the terrain popped its
    item — the seed fires on arriving into standing terrain exactly as it does on the terrain."""
    lead(battle, ("p2", 0, "Kingambit"))
    answer_all(battle)
    battle.append({"kind": "field", "what": "terrain", "value": "Grassy Terrain", "on": True})
    answer_all(battle)
    rp = lead(battle, ("p2", 1, "Torkoal"))
    assert {x.kind for x in rp.questions} == {"switch_in", "terrain"}
    assert all(x.species == "Torkoal" for x in rp.questions)


# --- the journal ------------------------------------------------------------------------------

def test_undo_is_popping_the_tail_and_leaves_no_trace(battle):
    lead(battle, ("p1", 0, "Incineroar"))
    before = battle.rp.state.observation()
    battle.append({"kind": "lead", "side": "p2", "slot": 0, "species": "Gholdengo"})
    rp = battle.undo()
    assert rp.state.observation() == before and len(battle.journal) == 1


def test_walking_back_to_a_turn_replays_a_prefix(battle):
    lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Gholdengo"))
    answer_all(battle)
    battle.append({"kind": "turn", "n": 1})
    at_one = len(battle.journal)
    battle.append({"kind": "turn", "n": 2})
    assert battle.at(at_one).state.turn == 1
    assert battle.at(len(battle.journal)).state.turn == 2
    # ...and the whole thing is a function of the journal alone.
    twin = entry.Battle(battle.reg, battle.setup, battle.journal)
    assert twin.rp.state.observation() == battle.rp.state.observation()


def test_question_ids_survive_an_append(battle):
    # Both announce on arrival — Drought and Drizzle — so both raise a question, and Milotic,
    # whose three abilities all say nothing here, would raise none.
    lead(battle, ("p2", 0, "Torkoal"), ("p2", 1, "Pelipper"))
    first = [q.id for q in battle.rp.questions]
    assert len(first) == 2
    battle.append({"kind": "lead", "side": "p1", "slot": 0, "species": "Incineroar"})
    assert [q.id for q in battle.rp.questions][:2] == first


def test_an_impossible_entry_is_an_error_and_not_a_silently_wrong_state(battle):
    rp = battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Fake Out"})
    assert rp.errors and "nothing active" in rp.errors[0]
    assert not rp.state.moves_log
    assert len(battle.append({"kind": "nonsense"}).errors) == 2


def test_a_battle_round_trips_through_json(battle):
    lead(battle, ("p1", 0, "Incineroar"), ("p2", 0, "Kingambit"))
    answer_all(battle, option=0)
    battle.append({"kind": "turn", "n": 1})
    twin = entry.Battle.from_json(battle.reg, json.loads(battle.dumps()))
    assert twin.rp.state.observation() == battle.rp.state.observation()
    assert [q.id for q in twin.rp.questions] == [q.id for q in battle.rp.questions]


def test_ally_switch_trades_the_two_places(battle):
    """A swap left out crosses every target and turn-order read after it, so it is a tap."""
    lead(battle, ("p2", 0, "Gholdengo"), ("p2", 1, "Milotic"))
    rp = battle.append({"kind": "swap", "side": "p2", "slot": 0})
    assert rp.state.at("p2", 0).species == "Milotic" and rp.state.at("p2", 1).species == "Gholdengo"
    battle.append({"kind": "move", "side": "p2", "slot": 1, "move": "Make It Rain", "target": None, "spread": True})
    assert battle.named_journal()[-1]["species"] == "Gholdengo"


def test_your_four_puts_the_back_in_reserve(battle, team_text):
    """Marking the four you brought does what a player's request does: the four are in reserve until
    they come out, the other two were not brought, and the switch menu offers only the four."""
    mine = [m["species"] for m in entry.from_team(battle.reg, team_text("valid_basic"))]
    rp = battle.append({"kind": "bring", "side": "p1", "species": mine[:4]})
    side = rp.state.sides["p1"]
    assert side.brought_known and side.brought == mine[:4]
    assert [m.state for m in side.mons] == ["bench"] * 4 + ["not_brought"] * 2
    assert [m.species for m in rp.state.bench("p1")] == mine[:4]
    rp = battle.append({"kind": "bring", "side": "p1", "species": mine[:3]})
    assert rp.errors and "brings 4" in rp.errors[-1]


# --- where the turn has got to ----------------------------------------------------------------

def _doubles(battle):
    lead(battle, ("p1", 0, "Incineroar"), ("p1", 1, "Garchomp"),
         ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    answer_all(battle)
    return battle.append({"kind": "turn", "n": 1})


def test_a_spread_move_owes_a_result_for_everyone_it_could_have_hit(battle):
    """Earthquake hits the ally too, so it owes three results; damage pays one each, and a target
    it did nothing to is paid by saying how."""
    _doubles(battle)
    rp = battle.append({"kind": "move", "side": "p1", "slot": 1, "move": "Earthquake", "spread": True})
    owed = rp.awaiting["targets"]
    assert {(t["side"], t["slot"]) for t in owed} == {("p2", 0), ("p2", 1), ("p1", 0)}
    battle.append({"kind": "damage", "side": "p2", "slot": 0, "pct": 30})
    rp = battle.append({"kind": "damage", "side": "p1", "slot": 0, "pct": 70})
    assert rp.awaiting["targets"] == [{"side": "p2", "slot": 1}]
    battle.undo(), battle.undo(), battle.undo()
    rp = battle.append({"kind": "move", "side": "p1", "slot": 1, "move": "Earthquake", "spread": True,
                        "results": [{"side": "p2", "slot": 1, "result": "protected"}]})
    assert {(t["side"], t["slot"]) for t in rp.awaiting["targets"]} == {("p2", 0), ("p1", 0)}


def test_a_miss_settles_the_move_and_is_not_a_hit_for_nothing(battle):
    _doubles(battle)
    rp = battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Flare Blitz",
                        "target": {"side": "p2", "slot": 0}, "result": "miss"})
    assert rp.awaiting is None and rp.state.damage_log == [] and len(rp.state.moves_log) == 1
    rp = battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Flare Blitz",
                        "target": {"side": "p2", "slot": 0}, "result": "dodged"})
    assert any("unknown result" in e for e in rp.errors)


def test_a_pokemon_that_could_not_move_has_had_its_turn(battle):
    """A flinch changes nothing on the field, but the Pokémon has acted and the turn has begun."""
    rp = _doubles(battle)
    assert rp.acted == [] and not rp.moved
    hp = rp.state.at("p2", 0).hp
    rp = battle.append({"kind": "cant", "side": "p2", "slot": 0, "reason": "flinch"})
    prog = entry.progress(rp, battle.reg)
    assert prog["acted"] == [{"side": "p2", "species": "Kingambit"}] and prog["moved"]
    assert rp.state.at("p2", 0).hp == hp and rp.state.moves_log == []
    rp = battle.append({"kind": "cant", "side": "p2", "slot": 1})
    assert any("needs a reason" in e for e in rp.errors)
    rp = battle.append({"kind": "turn", "n": 2})
    assert entry.progress(rp, battle.reg)["acted"] == []


def test_a_fainted_slot_waits_for_a_replacement_and_says_who_fell_there(battle):
    _doubles(battle)
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Flare Blitz",
                   "target": {"side": "p2", "slot": 0}})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 0, "fainted": True})
    assert entry.progress(rp, battle.reg)["waiting"] == [{"side": "p2", "slot": 0, "was": "Kingambit"}]
    rp = battle.append({"kind": "switch", "side": "p2", "slot": 0, "species": "Pelipper"})
    assert entry.progress(rp, battle.reg)["waiting"] == []


def test_once_four_are_seen_the_other_two_are_not_offered(battle):
    _doubles(battle)
    battle.append({"kind": "switch", "side": "p2", "slot": 0, "species": "Archaludon"})
    assert len(entry.sendable(battle.reg, battle.rp.state, "p2")) == 4   # Kingambit + three unseen
    battle.append({"kind": "switch", "side": "p2", "slot": 1, "species": "Torkoal"})
    left = entry.sendable(battle.reg, battle.rp.state, "p2")
    assert sorted(m.species for m in left) == ["Kingambit", "Milotic"]


def test_the_end_of_turn_offers_what_could_apply_with_the_hp_it_would_leave(battle):
    """Sand chips everyone but the Ground type; your own Leftovers heals by a real number; a burn
    comes after both, as the cartridge resolves them."""
    lead(battle, ("p1", 0, "Sinistcha"), ("p1", 1, "Garchomp"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    answer_all(battle)
    battle.append({"kind": "turn", "n": 1})
    sinistcha = battle.rp.state.at("p1", 0)
    battle.append({"kind": "damage", "side": "p1", "slot": 0, "hp": sinistcha.hp_max - 50})
    battle.append({"kind": "status", "side": "p2", "slot": 1, "status": "brn"})
    rp = battle.append({"kind": "field", "what": "weather", "value": "sandstorm", "on": True})
    eot = entry.end_of_turn(rp, battle.reg)
    effects = [(x["effect"], x["species"]) for x in eot]
    sand = [sp for e, sp in effects if e == "Sandstorm"]
    assert "Garchomp" not in sand and "Kingambit" not in sand      # Ground; Steel
    assert {"Sinistcha", "Milotic"} <= set(sand)
    lefties = next(x for x in eot if x["effect"] == "Leftovers" and x["side"] == "p1")
    assert lefties["expect"] == {"hp": sinistcha.hp_max - 50 + sinistcha.hp_max // 16}
    assert effects.index(("Burn", "Milotic")) > effects.index(("Leftovers", "Sinistcha"))
    burn = next(x for x in eot if x["effect"] == "Burn")
    assert burn["expect"] == {"pct": 94} and burn["kind"] == "damage"


def test_a_confirmed_end_of_turn_effect_is_not_offered_again_or_put_down_to_a_move(battle):
    """Leftovers confirmed once stays confirmed through a reload; and sand chip logged after a move
    must not become that move's damage, or the damage channel reads the move as hitting harder."""
    lead(battle, ("p1", 0, "Sinistcha"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    answer_all(battle)
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "move", "side": "p2", "slot": 1, "move": "Scald", "target": {"side": "p1", "slot": 0}})
    sinistcha = battle.rp.state.at("p1", 0)
    battle.append({"kind": "damage", "side": "p1", "slot": 0, "hp": sinistcha.hp_max - 60})
    rp = battle.append({"kind": "field", "what": "weather", "value": "sandstorm", "on": True})
    keys = {x["key"] for x in entry.end_of_turn(rp, battle.reg)}
    assert {"leftovers:p10", "sandstorm:p10", "sandstorm:p21"} <= keys
    battle.append({"kind": "heal", "side": "p1", "slot": 0, "hp": sinistcha.hp_max - 70, "eot": "sandstorm:p10"})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 1, "pct": 94, "eot": "sandstorm:p21"})
    left = {x["key"] for x in entry.end_of_turn(rp, battle.reg)}
    assert "sandstorm:p10" not in left and "sandstorm:p21" not in left and "leftovers:p10" in left
    assert len(rp.state.damage_log) == 1                    # only Scald's
    replayed = entry.Battle(battle.reg, battle.setup, battle.journal).rp
    assert {x["key"] for x in entry.end_of_turn(replayed, battle.reg)} == left


# --- what follows from a move ------------------------------------------------------------------

def _mixed(battle):
    lead(battle, ("p1", 0, "Salamence"), ("p1", 1, "Incineroar"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    answer_all(battle)
    return battle.append({"kind": "turn", "n": 1})


def test_a_move_that_lowers_its_users_stats_does_so_once_it_connects(battle):
    """Draco Meteor's −2 lands with its hit, and also when the hit is a KO; a miss lands nothing.
    Your own item is known and is no White Herb, so nothing is asked."""
    _mixed(battle)
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Draco Meteor", "target": {"side": "p2", "slot": 1}})
    assert battle.rp.state.at("p1", 0).boosts.get("spa", 0) == 0          # not yet: nothing has hit
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 1, "fainted": True})
    assert rp.state.at("p1", 0).boosts["spa"] == -2 and not [q for q in rp.questions if q.kind == "move_drop"]
    rp = battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Draco Meteor",
                        "target": {"side": "p2", "slot": 0}, "result": "miss"})
    assert rp.state.at("p1", 0).boosts["spa"] == -2


def test_a_certain_drop_on_a_target_asks_what_its_ability_did(battle):
    """Snarl lowers each target it hits, and Kingambit could be Defiant, so that is asked; the
    target that fainted is not."""
    _mixed(battle)
    battle.append({"kind": "move", "side": "p1", "slot": 1, "move": "Snarl", "spread": True})
    battle.append({"kind": "damage", "side": "p2", "slot": 0, "pct": 80})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 1, "fainted": True})
    drops = [q for q in rp.questions if q.kind == "move_drop"]
    assert [q.species for q in drops] == ["Kingambit"]
    defiant = next(i for i, o in enumerate(drops[0].outcomes) if o.ability == "defiant")
    rp = battle.append({"kind": "answer", "question": drops[0].id, "option": defiant})
    gambit = rp.state.at("p2", 0)
    assert gambit.boosts == {"spa": -1, "atk": 2} and gambit.ability == "defiant"


def test_a_chance_effect_lands_only_when_you_say_it_happened(battle):
    _mixed(battle)
    battle.append({"kind": "move", "side": "p2", "slot": 1, "move": "Scald", "target": {"side": "p1", "slot": 1}})
    rp = battle.append({"kind": "damage", "side": "p1", "slot": 1, "pct": 70})
    assert rp.state.at("p1", 1).status is None
    battle.undo(), battle.undo()
    battle.append({"kind": "move", "side": "p2", "slot": 1, "move": "Scald", "target": {"side": "p1", "slot": 1},
                   "chance": [{"side": "p1", "slot": 1}]})
    rp = battle.append({"kind": "damage", "side": "p1", "slot": 1, "pct": 70})
    assert rp.state.at("p1", 1).status == "brn"


def test_a_status_move_lowers_its_target_as_it_is_used(battle):
    _mixed(battle)
    rp = battle.append({"kind": "move", "side": "p1", "slot": 1, "move": "Parting Shot", "target": {"side": "p2", "slot": 1}})
    q = next(q for q in rp.questions if q.kind == "move_drop")
    assert q.species == "Milotic" and "atk, spa" in q.prompt
    assert any(o.ability == "competitive" for o in q.outcomes)


def _garchomp(battle):
    lead(battle, ("p1", 0, "Garchomp"), ("p1", 1, "Incineroar"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    answer_all(battle)
    return battle.append({"kind": "turn", "n": 1})


def test_life_orb_takes_a_tenth_once_the_move_hits_and_says_so(battle):
    """Your Garchomp's Life Orb is known: one hit costs a tenth of its HP, rounded down, once for
    a spread move and not at all for a miss."""
    rp = _garchomp(battle)
    full = rp.state.at("p1", 0).hp_max
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Dragon Claw", "target": {"side": "p2", "slot": 1}})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 1, "pct": 60})
    chomp = rp.state.at("p1", 0)
    assert round(chomp.hp * chomp.hp_max) == full - full // 10
    assert rp.applied == {len(battle.journal) - 1: [f"Life Orb: Garchomp on {full - full // 10} HP"]}
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Rock Slide", "spread": True})
    battle.append({"kind": "damage", "side": "p2", "slot": 0, "pct": 80})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 1, "pct": 40})
    assert round(rp.state.at("p1", 0).hp * full) == full - 2 * (full // 10)
    rp = battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Dragon Claw",
                        "target": {"side": "p2", "slot": 0}, "result": "miss"})
    assert round(rp.state.at("p1", 0).hp * full) == full - 2 * (full // 10)


def test_a_first_protect_is_up_until_feint_and_a_second_in_a_row_is_not_assumed(battle):
    rp = _garchomp(battle)
    rp = battle.append({"kind": "move", "side": "p2", "slot": 1, "move": "Protect", "target": None})
    assert entry.progress(rp, battle.reg)["shielded"] == [{"side": "p2", "slot": 1}]
    rp = battle.append({"kind": "move", "side": "p1", "slot": 1, "move": "Feint", "target": {"side": "p2", "slot": 1}})
    assert entry.progress(rp, battle.reg)["shielded"] == []
    battle.append({"kind": "turn", "n": 2})
    rp = battle.append({"kind": "move", "side": "p2", "slot": 1, "move": "Protect", "target": None})
    assert entry.progress(rp, battle.reg)["shielded"] == []


def test_recoil_is_a_share_of_the_damage_dealt(battle):
    """Your Rillaboom's Wood Hammer takes a third of what it dealt. Kingambit's HP is a percentage
    of a max HP its sheet does not show, so the amount rests on a guess and says so."""
    lead(battle, ("p1", 0, "Rillaboom"), ("p1", 1, "Incineroar"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    answer_all(battle)
    rp = battle.append({"kind": "turn", "n": 1})
    full = rp.state.at("p1", 0).hp_max
    dealt = round(0.6 * rules.max_hp(rp.state, rp.state.at("p2", 0))[0])
    battle.append({"kind": "move", "side": "p1", "slot": 0, "move": "Wood Hammer", "target": {"side": "p2", "slot": 0}})
    rp = battle.append({"kind": "damage", "side": "p2", "slot": 0, "pct": 40})
    answer_all(battle)
    left = full - round(dealt * 33 / 100)
    assert round(battle.rp.state.at("p1", 0).hp * full) == left
    assert rp.applied[len(battle.journal) - 1 - len(rp.questions)] == [f"Wood Hammer recoil: Rillaboom on about {left} HP"]


def test_their_recoil_and_your_rough_skin_take_their_hp(battle):
    rp = _garchomp(battle)
    gambit = rules.max_hp(rp.state, rp.state.at("p2", 0))[0]
    # Kingambit makes contact with your Garchomp, whose Rough Skin is known: no question, an eighth.
    battle.append({"kind": "move", "side": "p2", "slot": 0, "move": "Iron Head", "target": {"side": "p1", "slot": 0}})
    rp = battle.append({"kind": "damage", "side": "p1", "slot": 0, "hp": 100})
    assert not [q for q in rp.questions if q.kind == "on_hit"]
    assert round(rp.state.at("p2", 0).hp * 100) == round(100 - 100 * (gambit // 8) / gambit)
    # Earthquake makes none, so nothing is taken.
    hp = rp.state.at("p2", 0).hp
    battle.append({"kind": "move", "side": "p2", "slot": 0, "move": "Earthquake", "spread": True})
    rp = battle.append({"kind": "damage", "side": "p1", "slot": 0, "hp": 80})
    assert rp.state.at("p2", 0).hp == hp


def test_steel_beam_costs_half_whatever_it_did(battle):
    rp = _garchomp(battle)
    rp = battle.append({"kind": "move", "side": "p2", "slot": 0, "move": "Steel Beam",
                        "target": {"side": "p1", "slot": 1}, "result": "miss"})
    assert 49 <= round(rp.state.at("p2", 0).hp * 100) <= 50


def test_picking_rocky_helmet_reveals_it_and_hurts_the_attacker(reg, team_text):
    """A third of Indeedee-F run Rocky Helmet, so after your Incineroar's Flare Blitz makes contact
    it is one of the answers; picking it is both the reveal and the sixth off Incineroar."""
    b = entry.Battle(reg, {"perspective": "p1", "mine": entry.from_team(reg, team_text("valid_basic")),
                           "theirs": [{"species": s} for s in ["Indeedee-F"] + THEIRS[1:]]})
    lead(b, ("p1", 0, "Garchomp"), ("p1", 1, "Incineroar"), ("p2", 0, "Indeedee-F"), ("p2", 1, "Milotic"))
    answer_all(b)
    b.append({"kind": "turn", "n": 1})
    b.append({"kind": "move", "side": "p1", "slot": 1, "move": "Flare Blitz", "target": {"side": "p2", "slot": 0}})
    rp = b.append({"kind": "damage", "side": "p2", "slot": 0, "pct": 70})
    q = next(q for q in rp.questions if q.kind == "on_hit")
    inc = rp.state.at("p1", 1)
    after_recoil = round(inc.hp * inc.hp_max)
    i = next(i for i, o in enumerate(q.outcomes) if o.label == "Rocky Helmet hurt the attacker")
    rp = b.append({"kind": "answer", "question": q.id, "option": i})
    inc = rp.state.at("p1", 1)
    assert rp.state.at("p2", 0).item == "rockyhelmet"
    assert round(inc.hp * inc.hp_max) == after_recoil - inc.hp_max // 6
    assert rp.applied[len(b.journal) - 1] == [f"Rocky Helmet: Incineroar on {after_recoil - inc.hp_max // 6} HP"]
