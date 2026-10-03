"""Every stage the worker can report has its explanation in the client.

The upload page explains each station of a running review in a line under its
name, and that line is looked up by the stage's exact string
(`web/src/app/review/review.ts`, `STAGE_DETAIL`) — by name rather than by index,
because the stage list is per job. The cost of that is drift: a stage renamed in
`webapp/worker.py`, or a new one added there, still shows up in the progress
list, but with no explanation and no error anywhere. For the drawing upload's
"Converting the drawing" that is the station a person watches for minutes.

So this reads the TypeScript and checks the two against each other, the same
way `test_button_hover.py` reads the stylesheet: the contract spans two
languages, and only something that reads both can hold it. It runs in pytest
rather than vitest because the server's list is computed in Python.

The check goes both ways. A stage with no explanation is the drift above; an
explanation for a stage the worker no longer sends is a rename that updated one
side and left a dead key on the other.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Set

from webapp import worker
from webapp.worker import stages_for

REVIEW_TS = (Path(__file__).resolve().parent.parent
             / "web" / "src" / "app" / "review" / "review.ts")

#: `const NAME = 'value';` at the top level of the module.
_CONST = re.compile(r"^const ([A-Z][A-Z0-9_]*) = '([^']*)';", re.M)
#: The STAGE_DETAIL object literal, from its opening brace to the closing `};`.
_TABLE = re.compile(r"^const STAGE_DETAIL: Record<string, string> = \{\n(.*?)^\};", re.M | re.S)
#: A key at the object's own indentation (two spaces). Value continuation lines
#: are indented further, so they never match. Quoted, computed or bare.
_KEY = re.compile(
    r"^  (?! )(?:'(?P<quoted>[^']+)'|\[(?P<const>[A-Z][A-Z0-9_]*)\]|(?P<bare>[A-Za-z_]\w*))\s*:",
    re.M,
)
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _source() -> str:
    return REVIEW_TS.read_text(encoding="utf-8")


def _consts() -> Dict[str, str]:
    return dict(_CONST.findall(_source()))


def client_keys() -> Set[str]:
    """The stage names `STAGE_DETAIL` explains, with computed keys resolved."""
    match = _TABLE.search(_source())
    assert match, "STAGE_DETAIL is not declared in review.ts in the expected form"
    body = _COMMENT.sub("", match.group(1))
    consts = _consts()
    keys = set()
    for key in _KEY.finditer(body):
        if key.group("const"):
            name = key.group("const")
            assert name in consts, f"STAGE_DETAIL key [{name}] is not a string const in review.ts"
            keys.add(consts[name])
        else:
            keys.add(key.group("quoted") or key.group("bare"))
    return keys


def server_stages() -> Set[str]:
    """Every label any job can carry: each combination of the options that add one."""
    labels: Set[str] = set()
    for convert in (False, True):
        for ai in (False, True):
            for review in (False, True):
                labels.update(stages_for(convert, ai, review))
                labels.update(stages_for(convert, ai, review, cad=True))
    return labels


def test_the_parser_finds_the_table():
    """Guards the guard: a regex that matched nothing would pass everything below."""
    keys = client_keys()
    assert len(keys) >= 8, keys
    assert "Reading the PDF" in keys
    assert "Delivering" in keys


def test_every_stage_the_worker_sends_has_an_explanation():
    missing = sorted(server_stages() - client_keys())
    assert not missing, (
        f"stages with no STAGE_DETAIL entry in review.ts: {missing}. They will show "
        f"in the progress list with no explanation."
    )


def test_every_explanation_is_for_a_stage_the_worker_sends():
    stale = sorted(client_keys() - server_stages())
    assert not stale, (
        f"STAGE_DETAIL keys the worker never sends: {stale}. A stage was renamed on "
        f"one side only."
    )


def test_the_drawing_stage_is_explained():
    """The case this file was written for, named so a failure says which."""
    assert worker.CAD_STAGE in stages_for(False, False, False, cad=True)
    assert worker.CAD_STAGE in client_keys()


def test_the_client_constants_are_the_workers_strings():
    """The constants review.ts names after the worker's are the worker's, byte for byte."""
    consts = _consts()
    for name in ("CAD_STAGE", "AI_STAGE", "REVIEW_STAGE"):
        assert name in consts, f"review.ts no longer declares {name}"
        assert consts[name] == getattr(worker, name), (
            f"review.ts {name} = {consts[name]!r}, worker.{name} = {getattr(worker, name)!r}"
        )
