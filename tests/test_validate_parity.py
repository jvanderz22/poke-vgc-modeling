"""Our validator and Showdown's must agree on legality — except Tera, which we reject and
Showdown silently ignores (docs/phase0-findings.md)."""

from __future__ import annotations

import pytest

from tests.conftest import FIXTURES
from vgc.engine.showdown import validate_with_showdown
from vgc.teams import is_legal, parse_team, validate_team

pytestmark = pytest.mark.showdown

KNOWN_DIVERGENCE = {"bad_tera"}


@pytest.mark.parametrize("path", sorted((FIXTURES / "teams").glob("*.txt")), ids=lambda p: p.stem)
def test_parity_with_showdown(reg, path):
    text = path.read_text()
    ours = is_legal(validate_team(parse_team(text), reg))
    theirs = not validate_with_showdown(text, reg)
    if path.stem in KNOWN_DIVERGENCE:
        assert theirs and not ours
    else:
        assert ours == theirs


def test_showdown_pin(reg):
    from vgc.engine.showdown import check_pin

    check_pin(reg)
