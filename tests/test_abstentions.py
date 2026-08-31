"""Classifying a rule that declined to run, and diagnosing why so many did.

The load-bearing test in this file is
`test_every_reason_the_corpus_emits_is_classified`. `webapp.abstentions` matches
on reason strings the engine writes, and the engine is a directory this service
does not own — so a new reason added over there must fail a test here rather
than quietly landing in `unknown` and being reported to a reviewer as
"this build has no classification for that".

The numbers in `test_the_itec_set_is_diagnosed_as_a_pasted_code_table` come from
a real run: the ITEC Alico Park set, 35 sheets, 31 rules, 25 abstentions, with
eleven sheets carrying pasted images and the rebuild switched off.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from webapp import abstentions

ROOT = Path(__file__).resolve().parent.parent


# ══ the gate ══════════════════════════════════════════════════════════════
def _corpus_reasons() -> set[str]:
    """Every reason string `fbcreview` constructs an `Abstention` with.

    Parsed out of the source rather than collected from a run: a rule that only
    abstains on a set nobody has tested with would otherwise never be seen, and
    those are exactly the reasons most likely to be missed.

    An f-string is rendered with its interpolations blanked, which is enough:
    the classifier matches on the literal parts, and a reason whose *only*
    distinguishing text is interpolated could not be classified by anything.
    """
    reasons: set[str] = set()

    def render(node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value if isinstance(part, ast.Constant) else ""
                for part in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = render(node.left), render(node.right)
            return (left or "") + (right or "") if (left or right) else None
        return None

    for path in sorted((ROOT / "fbcreview").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name != "Abstention":
                continue
            keywords = {k.arg: k.value for k in node.keywords}
            argument = node.args[1] if len(node.args) >= 2 else keywords.get("reason")
            text = render(argument)
            if text and text.strip():
                reasons.add(text)
    return reasons


def test_every_reason_the_corpus_emits_is_classified():
    """A reason this build cannot classify is a gap in the list, not a verdict.

    `fbcreview/` is not this service's to edit, so the coupling runs the other
    way: when a rule grows a new way of standing down, this test fails and
    somebody decides which class it belongs to and what the guidance should
    say. The alternative is telling a reviewer "unclassified" about a rule the
    engine had a perfectly clear reason for.
    """
    found = _corpus_reasons()
    assert found, "no Abstention reasons found — the parser has stopped working"

    unclassified = sorted(r for r in found if abstentions.classify(r) == abstentions.UNKNOWN)
    assert not unclassified, (
        "webapp/abstentions.py has no classification for:\n  "
        + "\n  ".join(repr(r) for r in unclassified)
    )


def test_an_unrecognised_reason_says_so_rather_than_guessing():
    assert abstentions.classify("the moon was in the wrong phase") == abstentions.UNKNOWN
    assert abstentions.classify("") == abstentions.UNKNOWN
    # Unknown is still proposable: not knowing what a reason means is a reason
    # to have somebody look, not to file it as fine.
    assert abstentions.kind(abstentions.UNKNOWN).proposable is True


# ══ the classes ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "reason, expected",
    [
        ("no door schedule extracted", abstentions.EXTRACTION),
        ("no parseable 1005.3.2 datum", abstentions.EXTRACTION),
        ("occupant load not extracted", abstentions.EXTRACTION),
        ("building area not found on both general sheets", abstentions.EXTRACTION),
        ("no CAD layer matching 'egress path' — the plot may be a scan",
         abstentions.GEOMETRY),
        ("egress layer present but carries no line geometry", abstentions.GEOMETRY),
        ("Table 504.3 row not carried in this build's corpus", abstentions.CORPUS),
        ("no ASCE 7 edition is recorded for this code edition", abstentions.CORPUS),
        ("neither the drawings nor the declaration state this", abstentions.ABSENT),
        ("geometric measurement was switched off in the review options",
         abstentions.OPTION),
        ("rule raised", abstentions.ERROR),
    ],
)
def test_reasons_land_in_the_class_a_person_would_put_them_in(reason, expected):
    assert abstentions.classify(reason) == expected


def test_only_the_classes_with_something_to_fix_are_proposable():
    """Offering "propose a fix" on a correct abstention trains people to ignore it."""
    assert abstentions.kind(abstentions.EXTRACTION).proposable is True
    assert abstentions.kind(abstentions.GEOMETRY).proposable is True
    assert abstentions.kind(abstentions.CORPUS).proposable is True
    assert abstentions.kind(abstentions.ERROR).proposable is True
    # Nothing is wrong in either of these.
    assert abstentions.kind(abstentions.ABSENT).proposable is False
    assert abstentions.kind(abstentions.OPTION).proposable is False


def test_the_catalogue_covers_every_class_it_can_return():
    published = {row["key"] for row in abstentions.catalogue()}
    assert published == set(abstentions.KINDS)


# ══ the one contextual revision ═══════════════════════════════════════════
def test_absent_becomes_extraction_when_the_set_has_unread_pasted_tables():
    """"The set does not state it" is a claim about the part of the set we read.

    On a sheet whose code-analysis table is a pasted picture, nothing read the
    table — so the rule's reason is true about the text layer and false about
    the drawing. That is a defect, and it has to be reachable as one.
    """
    rows = [{"rule": "DECL.HEIGHT", "reason": "neither the drawings nor the declaration state this"}]

    plain = abstentions.classify_all(rows, unread_pasted_tables=False)
    assert plain[0]["kind"] == abstentions.ABSENT
    assert plain[0]["proposable"] is False

    revised = abstentions.classify_all(rows, unread_pasted_tables=True)
    assert revised[0]["kind"] == abstentions.EXTRACTION
    assert revised[0]["proposable"] is True


def test_classification_never_rewrites_what_the_rule_said():
    rows = [{"rule": "DOORS.CLEAR_WIDTH", "reason": "no door schedule extracted",
             "detail": "sheet A-601"}]
    out = abstentions.classify_all(rows, unread_pasted_tables=True)
    assert out[0]["reason"] == "no door schedule extracted"
    assert out[0]["detail"] == "sheet A-601"
    # And the input is not mutated: the caller's record is the stored one.
    assert "kind" not in rows[0]


def test_only_absent_is_revised_by_context():
    """A corpus gap is a corpus gap whatever else is wrong with the file."""
    rows = [{"rule": "HEIGHT_AREA.TABLE_504_HEIGHT",
             "reason": "Table 504.3 row not carried in this build's corpus"}]
    out = abstentions.classify_all(rows, unread_pasted_tables=True)
    assert out[0]["kind"] == abstentions.CORPUS


# ══ the diagnosis ═════════════════════════════════════════════════════════
ITEC = [
    {"rule": "DECL.BUILDING_AREA", "reason": "neither the drawings nor the declaration state this"},
    {"rule": "DECL.CONSTRUCTION_TYPE", "reason": "neither the drawings nor the declaration state this"},
    {"rule": "DECL.HEIGHT", "reason": "neither the drawings nor the declaration state this"},
    {"rule": "DECL.OCCUPANCY", "reason": "neither the drawings nor the declaration state this"},
    {"rule": "DECL.SPRINKLER", "reason": "neither the drawings nor the declaration state this"},
    {"rule": "DECL.STORIES", "reason": "neither the drawings nor the declaration state this"},
    {"rule": "DOORS.CLEAR_WIDTH", "reason": "no door schedule extracted"},
    {"rule": "EGRESS.CAPACITY_FACTOR", "reason": "no parseable 1005.3.2 datum"},
    {"rule": "EGRESS.EXIT_COUNT", "reason": "occupant load not extracted"},
    {"rule": "ELEC.PANEL_LOADING", "reason": "no load calculation table found"},
    {"rule": "MECH.OUTDOOR_AIR_CAPACITY", "reason": "outdoor-air total not extracted"},
    {"rule": "XSHEET.BUILDING_AREA", "reason": "building area not found on both general sheets"},
    {"rule": "MEASURE.EGRESS_EXTENT",
     "reason": "no CAD layer matching 'egress path' — the plot may be a scan, or the "
               "layer is named differently in this office's standard"},
]
#: `pdfkind` counts pages from zero; these are the eleven it reported.
ITEC_REGIONS = [0, 1, 5, 6, 8, 11, 12, 16, 17, 18, 30]


def test_the_itec_set_is_diagnosed_as_a_pasted_code_table():
    found = abstentions.diagnose(
        ITEC, region_pages=ITEC_REGIONS, raster_pages=[], converted=False, cad_layers=0
    )
    keys = [d["key"] for d in found]
    assert "pasted_code_table" in keys

    pasted = next(d for d in found if d["key"] == "pasted_code_table")
    # Every starved rule is named, so the claim can be checked against the list.
    assert len(pasted["rules"]) == 12
    assert "DECL.HEIGHT" in pasted["rules"]
    assert "MEASURE.EGRESS_EXTENT" not in pasted["rules"]  # that one is geometry
    # Sheets are 1-based, because that is how the viewer numbers them and
    # sending somebody to the wrong sheet is worse than sending them nowhere.
    assert pasted["sheets"][0] == 1
    assert pasted["sheets"][-1] == 31
    assert pasted["rerun"] is True


def test_a_rebuilt_set_is_not_diagnosed_as_needing_a_rebuild():
    found = abstentions.diagnose(
        ITEC, region_pages=ITEC_REGIONS, raster_pages=[], converted=True, cad_layers=0
    )
    assert "pasted_code_table" not in [d["key"] for d in found]


def test_one_starved_rule_is_not_a_pattern():
    """A single abstention is ordinary. A dozen at once is a cause."""
    found = abstentions.diagnose(
        ITEC[:1], region_pages=ITEC_REGIONS, converted=False
    )
    assert "pasted_code_table" not in [d["key"] for d in found]


def test_geometry_is_diagnosed_separately_and_offers_no_rerun():
    found = abstentions.diagnose(ITEC, region_pages=[], converted=False, cad_layers=0)
    layers = next(d for d in found if d["key"] == "no_layers")
    assert layers["rules"] == ["MEASURE.EGRESS_EXTENT"]
    # Re-running changes nothing about a layer name.
    assert layers["rerun"] is False
    assert "no CAD layers at all" in layers["headline"]


def test_geometry_switched_off_is_not_reported_as_a_problem():
    found = abstentions.diagnose(
        [{"rule": "MEASURE.EGRESS_EXTENT",
          "reason": "geometric measurement was switched off in the review options"}],
        measured_off=True,
    )
    assert found == []


def test_a_clean_review_is_diagnosed_with_nothing():
    assert abstentions.diagnose([], region_pages=[], raster_pages=[]) == []


def test_a_scanned_set_names_its_sheets():
    found = abstentions.diagnose([], raster_pages=[3, 4], converted=False)
    scanned = next(d for d in found if d["key"] == "scanned_sheets")
    assert scanned["sheets"] == [4, 5]
    assert scanned["rerun"] is True


def test_a_rule_that_raised_is_a_bug_and_says_so():
    found = abstentions.diagnose([{"rule": "EGRESS.DEAD_END", "reason": "rule raised: KeyError"}])
    raised = next(d for d in found if d["key"] == "rule_error")
    assert raised["rerun"] is False
    assert raised["rules"] == ["EGRESS.DEAD_END"]


# ══ the pre-filled verdict ════════════════════════════════════════════════
def test_prefill_suggests_the_verdict_the_class_implies():
    assert abstentions.prefill("no door schedule extracted") == "data_on_sheet"
    assert abstentions.prefill("no CAD layer matching 'egress path'") == "layer_named_differently"
    assert abstentions.prefill("Table 601 row not carried in this build's corpus") == "corpus_missing"
    assert abstentions.prefill("rule raised") == "rule_failed"


def test_prefill_suggests_nothing_where_the_abstention_was_probably_right():
    """Pre-selecting a complaint is putting words in somebody's mouth."""
    assert abstentions.prefill("neither the drawings nor the declaration state this") is None
    assert abstentions.prefill("geometric measurement was switched off in the review options") is None
    assert abstentions.prefill("something nobody has classified") is None


def test_prefill_follows_the_context_revision():
    reason = "neither the drawings nor the declaration state this"
    assert abstentions.prefill(reason, unread_pasted_tables=True) == "data_in_image"


def test_every_prefilled_verdict_exists_in_the_taxonomy():
    """A suggestion the form cannot select would open on nothing at all."""
    from webapp import feedback_schema

    reasons = _corpus_reasons() | {"neither the drawings nor the declaration state this"}
    for reason in reasons:
        for context in (False, True):
            suggested = abstentions.prefill(reason, unread_pasted_tables=context)
            if suggested is None:
                continue
            verdict = feedback_schema.lookup("standdown", suggested)
            assert verdict is not None, f"{suggested!r} is not a standdown verdict"
