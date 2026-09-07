"""`CODE.EDITION_CURRENT`, and the corpus and lexicon it reads from.

Written from training feedback `e4e02383fed1` on **ITEC - Building Plans.pdf**,
which a reviewer confirmed: the finding was right, and no defect was reported.
A confirmation is evidence, so it is written down as a gate rather than filed —
the sentences the reviewer read and agreed with are the ones a later refinement
must not quietly change.

Reproducing it also turned up something the reviewer was not looking at:
`fbcreview/codes/editions.py` gave `fbc2017` the ordinal `7th`, which is the 2020
code's. The rule prints `cited.ordinal` into its result and `cited.label` into
its title, so a 6th Edition set produced a finding whose title said *2017 Florida
Building Code, 6th Edition* and whose result said *Every code reference in this
set is 7th Edition (2017)* — a finding contradicting itself in adjacent
sentences, in the same paragraph the reviewer had just confirmed.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fbcreview.codes import editions as E
from fbcreview.declaration import ProjectDeclaration
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all
from fixtures.permit_sets import ITEC_DECLARATION, itec

#: The date the confirmed review was run. Pinned rather than `today()` so the
#: sentences below keep meaning what the reviewer read, and so the suite does
#: not change its mind on 31 December 2026.
AS_OF = dt.date(2026, 8, 25)


@pytest.fixture(scope="module")
def itec_set(tmp_path_factory):
    path = tmp_path_factory.mktemp("edition") / "itec.pdf"
    path.write_bytes(itec(False))
    return str(path)


@pytest.fixture(scope="module")
def confirmed(itec_set):
    """The finding feedback `e4e02383fed1` confirmed."""
    facts = build_facts(itec_set)
    facts.meta["as_of"] = AS_OF
    res = run_all(facts, None, ProjectDeclaration.from_dict(ITEC_DECLARATION))
    fired = [f for f in res.findings if f.rule_id == "CODE.EDITION_CURRENT"]
    assert len(fired) == 1, "the confirmed finding is reported exactly once"
    return fired[0]


# ══════════════════════════════════════════════════════════════════════════
# 1. The confirmed finding, clause by clause
# ══════════════════════════════════════════════════════════════════════════
def test_the_confirmed_finding_keeps_its_register(confirmed):
    assert confirmed.fid == "C-ED"
    assert confirmed.status == "OPEN"
    assert confirmed.severity == "CRITICAL"
    assert confirmed.discipline == "Administration"
    assert confirmed.page == 0


def test_the_confirmed_finding_keeps_its_citation(confirmed):
    """`citation` is never auto-applied (TRAINING-MODE.md §3.4), so the string a
    reviewer signed off on is the string that has to survive."""
    assert confirmed.code == "F.A.C. 61G20 · FBC adoption schedule · FBC-B 1609"


def test_the_confirmed_finding_names_the_superseded_edition_in_its_title(confirmed):
    assert confirmed.title == (
        "Designed and cited to 2020 Florida Building Code, 7th Edition, "
        "which is superseded"
    )


def test_the_confirmed_finding_states_what_the_set_cites(confirmed):
    assert "Every code reference in this set is 7th Edition (2020)" in confirmed.result
    assert "based on IBC 2018" in confirmed.result


def test_the_confirmed_finding_dates_the_edition_in_force(confirmed):
    """Both dates, because the whole claim is a comparison between them."""
    assert "2023 Florida Building Code, 8th Edition took effect 2023-12-31" in confirmed.result
    assert f"is the code in force as of {AS_OF.isoformat()}" in confirmed.result


def test_the_confirmed_finding_says_the_set_was_legitimate_when_drawn(confirmed):
    """The sentence that stops this reading as an accusation. A reviewer
    confirmed it; it is not decoration."""
    assert (
        "The set was legitimately 7th Edition when it was drawn" in confirmed.result
    )
    assert "submitted, re-submitted or revived today" in confirmed.result


def test_the_confirmed_finding_is_a_re_analysis_not_a_cover_sheet_edit(confirmed):
    """The reviewer's own summary of why this is CRITICAL: the referenced
    standard moved, so the wind numbers move with it."""
    assert "re-analysis rather than a cover-sheet edit" in confirmed.result
    assert "adopts ASCE 7-22 in place of ASCE 7-16" in confirmed.result


def test_the_confirmed_finding_asks_about_the_live_permit_first(confirmed):
    assert confirmed.action.startswith(
        "Confirm whether a live permit exists from the original submittal."
    )
    assert "update every code reference to the 8th Edition" in confirmed.action


# ══════════════════════════════════════════════════════════════════════════
# 2. The corpus rows have to agree with themselves
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("edition", E.EDITIONS, ids=lambda e: e.key)
def test_every_edition_row_agrees_with_its_own_label(edition):
    """`ordinal` is printed into the result and `label` into the title, so a row
    where they disagree produces a finding that contradicts itself."""
    assert f"{edition.ordinal} Edition" in edition.label
    assert str(edition.year) in edition.label


def test_no_two_editions_claim_the_same_ordinal():
    ordinals = [e.ordinal for e in E.EDITIONS]
    assert len(set(ordinals)) == len(ordinals), f"duplicated ordinal in {ordinals}"


def test_editions_are_ordered_oldest_first():
    dates = [e.effective for e in E.EDITIONS]
    assert dates == sorted(dates)


def test_the_sixth_edition_is_the_2017_code():
    """F.A.C. 61G20 / FBC adoption schedule: the 2017 Florida Building Code is
    the 6th Edition, effective 31 December 2017. It is not a second 7th."""
    sixth = E.edition("fbc2017")
    assert sixth is not None
    assert sixth.ordinal == "6th"
    assert sixth.year == 2017
    assert sixth.effective == dt.date(2017, 12, 31)
