"""The Project Declaration: reconciliation, dual evaluation, and the ITEC run.

Three things are under test here, in order of how much they matter:

1. **Nothing moved.** A review submitted with no declaration must produce
   exactly what it produced before this feature existed. That is checked against
   a baseline captured by running the pre-change engine, not against a
   hand-written expectation.
2. **The ITEC before and after.** The set that returned nothing, reviewed again
   with a declaration built from its own G-002 values.
3. **The normaliser and the tolerances**, because almost every false conflict
   this feature could produce would come from formatting rather than from
   disagreement.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fbcreview import declaration_schema as S
from fbcreview.codes import editions as E
from fbcreview.declaration import ProjectDeclaration
from fbcreview.pipeline import build_facts
from fbcreview.reconcile import (AS_DECLARED, AS_DRAWN, CONFLICT, CORROBORATED,
                                 DECLARED_ONLY, agree, normalise, reconcile)
from fbcreview.rules import registered, run_all
from fixtures.permit_sets import (DIVERGENCE_DECLARATION, ITEC_DECLARATION,
                                  ITEC_STATED_LOAD, ITEC_TOTAL_SF, divergent,
                                  itec, sculpted_like)

BASELINE = json.loads(
    (Path(__file__).parent / "baseline_no_declaration.json").read_text(encoding="utf-8")
)
#: The rules that existed before this feature. Everything else is new, and a new
#: rule firing on a set nobody declared anything about is a feature, not drift.
PRE_EXISTING = set(BASELINE["rules"])


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    """The three fixtures, written once and reused. Building a PDF is not free."""
    root = tmp_path_factory.mktemp("sets")
    built = {}
    for name, data in (("sculpted_like", sculpted_like()),
                       ("itec_raster", itec(True)),
                       ("itec_live", itec(False)),
                       ("divergent", divergent())):
        path = root / f"{name}.pdf"
        path.write_bytes(data)
        built[name] = str(path)
    return built


def review(path, declaration=None, as_of=dt.date(2026, 8, 23)):
    facts = build_facts(path)
    # The edition rules take "today" as an input rather than reaching for it, so
    # the suite does not start failing on 31 December 2026.
    facts.meta["as_of"] = as_of
    return run_all(facts, None, declaration)


# ══════════════════════════════════════════════════════════════════════════
# 1. Nothing moved
# ══════════════════════════════════════════════════════════════════════════
def test_no_declaration_reproduces_the_baseline_byte_for_byte(sets):
    """The whole refactor's guard.

    `sculpted_like` is a cleanly plotted set with a readable code data block —
    the shape the corpus was written against, and the one where rules actually
    fire. With no declaration its findings must be identical, field for field,
    to what the engine produced before the declaration existed.
    """
    res = review(sets["sculpted_like"])
    got = [f.to_dict() for f in res.findings]
    expected = BASELINE["sets"]["sculpted_like"]["findings"]

    # `scenario` and `basis` are new fields and carry their defaults on a run
    # with no declaration; everything else must match exactly.
    for f in got:
        assert f.pop("scenario") == "both"
        assert f.pop("basis") == "drawings"
    assert got == expected


@pytest.mark.parametrize("name", ["sculpted_like", "itec_raster", "itec_live"])
def test_pre_existing_rules_are_untouched_without_a_declaration(sets, name):
    """New rules may fire where the drawings themselves supply their inputs —
    that is the point of them. What may not change is what the rules that
    already existed report."""
    res = review(sets[name])
    got = sorted(
        (f.rule_id, f.fid, f.status, f.severity, f.result)
        for f in res.findings if f.rule_id in PRE_EXISTING
    )
    expected = sorted(
        (f["rule_id"], f["fid"], f["status"], f["severity"], f["result"])
        for f in BASELINE["sets"][name]["findings"] if f["rule_id"] in PRE_EXISTING
    )
    assert got == expected

    got_abstained = sorted(a.rule_id for a in res.abstentions if a.rule_id in PRE_EXISTING)
    assert got_abstained == sorted(BASELINE["sets"][name]["abstentions"])


def test_an_empty_declaration_is_the_same_as_no_declaration(sets):
    a = review(sets["sculpted_like"], None)
    b = review(sets["sculpted_like"], ProjectDeclaration())
    assert [f.to_dict() for f in a.findings] == [f.to_dict() for f in b.findings]


def test_every_rule_fires_or_abstains(sets):
    """Silence is a bug: "not checked" must never be indistinguishable from
    "checked and passed"."""
    for name in sets:
        for declaration in (None, ProjectDeclaration.from_dict(ITEC_DECLARATION)):
            res = review(sets[name], declaration)
            spoke = {f.rule_id for f in res.findings} | {a.rule_id for a in res.abstentions}
            missing = set(registered()) - spoke
            assert not missing, f"{name}: silent rules {sorted(missing)}"


@pytest.mark.skipif(not os.environ.get("FBC_TEST_PDF"),
                    reason="set FBC_TEST_PDF to check against the real permit set")
def test_the_real_set_is_unchanged_without_a_declaration():
    """The same guarantee against the genuine Sculpted Hot Pilates set, when it
    is available. The synthetic fixture is a stand-in, not a substitute."""
    path = os.environ["FBC_TEST_PDF"]
    a = run_all(build_facts(path))
    b = run_all(build_facts(path), None, ProjectDeclaration())
    assert [f.to_dict() for f in a.findings] == [f.to_dict() for f in b.findings]
    for f in a.findings:
        assert f.scenario == "both" and f.basis == "drawings"


# ══════════════════════════════════════════════════════════════════════════
# 2. The ITEC before and after
# ══════════════════════════════════════════════════════════════════════════
def test_itec_as_plotted_reproduces_the_documented_baseline(sets):
    """`docs/reference/ITEC Alico Park Findings.md`: 0 findings, 12 reasoned
    abstentions, because the code-analysis block is a picture."""
    res = review(sets["itec_raster"])
    pre = [f for f in res.findings if f.rule_id in PRE_EXISTING]
    pre_abstained = [a for a in res.abstentions if a.rule_id in PRE_EXISTING]
    assert pre == []
    assert len(pre_abstained) == 12


def test_itec_abstentions_fall_sharply_with_a_declaration(sets):
    before = review(sets["itec_live"])
    after = review(sets["itec_live"], ProjectDeclaration.from_dict(ITEC_DECLARATION))

    assert len(after.abstentions) < len(before.abstentions)
    # Well below, not marginally below: this is the measurable point of the
    # feature. Anything less means it is wired wrong.
    assert len(after.abstentions) <= len(before.abstentions) / 2
    assert len(after.findings) > len(before.findings)


def test_itec_code_edition_rule_fires(sets):
    res = review(sets["itec_live"], ProjectDeclaration.from_dict(ITEC_DECLARATION))
    fired = [f for f in res.findings if f.rule_id == "CODE.EDITION_CURRENT"]
    assert len(fired) == 1
    assert fired[0].status == "OPEN" and fired[0].severity == "CRITICAL"
    assert "7th Edition" in fired[0].result


def test_the_edition_rule_reads_its_dates_from_the_corpus():
    """Encoded as data keyed by edition, so December 2026 does not break it."""
    assert E.in_force(dt.date(2026, 8, 23)).key == "fbc2023"
    assert E.in_force(dt.date(2027, 1, 5)).key == "fbc2026"
    assert E.superseded_by("fbc2023", dt.date(2026, 8, 23)) is None
    assert E.superseded_by("fbc2023", dt.date(2027, 1, 5)).key == "fbc2026"


def test_itec_occupant_load_recomputes_107_against_the_152_stated(sets):
    """Finding H-02, mechanically. Business is 150 gross, read out of the
    Table 1004.5 data rather than written into the rule."""
    res = review(sets["itec_live"], ProjectDeclaration.from_dict(ITEC_DECLARATION))
    fired = [f for f in res.findings if f.rule_id == "EGRESS.OCCUPANT_LOAD_COMPUTED"]
    assert len(fired) == 1
    assert fired[0].status == "OPEN"
    assert "107 occupants" in fired[0].result
    assert f"states {ITEC_STATED_LOAD}" in fired[0].result
    assert f"{ITEC_TOTAL_SF:,} SF" in fired[0].result


def test_itec_mixed_occupancy_conflict_fires(sets):
    """Finding H-01: `MIXED OCCUPANCY? NO` beside `SEPARATED PER TABLE 508.4`,
    against a declaration that says the building is not mixed."""
    res = review(sets["itec_live"], ProjectDeclaration.from_dict(ITEC_DECLARATION))
    fired = [f for f in res.findings if f.rule_id == "DECL.MIXED_OCCUPANCY"]
    assert len(fired) == 1
    assert fired[0].status == "CONFLICT"
    assert fired[0].severity == "HIGH"          # it changes which chapter applies
    assert "508.4" in fired[0].result
    assert "MIXED OCCUPANCY? NO" in fired[0].result


def test_agreement_is_reported_as_verified_not_silently(sets):
    """The reward for answering has to be visible, or nobody answers."""
    res = review(sets["itec_live"], ProjectDeclaration.from_dict(ITEC_DECLARATION))
    corroborated = [f for f in res.findings
                    if f.rule_id.startswith("DECL.") and f.status == "PASS"]
    assert corroborated, "corroboration produced no visible record"
    assert all(f.severity == "VERIFIED" and f.basis == "both" for f in corroborated)


def test_confidence_is_upgraded_when_both_sources_agree(sets):
    rf = reconcile(build_facts(sets["itec_live"]),
                   ProjectDeclaration.from_dict(ITEC_DECLARATION))
    rec = rf.fields["construction_type"]
    assert rec.state == CORROBORATED
    evidence = rec.evidence()
    assert evidence.confidence == "high"
    assert "declaration" in evidence.source and "two independent sources" in evidence.note


# ══════════════════════════════════════════════════════════════════════════
# 3. The normaliser
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("key,a,b", [
    ("construction_type", "II-B", "IIB"),
    ("construction_type", "II-B", "Type II-B"),
    ("construction_type", "II-B", "type ii-b"),
    ("construction_type", "II-B", "TYPE 2B"),
    ("construction_type", "V-A", "va"),
    ("height_ft", "26'-4\"", 26.33),
    ("height_ft", 26.0, "26 FT"),
    ("mixed_occupancy", "Yes", True),
    ("mixed_occupancy", "NO", False),
    ("occupancy_group", "B", "BUSINESS"),
    ("occupancy_group", "B", "Group B"),
    ("occupancy_group", "M", "Mercantile"),
    ("risk_category", "II", "2"),
    ("risk_category", "III", "iii"),
    ("sprinkler_system", "nfpa13", "NFPA 13"),
    ("sprinkler_system", "nfpa13", True),
    ("code_edition", "fbc2020", "7th Edition"),
    ("code_edition", "fbc2023", "8TH EDITION"),
    ("exposure_category", "B", "Exposure B"),
    ("building_area_sf", 15376.0, "15,376 SF"),
    ("jurisdiction", "Lee County, FL", "LEE COUNTY, FLORIDA"),
])
def test_the_same_answer_written_two_ways_is_not_a_conflict(key, a, b):
    assert normalise(key, a) == normalise(key, b) or agree(key, a, b), (
        f"{key}: {a!r} and {b!r} read as different answers"
    )


@pytest.mark.parametrize("key,a,b", [
    ("construction_type", "II-B", "II-A"),
    ("construction_type", "II-B", "V-B"),
    ("occupancy_group", "B", "M"),
    ("risk_category", "II", "III"),
    ("mixed_occupancy", True, False),
    ("code_edition", "fbc2020", "fbc2023"),
    ("exposure_category", "B", "C"),
    ("sprinkler_system", "nfpa13", "none"),
    ("sprinkler_system", "nfpa13", "nfpa13r"),
])
def test_a_genuinely_different_answer_is_a_conflict(key, a, b):
    assert not agree(key, a, b)


def test_a_drawing_that_says_only_yes_cannot_contradict_a_named_standard():
    """`SPRINKLERED: YES` establishes that a system exists and nothing more, so
    it must not be reported as disagreeing with `NFPA 13`. It does disagree
    with `none`."""
    assert agree("sprinkler_system", "nfpa13", True)
    assert agree("sprinkler_system", "nfpa13r", "YES")
    assert not agree("sprinkler_system", "none", "YES")


# ══════════════════════════════════════════════════════════════════════════
# 4. Tolerances
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("key,a,b,expected", [
    ("building_area_sf", 15376, 15380, True),      # rounding
    ("building_area_sf", 15376, 15600, True),      # inside 2 percent
    ("building_area_sf", 15376, 18000, False),     # a different building
    ("total_area_sf", 900, 940, True),             # the 50 sf floor, on a small one
    ("total_area_sf", 900, 1200, False),
    ("height_ft", 26.33, 26.3333, True),
    ("height_ft", 26.33, 26.7, True),
    ("height_ft", 26.33, 28.0, False),
    ("wind_speed_mph", 155, 155.4, True),
    ("wind_speed_mph", 155, 160, False),
    ("stories", 1, 1, True),
    ("stories", 1, 2, False),                      # there is no half storey
])
def test_tolerances_ignore_rounding_and_catch_disagreement(key, a, b, expected):
    assert agree(key, a, b) is expected


def test_every_numeric_field_declares_its_tolerance_or_is_exact():
    for field in S.FIELDS:
        if field.kind == S.NUMBER:
            assert field.tolerance is not None, f"{field.key} has no tolerance"
        if field.kind == S.INTEGER:
            assert field.tolerance is None, f"{field.key} must compare exactly"


# ══════════════════════════════════════════════════════════════════════════
# 5. Dual evaluation
# ══════════════════════════════════════════════════════════════════════════
def test_one_conflicting_field_produces_exactly_two_runs(sets, monkeypatch):
    """Five conflicting fields would still produce two runs, not thirty-two."""
    import fbcreview.rules as rules

    calls = []
    original = rules._run_once
    monkeypatch.setattr(rules, "_run_once",
                        lambda facts: (calls.append(facts.meta.get("scenario")),
                                       original(facts))[1])
    review(sets["divergent"], ProjectDeclaration.from_dict(DIVERGENCE_DECLARATION))
    assert calls == [AS_DRAWN, AS_DECLARED]


def test_no_declaration_runs_the_corpus_once(sets, monkeypatch):
    import fbcreview.rules as rules

    calls = []
    original = rules._run_once
    monkeypatch.setattr(rules, "_run_once",
                        lambda facts: (calls.append(facts.meta.get("scenario")),
                                       original(facts))[1])
    review(sets["sculpted_like"])
    assert calls == [AS_DRAWN]


def test_findings_untouched_by_the_conflict_appear_once_marked_both(sets):
    res = review(sets["divergent"], ProjectDeclaration.from_dict(DIVERGENCE_DECLARATION))
    edition = [f for f in res.findings if f.rule_id == "CODE.EDITION_CURRENT"]
    assert len(edition) == 1, "a finding present in both runs was printed twice"
    assert edition[0].scenario == "both"


def test_a_check_that_diverges_is_reported_under_both_readings(sets):
    """Type II-B allows 69,000 SF and 75 ft; Type V-B allows 27,000 and 60 ft.
    The same 40,000 SF at 62 ft therefore passes as drawn and fails as
    declared, and both outcomes have to reach the register."""
    res = review(sets["divergent"], ProjectDeclaration.from_dict(DIVERGENCE_DECLARATION))
    by_rule = {}
    for f in res.findings:
        by_rule.setdefault(f.rule_id, []).append(f)

    area = by_rule["HEIGHT_AREA.TABLE_506_AREA"]
    assert {f.scenario for f in area} == {AS_DRAWN, AS_DECLARED}
    assert {f.status for f in area} == {"PASS", "OPEN"}
    drawn = next(f for f in area if f.scenario == AS_DRAWN)
    declared = next(f for f in area if f.scenario == AS_DECLARED)
    assert drawn.status == "PASS" and "69,000" in drawn.result
    assert declared.status == "OPEN" and "27,000" in declared.result

    height = by_rule["HEIGHT_AREA.TABLE_504_HEIGHT"]
    assert {f.scenario for f in height} == {AS_DRAWN, AS_DECLARED}


def test_the_conflicting_field_itself_is_reported_once(sets):
    res = review(sets["divergent"], ProjectDeclaration.from_dict(DIVERGENCE_DECLARATION))
    conflict = [f for f in res.findings if f.rule_id == "DECL.CONSTRUCTION_TYPE"]
    assert len(conflict) == 1
    assert conflict[0].status == "CONFLICT"
    assert "V-B" in conflict[0].title and "II-B" in conflict[0].title


def test_free_text_disagreement_does_not_trigger_a_second_run(sets, monkeypatch):
    """Zoning and jurisdiction feed no rule. A difference there belongs on the
    declaration page, and must not double the work."""
    import fbcreview.rules as rules

    calls = []
    original = rules._run_once
    monkeypatch.setattr(rules, "_run_once",
                        lambda facts: (calls.append(facts.meta.get("scenario")),
                                       original(facts))[1])
    declaration = ProjectDeclaration.from_dict(
        {**ITEC_DECLARATION, "mixed_occupancy": True, "zoning": "C-2"})
    review(sets["itec_live"], declaration)
    assert calls == [AS_DRAWN]


# ══════════════════════════════════════════════════════════════════════════
# 6. A blank field is not permission to guess
# ══════════════════════════════════════════════════════════════════════════
def test_an_unanswered_field_never_becomes_a_default(sets):
    res = review(sets["itec_raster"], ProjectDeclaration(occupancy_group="B"))
    standing_down = {a.rule_id for a in res.abstentions}
    # Nothing was declared about construction type and the sheets are pictures,
    # so every Chapter 5 rule must stand down rather than assume a type.
    assert "HEIGHT_AREA.TABLE_504_HEIGHT" in standing_down
    assert "HEIGHT_AREA.TABLE_506_AREA" in standing_down
    assert "FIRE.TABLE_601" in standing_down


def test_a_finding_resting_on_the_declaration_says_so(sets):
    res = review(sets["itec_raster"],
                 ProjectDeclaration.from_dict(ITEC_DECLARATION))
    chapter5 = [f for f in res.findings if f.rule_id.startswith("HEIGHT_AREA.")]
    assert chapter5, "the declaration unlocked nothing on an unreadable set"
    assert all(f.basis == "declaration" for f in chapter5)


def test_the_declaration_never_overrides_the_drawings_in_the_markup(sets):
    """The AHJ reviews the sheet, so an as-declared-only finding gets no marker."""
    from fbcreview.render.markup import Renderer

    res = review(sets["divergent"], ProjectDeclaration.from_dict(DIVERGENCE_DECLARATION))
    r = Renderer(sets["divergent"], res.findings, build_facts(sets["divergent"]).sheets,
                 __import__("fbcreview.options", fromlist=["ReviewOptions"]).ReviewOptions(),
                 res.abstentions, res.reconciled)
    try:
        declared_only = [f for f in res.findings if f.scenario == AS_DECLARED]
        assert declared_only
        assert all(r._box(f) is None for f in declared_only)
    finally:
        r.doc.close()


# ══════════════════════════════════════════════════════════════════════════
# 7. The schema is the single source of truth
# ══════════════════════════════════════════════════════════════════════════
def test_every_declaration_field_has_both_vocabularies():
    for field in S.FIELDS:
        assert field.pro_label and field.pro_help
        assert field.simple_label and field.simple_help
        assert field.group in {k for k, _ in S.GROUPS}
        if field.kind == S.ENUM:
            assert field.choices, f"{field.key} is an enum with no choices"
            assert set(field.choice_labels or {}) == set(field.choices)


def test_every_dataclass_field_is_in_the_schema():
    from fbcreview.declaration import FIELD_NAMES

    assert set(FIELD_NAMES) == set(S.BY_KEY)


def test_every_unlocked_rule_actually_exists():
    """`unlocks` drives what the client tells a user they will get. A rule id
    that does not exist is a promise the engine cannot keep."""
    known = set(registered())
    for field in S.FIELDS:
        for rule_id in field.unlocks:
            assert rule_id in known, f"{field.key} unlocks unknown rule {rule_id}"


def test_the_schema_rejects_what_it_does_not_recognise():
    assert S.validate({"occupancy_group": "B"}) == []
    assert any("Z-9" in p for p in S.validate({"occupancy_group": "Z-9"}))
    assert any("stories" in p for p in S.validate({"stories": 1.5}))
    assert any("height_ft" in p for p in S.validate({"height_ft": "tall"}))
    assert any("nope" in p for p in S.validate({"nope": 1}))
