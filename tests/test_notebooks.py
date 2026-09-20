"""Notebooks we ship are executed by a machine we pay for (or queue for), so a syntax error
costs a round trip to find. Jupyter stores `source` as a list of lines *with* their newlines;
a notebook written without them concatenates into one line on load and fails at the first
statement — which is exactly how kaggle_train_wp.ipynb failed its first real run.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

NOTEBOOKS = sorted((Path(__file__).parent.parent / "scripts").rglob("*.ipynb"))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_code_cells_parse(path: Path):
    nb = json.loads(path.read_text())
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] != "code":
            continue
        src = "".join(cell["source"])
        try:
            ast.parse(src)
        except SyntaxError as e:
            pytest.fail(f"{path.name} cell {i} line {e.lineno}: {e.msg}\n{src[:200]}")


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_source_lines_keep_their_newlines(path: Path):
    nb = json.loads(path.read_text())
    for i, cell in enumerate(nb["cells"]):
        src = cell["source"]
        if len(src) < 2:
            continue
        missing = [j for j, line in enumerate(src[:-1]) if not line.endswith("\n")]
        assert not missing, f"{path.name} cell {i}: lines {missing[:5]} lack a trailing newline"
