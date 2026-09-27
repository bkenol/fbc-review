"""The guardrails that let a model into the review path, each held by a test.

`CLAUDE.md`, "AI reads; rules decide":

1. A model's output can only become a claim — never a finding, a severity, a
   threshold, a citation or an abstention reason.
2. Grounded or discarded.
3. Deterministic floor: with no readings the review completes and calls nothing.
4. Replayable: the same readings give the same findings.
5. Provenance is visible: a value the model located says so.
6. `fbcreview/rules` and `fbcreview/codes` never import `fbcreview/ai` or `anthropic`.
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pymupdf
import pytest

from fbcreview.ai.readings import Readings
from fbcreview.ai.schema import ALLOWED_KEYS, FieldReading, SheetReading
from fbcreview.factstore import AI
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all

ROOT = Path(__file__).resolve().parent.parent


# ══ 6. the rule layer cannot reach the model ══════════════════════════════
def _imports(path: Path, package: str):
    """Every module a file imports, relative imports resolved, function bodies included."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            yield mod
            for a in node.names:
                yield f"{mod}.{a.name}"


def _path(module: str):
    p = ROOT / Path(*module.split("."))
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return None


def _closure(*roots: str):
    seen, queue = set(), list(roots)
    while queue:
        mod = queue.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = _path(mod)
        if path is None:
            continue
        package = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
        for imp in _imports(path, package):
            if imp.startswith("fbcreview") or imp.split(".")[0] == "anthropic":
                queue.append(imp)
    return seen


def _rule_and_code_modules():
    mods = []
    for pkg in ("rules", "codes"):
        for p in sorted((ROOT / "fbcreview" / pkg).glob("*.py")):
            name = "__init__" if p.stem == "__init__" else p.stem
            mods.append(f"fbcreview.{pkg}" if name == "__init__" else f"fbcreview.{pkg}.{name}")
    return mods


@pytest.mark.parametrize("module", _rule_and_code_modules())
def test_a_rule_or_the_code_corpus_cannot_reach_the_model(module):
    reach = _closure(module)
    assert not any(m.startswith("fbcreview.ai") for m in reach), \
        f"{module} reaches {sorted(m for m in reach if m.startswith('fbcreview.ai'))}"
    assert not any(m.split(".")[0] == "anthropic" for m in reach), f"{module} reaches anthropic"


# ══ 1. the schema carries no verdict ═══════════════════════════════════════
def test_a_reading_can_carry_nothing_but_a_located_value():
    assert set(FieldReading.model_fields) == ALLOWED_KEYS
    assert set(SheetReading.model_fields) == {"sheet_number", "fields"}
    for banned in ("severity", "status", "finding", "verdict", "code", "citation",
                   "threshold", "action", "compliant"):
        assert banned not in FieldReading.model_fields


# ══ fixtures ═══════════════════════════════════════════════════════════════
def _set(tmp_path, rows):
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    for i, row in enumerate(rows):
        page.insert_text((40, 100 + i * 15), row, fontsize=9)
    page.insert_text((1100, 760), "G-1", fontsize=15)
    path = tmp_path / "set.pdf"
    doc.save(str(path))
    return str(path)


def _readings(*fields):
    return Readings("sha", "claude-opus-5", "test",
                    sheets={0: SheetReading(sheet_number="G-1", fields=list(fields))})


NOTE = "THIS TENANT SPACE IS DESIGNED FOR AN OCCUPANT LOAD OF 48 PERSONS."


# ══ 3. the deterministic floor ═════════════════════════════════════════════
def test_a_review_without_readings_calls_no_model(tmp_path, monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("the deterministic review constructed an API client")
    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Boom
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    facts = build_facts(_set(tmp_path, ["OCCUPANCY: BUSINESS", NOTE]))
    res = run_all(facts)
    assert res.findings or res.abstentions


# ══ 2. grounded or discarded ═══════════════════════════════════════════════
def test_a_value_the_sheet_does_not_print_never_reaches_a_rule(tmp_path):
    path = _set(tmp_path, ["OCCUPANCY: BUSINESS", NOTE])
    readings = _readings(FieldReading(field="occupant_load", value="999",
                                      quote="OCCUPANT LOAD: 999"))
    facts = build_facts(path, readings=readings)
    assert all(c.value != 999.0 for c in facts.store.claims("occupant_load"))
    assert any(r["value"] == "999" for r in facts.store.rejected)
    res = run_all(facts)
    assert not any("999" in f.result for f in res.findings)
    assert readings.grounded == {"accepted": 0, "rejected": 1}


def test_a_value_only_the_model_could_place_is_used_and_says_so(tmp_path):
    """A sentence, not a label: the deterministic reader has no pair to match.
    The model's quote is on the sheet and names the field, so it stands."""
    path = _set(tmp_path, ["OCCUPANCY: BUSINESS", NOTE])
    plain = build_facts(path)
    assert plain.store.resolve("occupant_load") is None

    readings = _readings(FieldReading(field="occupant_load", value="48",
                                      quote="OCCUPANT LOAD OF 48 PERSONS"))
    facts = build_facts(path, readings=readings)
    r = facts.store.resolve("occupant_load")
    assert r.value == 48.0 and r.methods == [AI]
    # 5. provenance is visible
    assert "read by AI" in r.evidence().note


def test_two_readers_agreeing_is_high_confidence(tmp_path):
    path = _set(tmp_path, ["TOTAL OCCUPANT LOAD: 152"])
    readings = _readings(FieldReading(field="occupant_load", value="152",
                                      quote="TOTAL OCCUPANT LOAD: 152"))
    r = build_facts(path, readings=readings).store.resolve("occupant_load")
    assert r.confidence == "high" and r.methods == ["ai", "pair"]


def test_a_model_disagreeing_with_the_sheet_does_not_become_a_disagreement_in_the_set(tmp_path):
    """The deterministic reading stands; the model's is not a rival."""
    path = _set(tmp_path, ["TOTAL OCCUPANT LOAD: 152", "SEE A-101 FOR 48 SEATS"])
    readings = _readings(FieldReading(field="occupant_load", value="48",
                                      quote="SEE A-101 FOR 48 SEATS"))
    r = build_facts(path, readings=readings).store.resolve("occupant_load")
    assert r.value == 152.0 and not r.conflict


# ══ 4. replayable ══════════════════════════════════════════════════════════
def test_the_same_readings_give_the_same_findings(tmp_path):
    path = _set(tmp_path, ["OCCUPANCY: BUSINESS", "RISK CATEGORY: II", NOTE])
    readings = _readings(FieldReading(field="occupant_load", value="48",
                                      quote="OCCUPANT LOAD OF 48 PERSONS"))
    a = [f.to_dict() for f in run_all(build_facts(path, readings=readings)).findings]
    b = [f.to_dict() for f in run_all(build_facts(
        path, readings=Readings.from_json(readings.to_json()))).findings]
    assert a == b


def test_readings_survive_a_round_trip_through_storage():
    r = _readings(FieldReading(field="egress.common_path", value="50 LF",
                               quote="COMMON PATH (1006.2.1): 50 LF", role="required"))
    r.errors = {3: "refused: declined"}
    back = Readings.from_json(r.to_json())
    assert back.sheets[0].fields[0].role == "required"
    assert back.errors == {3: "refused: declined"}
