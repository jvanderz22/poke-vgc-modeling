from __future__ import annotations

from pathlib import Path

import pytest

from vgc.video.screen import Observation, Plate, ScreenReader, Settler, scale

FRAMES = Path(__file__).parent / "fixtures" / "video"


# --- settling -------------------------------------------------------------------------------

def _feed(settler: Settler, rows: list[tuple[float, Observation]]):
    out = []
    for t, obs in rows:
        out += settler.feed(t, obs)
    return out


def test_a_value_counts_once_it_holds_and_is_stamped_when_it_first_showed():
    s = Settler(settle=2)
    events = _feed(s, [(0.0, Observation(message="Gardevoir used Hyper Voice!")),
                       (0.25, Observation(message="Gardevoir used Hyper Voice!")),
                       (0.5, Observation(message="Gardevoir used Hyper Voice!"))])
    assert [(e.t, e.kind, e.data) for e in events] == [(0.0, "message", {"text": "Gardevoir used Hyper Voice!"})]


def test_a_number_still_counting_never_becomes_an_event():
    s = Settler(settle=2)
    rows = [(t / 4, Observation(plates={"R0": Plate("Grimmsnarl", hp)})) for t, hp in enumerate([100, 82, 75, 66, 59, 59])]
    events = _feed(s, rows)
    assert [(e.data["hp"], e.t) for e in events] == [(59, 1.0)]


def test_a_message_fading_out_is_not_read_twice():
    s = Settler(settle=2)
    rows = [(0.0, Observation(message="Indeedee's Sp. Atk fell!")), (0.25, Observation(message="Indeedee's Sp. Atk fell!")),
            (1.5, Observation(message="Indeedee's Sp. Atk fel")), (1.75, Observation(message="Indeedee's Sp. Atk fel"))]
    assert [e.data["text"] for e in _feed(s, rows)] == ["Indeedee's Sp. Atk fell!"]


def test_two_messages_alike_but_about_different_pokemon_are_both_read():
    s = Settler(settle=2)
    a, b = "It's super effective on the opposing Grimmsnarl!", "It's super effective on the opposing Archaludon!"
    rows = [(0.0, Observation(message=a)), (0.25, Observation(message=a)),
            (1.5, Observation(message=b)), (1.75, Observation(message=b))]
    assert [e.data["text"] for e in _feed(s, rows)] == [a, b]


def test_the_same_message_after_a_gap_is_read_again():
    s = Settler(settle=2)
    m = "Gardevoir used Protect!"
    rows = [(0.0, Observation(message=m)), (0.25, Observation(message=m)),
            (0.5, Observation()), (0.75, Observation()),
            (9.0, Observation(message=m)), (9.25, Observation(message=m))]
    assert [e.t for e in _feed(s, rows)] == [0.0, 9.0]


# --- real frames, at 720p -------------------------------------------------------------------

@pytest.fixture(scope="module")
def observe():
    pytest.importorskip("rapidocr")
    from PIL import Image

    return lambda name: ScreenReader().observe(scale(Image.open(FRAMES / f"{name}.jpg")))


def test_a_move_message_and_the_plates_shown_with_it(observe):
    obs = observe("r5-snarl")
    assert obs.message == "The opposing Archaludon used Snarl!" and not obs.choosing
    assert obs.plates == {"L0": Plate("Gardevoir", 100), "L1": Plate("Indeedee", 100)}


def test_a_decision_point_shows_all_four_and_the_field(observe):
    obs = observe("r5-choosing")
    assert obs.choosing and obs.message is None
    assert obs.plates == {"L0": Plate("Gardevoir", 85), "L1": Plate("Indeedee", 38),
                          "R0": Plate("Grimmsnarl", 43), "R1": Plate("Archaludon", 66)}
    assert obs.effect == "Rain 3/5"
    assert obs.sides == ("Light Screen 1/5",)


def test_an_item_banner_is_read_on_its_owners_side(observe):
    obs = observe("r5-rocky-helmet")
    assert obs.banners == {"L": "Indeedee's Rocky Helmet", "R": None}


def test_a_faint(observe):
    assert observe("r5-faint").message == "Gardevoir fainted!"


def test_an_hp_read_with_its_percent_sign_first(observe):
    # Struggle Bug's chip on Incineroar, which OCR reads as "%88".
    assert observe("struggle-bug-hp").plates == {"L1": Plate("Incineroar", 88)}


def test_scenery_moving_behind_the_regions_is_not_read_again():
    """The animated screens put white shapes (a jacket, a Pokémon) in the banner regions on most
    frames. A shape isn't text, so it mustn't send a region back to OCR."""
    pytest.importorskip("rapidocr")
    from PIL import Image

    frame = scale(Image.open(FRAMES / "r5-choosing.jpg"))
    reader = ScreenReader()
    reader.observe(frame)
    reader.observe(frame)  # a changed region is read again on the frame after
    read = reader.ocr_calls

    h, w = frame.shape[:2]
    moved = frame.copy()
    for key in ("banner_L", "banner_R", "message"):
        x0, y0, x1, y1 = reader.layout.regions[key]
        cx, cy = int((x0 + x1) / 2 * w), int((y0 + y1) / 2 * h)
        moved[cy - 30:cy + 30, cx - 40:cx + 40] = 255
    obs = reader.observe(moved)
    assert reader.ocr_calls == read
    assert obs.choosing and obs.message is None
