from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vgc import paths
from vgc.regulation import load_regulation

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config, items):
    has_node = shutil.which("node") is not None
    missing = {
        "showdown": not (has_node and (paths.SHOWDOWN / "dist" / "sim").exists()),
        "calc": not (has_node and (paths.SIDECAR / "calc" / "node_modules" / "@smogon" / "calc").exists()),
    }
    for item in items:
        for mark, absent in missing.items():
            if absent and mark in item.keywords:
                item.add_marker(pytest.mark.skip(reason=f"{mark} not built (see README)"))


@pytest.fixture(scope="session")
def reg():
    return load_regulation("reg_mc")


@pytest.fixture(scope="session")
def calc():
    from vgc.engine.calc import DamageCalc

    with DamageCalc() as dc:
        yield dc


@pytest.fixture
def team_text():
    return lambda name: (FIXTURES / "teams" / f"{name}.txt").read_text()
