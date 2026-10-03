"""What counts as two readings when the sheets were plotted from a drawing.

On a PDF, two readers agreeing — or two sheets — is corroboration, and the fact
store says HIGH (`factstore.independent`). On a set plotted from a DWG or DXF the
text layer was written from the drawing's own entities, so the layout reader
pairing an ATTRIB's text and the CAD reader reading that ATTRIB have read one
thing twice, and a model-space note seen through viewports on two layouts is
one note. Each claim read off a drawn sheet carries the entities it came from
(`Claim.source`, `dxf:<handle>` tokens), and independence is counted over those.

What must not move: a claim with no source — every claim on a PDF upload —
behaves exactly as it always has.
"""
from __future__ import annotations

import dataclasses

import pytest

from fbcreview.confidence import HIGH, MEDIUM
from fbcreview.factstore import (AI, CAD, LINE, MEASURED, PAIR, STATED, Claim, FactStore,
                                 independent)


def claim(value="III", *, sheet="A-101", page=0, method=PAIR, source="", layout="",
          layer="", label="RISK CATEGORY", raw=None, box=(10, 10, 60, 20)):
    return Claim("risk_category", value, raw or f"{label}: {value}", page, sheet, box,
                 method, label=label, source=source, layout=layout, layer=layer)


def resolve(*claims):
    store = FactStore()
    store.extend(claims)
    return store.resolve("risk_category")


# ── PDF claims: unchanged ────────────────────────────────────────────────────
def test_two_readers_on_one_box_of_a_pdf_are_still_two_readings():
    r = resolve(claim(), claim(method=AI))
    assert r.confidence == HIGH
    assert "two independent readers agree" in r.evidence().note


def test_one_reader_twice_on_one_pdf_page_is_still_one_reading():
    a = claim(box=(10, 10, 60, 20))
    b = claim(box=(10, 300, 60, 310), raw="RISK CAT III")
    r = resolve(a, b)
    assert len(r.claims) == 2 and r.confidence == MEDIUM


def test_two_pdf_sheets_are_still_two_readings():
    r = resolve(claim(), claim(sheet="S-1", page=4))
    assert r.confidence == HIGH
    assert "also stated on S-1" in r.evidence().note


def test_independence_without_sources_is_the_old_rule_exactly():
    """More than one method or more than one page — for every combination."""
    for methods in ([PAIR], [PAIR, AI], [PAIR, LINE], [AI, AI]):
        for pages in ([0], [0, 1], [2, 2]):
            cs = [claim(method=m, page=p) for m in methods for p in pages]
            old = len({c.method for c in cs}) > 1 or len({c.page for c in cs}) > 1
            assert independent(cs) is old, (methods, pages)


# ── drawn claims ─────────────────────────────────────────────────────────────
def test_a_cad_claim_and_a_pair_claim_on_one_entity_are_one_reading():
    attrib = claim(method=CAD, source="dxf:2F1", layout="PLAN", raw="III")
    paired = claim(method=PAIR, source="dxf:2EE+dxf:2F1", layout="PLAN")
    r = resolve(attrib, paired)
    assert r.methods == ["cad", "pair"]
    assert r.confidence == MEDIUM
    assert "independent" not in r.evidence().note


def test_an_ai_reading_of_the_same_entity_is_not_a_second_reader_either():
    r = resolve(claim(method=PAIR, source="dxf:A1+dxf:A2"),
                claim(method=AI, source="dxf:A2"))
    assert r.confidence == MEDIUM and "independent" not in r.evidence().note


def test_different_entities_on_two_sheets_are_two_readings():
    r = resolve(claim(source="dxf:A1+dxf:A2", layout="PLAN"),
                claim(sheet="A-102", page=1, source="dxf:B7+dxf:B8", layout="PLAN-2"))
    assert r.confidence == HIGH
    assert "also stated on A-102" in r.evidence().note


def test_different_entities_by_two_readers_on_one_sheet_are_two_readings():
    """A code block's printed row and a separate title-block attribute that
    agree are two statements the drawing makes."""
    r = resolve(claim(method=PAIR, source="dxf:A1+dxf:A2"), claim(method=CAD, source="dxf:C9"))
    assert r.confidence == HIGH
    assert "two independent readers agree" in r.evidence().note


def test_one_model_space_note_on_two_layouts_is_one_reading():
    """The same TEXT, seen through a viewport on each of two sheets."""
    r = resolve(claim(source="dxf:5D0", layout="PLAN"),
                claim(sheet="A-102", page=1, source="dxf:5D0", layout="ENLARGED"))
    assert r.confidence == MEDIUM
    note = r.evidence().note
    assert "also stated on" not in note
    assert "the same drawing entity is also shown on A-102" in note


def test_a_sheet_with_its_own_statement_as_well_still_counts_as_stating_it():
    r = resolve(claim(source="dxf:5D0"),
                claim(sheet="A-102", page=1, source="dxf:5D0"),
                claim(sheet="A-102", page=1, source="dxf:7AA", box=(10, 400, 60, 410)))
    assert r.confidence == HIGH
    assert "also stated on A-102" in r.evidence().note


def test_shared_entities_chain_into_one_reading():
    """A shares with B, B with C: one cluster, however the claims arrive."""
    cs = [claim(source="dxf:1+dxf:2"), claim(method=AI, source="dxf:3"),
          claim(method=CAD, source="dxf:2+dxf:3")]
    assert not independent(cs)
    assert not independent(list(reversed(cs)))
    assert independent(cs + [claim(sheet="S-1", page=3, source="dxf:9")])


def test_a_drawn_claim_and_an_unstamped_one_are_two_readings():
    """A reading whose box touched no text cell carries no source; it is
    counted by page and method, as on a PDF."""
    assert independent([claim(source="dxf:1"), claim(method=AI)])


# ── the Claim itself ─────────────────────────────────────────────────────────
def test_the_positional_constructor_is_unchanged():
    c = Claim("occupant_load", 70.0, "OCCUPANT LOAD: 70", 2, "G-1", (1, 2, 3, 4), PAIR)
    assert (c.field, c.value, c.raw, c.page, c.sheet, c.box, c.method) == (
        "occupant_load", 70.0, "OCCUPANT LOAD: 70", 2, "G-1", (1, 2, 3, 4), PAIR)
    assert (c.source, c.layout, c.layer) == ("", "", "")
    names = [f.name for f in dataclasses.fields(Claim)]
    assert names[:7] == ["field", "value", "raw", "page", "sheet", "box", "method"]
    assert names[-4:] == ["note", "source", "layout", "layer"]      # appended, after note


def test_as_meta_carries_the_drawing_only_when_there_is_one():
    plain = claim().as_meta()
    assert not {"source", "layout", "layer"} & set(plain)
    drawn = claim(method=CAD, source="dxf:2F1", layout="PLAN", layer="TITLE").as_meta()
    assert (drawn["source"], drawn["layout"], drawn["layer"]) == ("dxf:2F1", "PLAN", "TITLE")
    assert drawn["method"] == "cad"


def test_a_cad_reading_says_it_was_read_from_the_drawing_field():
    r = resolve(claim(method=CAD, source="dxf:2F1", label="RISK CATEGORY", raw="III"))
    note = r.evidence().note
    assert note.startswith("'III' read from the drawing's RISK CATEGORY field, printed on A-101")
    assert r.evidence().source == "A-101" and r.evidence().confidence == MEDIUM


def test_a_cad_reading_is_stated_and_the_store_records_it_as_such():
    c = claim(method=CAD, source="dxf:2F1")
    assert c.basis == STATED and c.basis != MEASURED


def test_the_same_reading_twice_is_still_kept_once():
    store = FactStore()
    store.extend([claim(method=CAD, source="dxf:1"), claim(method=CAD, source="dxf:1")])
    assert len(store.claims("risk_category")) == 1


def test_two_entities_one_reader_one_sheet_are_kept_but_are_one_reading():
    """The same words printed twice on one sheet were never corroboration on a
    PDF, and are not on a drawing either: one reader, one sheet. Both are kept,
    so a sheet that states a value itself is not reported as merely showing
    another sheet's note (measured: with the second entity dropped as "the same
    reading twice", A-102's own statement vanished from the evidence)."""
    store = FactStore()
    store.extend([claim(source="dxf:5D0"), claim(source="dxf:7AA", box=(10, 400, 60, 410))])
    r = store.resolve("risk_category")
    assert len(r.claims) == 2 and r.confidence == MEDIUM
    pdf = FactStore()
    pdf.extend([claim(), claim(box=(10, 400, 60, 410))])        # no source: as before
    assert len(pdf.claims("risk_category")) == 1


@pytest.mark.parametrize("sources, expected", [
    (["dxf:1", "dxf:1"], False),
    (["dxf:1", "dxf:2"], False),          # one reader on one sheet, whatever the entities
    (["dxf:1+dxf:2", "dxf:2"], False),
    (["", ""], False),
])
def test_independence_over_sources_on_one_page_and_method(sources, expected):
    cs = [claim(source=s) for s in sources]
    assert independent(cs) is expected
