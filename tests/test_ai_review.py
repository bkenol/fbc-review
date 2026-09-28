"""The result reviewer: it checks a pass, may send sheets back, and cannot decide anything.

`CLAUDE.md`, "AI reads; rules decide", rule 7 — each clause held here:

* its output can only be re-read requests and notes — never a finding;
* what a re-read returns is grounded like any reading;
* at most three passes, whatever the configuration says;
* any failure leaves the last pass's result standing;
* the whole loop replays from its trace with no call.

No test makes a network call: the checks and re-reads are functions written
here, and the request shape goes through the real SDK over a mock transport.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pymupdf
import pytest

from fbcreview.ai import review as RV
from fbcreview.ai.reader import ReaderConfig
from fbcreview.ai.readings import Readings
from fbcreview.ai.reviewer import ReviewerConfig, check_result, system_prompt
from fbcreview.ai.schema import (REREAD_ALLOWED_KEYS, REVIEW_ALLOWED_KEYS, FieldReading,
                                 RereadRequest, ResultReview, SheetReading)
from fbcreview.options import ReviewOptions
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all

NOTE = "THIS TENANT SPACE IS DESIGNED FOR AN OCCUPANT LOAD OF 48 PERSONS."
READER = ReaderConfig()


def _set(tmp_path, rows=("OCCUPANCY: BUSINESS", NOTE)):
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    for i, row in enumerate(rows):
        page.insert_text((40, 100 + i * 15), row, fontsize=9)
    page.insert_text((1100, 760), "G-1", fontsize=15)
    path = tmp_path / "set.pdf"
    doc.save(str(path))
    return str(path)


def _readings(*fields, page=0):
    return Readings("sha", "claude-opus-5", "test",
                    sheets={page: SheetReading(sheet_number="G-1", fields=list(fields))}
                    if fields else {})


def _trace(max_passes=3):
    return RV.ReviewTrace("sha", "claude-opus-5", "t", max_passes,
                          reader="claude-opus-5|test")


def _run_pass(path):
    def run(r):
        f = build_facts(path, readings=r)
        return f, run_all(f)
    return run


def _asks(*requests, met=False, notes=()):
    return ResultReview(meets_request=met, rereads=[RereadRequest(**r) for r in requests],
                        notes=list(notes))


class _Checks:
    """A reviewer that answers from a script, one answer per pass."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, facts, result, number, history):
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer, {"input_tokens": 10, "output_tokens": 2}


def _raise(*_a, **_k):
    raise AssertionError("a replay reached the model")


# ══ what the reviewer may say ══════════════════════════════════════════════
def test_a_review_can_carry_nothing_but_rereads_and_notes():
    assert set(ResultReview.model_fields) == REVIEW_ALLOWED_KEYS
    assert set(RereadRequest.model_fields) == REREAD_ALLOWED_KEYS
    for banned in ("severity", "status", "finding", "findings", "verdict", "code",
                   "citation", "threshold", "action", "compliant", "abstention"):
        assert banned not in ResultReview.model_fields
        assert banned not in RereadRequest.model_fields


def test_the_reviewer_is_told_it_cannot_change_a_finding():
    prompt = system_prompt()
    assert "nothing you say can add, remove, edit or re-rank a finding" in prompt
    assert "occupant_load" in prompt
    assert system_prompt() == prompt                    # the cached prefix never varies


# ══ the switch and the limit ═══════════════════════════════════════════════
def test_the_reviewer_is_off_whenever_ai_reading_is():
    assert ReviewerConfig.from_env(None, {}) is None
    assert ReviewerConfig.from_env(READER, {}) is not None
    assert ReviewerConfig.from_env(READER, {"FBC_AI_REVIEW": "off"}) is None


@pytest.mark.parametrize("given,used", [("", 3), ("2", 2), ("1", 1), ("10", 3), ("0", 1),
                                        ("-4", 1), ("three", 3)])
def test_no_configuration_can_ask_for_more_than_three_passes(given, used):
    assert ReviewerConfig.from_env(READER, {"FBC_AI_MAX_PASSES": given}).max_passes == used


def test_the_loop_itself_holds_the_limit_whatever_the_trace_says():
    checks, rereads, n = _Checks(_asks({"page": 1, "fields": ["occupant_load"]})), [], [0]

    def run_pass(_r):
        n[0] += 1                                        # a different result every pass
        return _fake_facts(), _fake_result(n[0])

    def reread(focus):
        rereads.append(focus)
        return Readings("sha", "m", "p")
    checks.answers = [_asks({"page": 1, "fields": [k]}) for k in
                      ("occupant_load", "building_area_sf", "occupancy_group", "risk_category",
                       "sprinkler_system")]
    trace = _trace(max_passes=10)
    RV.review_loop((_fake_facts(), _fake_result(0)), Readings("sha", "m", "p"), run_pass,
                   checks, reread, trace)
    assert trace.passes == 3 and checks.calls == 3 and len(rereads) == 2
    assert trace.outcome == RV.LIMIT


def _fake_facts():
    return SimpleNamespace(sheets=[SimpleNamespace(index=0, code="G-1", title="", discipline="")])


def _fake_result(n):
    f = SimpleNamespace(fid=f"X-{n}", rule_id="R", status="OPEN", severity="LOW", sheet="G-1",
                        page=0, result=str(n))
    return SimpleNamespace(findings=[f], abstentions=[])


# ══ a pass that works ══════════════════════════════════════════════════════
def test_a_fact_the_readers_missed_is_found_on_a_second_pass(tmp_path):
    path = _set(tmp_path)
    first = _run_pass(path)(_readings())
    assert first[0].store.resolve("occupant_load") is None

    checks = _Checks(_asks({"page": 1, "fields": ["occupant_load"],
                            "hint": "a sentence near the top: OCCUPANT LOAD OF 48 PERSONS"}),
                     _asks(met=True))
    sent = []

    def reread(focus):
        sent.append(focus)
        return _readings(FieldReading(field="occupant_load", value="48",
                                      quote="OCCUPANT LOAD OF 48 PERSONS"))
    trace = _trace()
    facts, result, final = RV.review_loop(first, _readings(), _run_pass(path), checks,
                                          reread, trace)
    assert facts.store.resolve("occupant_load").value == 48.0
    assert trace.passes == 2 and trace.outcome in (RV.MET, RV.NO_CHANGE)
    # The re-read was asked for the named fact, with the reviewer's pointer.
    assert list(sent[0]) == [0]
    assert "occupant_load" in sent[0][0] and "OCCUPANT LOAD OF 48 PERSONS" in sent[0][0]
    assert "empty answer is correct" in sent[0][0]


def test_the_result_is_always_the_rules_own_over_the_final_readings(tmp_path):
    """The reviewer cannot put anything into the result: rerunning the rules on the
    loop's final readings, with no reviewer at all, gives exactly the same findings."""
    path = _set(tmp_path)
    checks = _Checks(_asks({"page": 1, "fields": ["occupant_load"]},
                           notes=["H-99 should be CRITICAL"]), _asks(met=True))

    def reread(_focus):
        return _readings(FieldReading(field="occupant_load", value="48",
                                      quote="OCCUPANT LOAD OF 48 PERSONS"))
    facts, result, final = RV.review_loop(_run_pass(path)(_readings()), _readings(),
                                          _run_pass(path), checks, reread, _trace())
    again = run_all(build_facts(path, readings=Readings.from_json(final.to_json())))
    assert [f.to_dict() for f in result.findings] == [f.to_dict() for f in again.findings]
    assert not any("H-99" in f.fid or "CRITICAL" == f.severity for f in result.findings)


def test_a_reread_value_the_sheet_does_not_print_is_rejected_like_any_other(tmp_path):
    path = _set(tmp_path)
    checks = _Checks(_asks({"page": 1, "fields": ["occupant_load"]}), _asks(met=True))

    def reread(_focus):
        return _readings(FieldReading(field="occupant_load", value="999",
                                      quote="OCCUPANT LOAD: 999"))
    facts, result, final = RV.review_loop(_run_pass(path)(_readings()), _readings(),
                                          _run_pass(path), checks, reread, _trace())
    assert facts.store.resolve("occupant_load") is None
    assert any(r["value"] == "999" for r in facts.store.rejected)


# ══ every way it stops leaves the last pass standing ═══════════════════════
def test_a_satisfied_reviewer_stops_at_one_pass(tmp_path):
    path = _set(tmp_path)
    first = _run_pass(path)(_readings())
    trace = _trace()
    facts, result, _ = RV.review_loop(first, _readings(), _run_pass(path),
                                      _Checks(_asks(met=True)), _raise, trace)
    assert (facts, result) == first and trace.passes == 1 and trace.outcome == RV.MET


@pytest.mark.parametrize("answer,outcome", [
    (RuntimeError("API down"), RV.FAILED),
    (_asks({"page": 1, "fields": ["not_a_catalog_key"]}), RV.NOTHING),
    (_asks({"page": 40, "fields": ["occupant_load"]}), RV.NOTHING),
    (_asks(notes=["the prose is thin"]), RV.NOTHING),
])
def test_a_check_that_cannot_lead_anywhere_leaves_the_first_pass(tmp_path, answer, outcome):
    path = _set(tmp_path)
    first = _run_pass(path)(_readings())
    trace = _trace()
    facts, result, _ = RV.review_loop(first, _readings(), _run_pass(path), _Checks(answer),
                                      _raise, trace)
    assert (facts, result) == first and trace.outcome == outcome and trace.passes == 1


def test_a_failed_reread_leaves_the_first_pass(tmp_path):
    path = _set(tmp_path)
    first = _run_pass(path)(_readings())

    def broken(_focus):
        raise ConnectionError("overloaded")
    trace = _trace()
    facts, result, _ = RV.review_loop(first, _readings(), _run_pass(path),
                                      _Checks(_asks({"page": 1, "fields": ["occupant_load"]})),
                                      broken, trace)
    assert (facts, result) == first and trace.outcome == RV.REREAD_FAILED
    assert trace.records[0].error == "ConnectionError"


def test_a_reread_that_changes_nothing_ends_the_loop(tmp_path):
    path = _set(tmp_path)
    checks = _Checks(_asks({"page": 1, "fields": ["occupant_load"]}))
    trace = _trace()
    RV.review_loop(_run_pass(path)(_readings()), _readings(), _run_pass(path), checks,
                   lambda _f: Readings("sha", "m", "p"), trace)
    assert trace.outcome == RV.NO_CHANGE and trace.passes == 2 and checks.calls == 1


def test_a_page_and_fact_already_reread_is_not_asked_for_again():
    review = _asks({"page": 1, "fields": ["occupant_load", "building_area_sf"]})
    focus = RV.plan_rereads(review, 1, tried={(0, "occupant_load")})
    assert "- building_area_sf" in focus[0] and "- occupant_load" not in focus[0]
    assert RV.plan_rereads(review, 1, tried={(0, "occupant_load"), (0, "building_area_sf")}) == {}


def test_one_pass_rereads_a_bounded_number_of_sheets():
    review = _asks(*({"page": p, "fields": ["occupant_load"]} for p in range(1, 21)))
    assert len(RV.plan_rereads(review, 20, set())) == RV.MAX_REREAD_SHEETS


# ══ replayable ═════════════════════════════════════════════════════════════
def test_a_stored_trace_replays_to_the_same_result_with_no_call(tmp_path):
    path = _set(tmp_path)
    checks = _Checks(_asks({"page": 1, "fields": ["occupant_load"]}), _asks(met=True))

    def reread(_focus):
        return _readings(FieldReading(field="occupant_load", value="48",
                                      quote="OCCUPANT LOAD OF 48 PERSONS"))
    live = _trace()
    _, result, _ = RV.review_loop(_run_pass(path)(_readings()), _readings(), _run_pass(path),
                                  checks, reread, live)
    stored = RV.ReviewTrace.from_json(json.loads(json.dumps(live.to_json())))
    again = _trace()
    _, replayed, _ = RV.review_loop(_run_pass(path)(_readings()), _readings(),
                                    _run_pass(path), _raise, _raise, again, replay=stored)
    assert [f.to_dict() for f in replayed.findings] == [f.to_dict() for f in result.findings]
    assert (again.passes, again.outcome) == (live.passes, live.outcome)
    assert again.summary() == live.summary()


def test_a_trace_is_only_replayed_on_the_readings_it_was_made_from():
    t = _trace()
    same = Readings("sha", "claude-opus-5", "test")
    assert RV.replayable(t, same, "claude-opus-5", "t", 3)
    assert not RV.replayable(t, Readings("other", "claude-opus-5", "test"), "claude-opus-5", "t", 3)
    assert not RV.replayable(t, Readings("sha", "claude-opus-5", "v2"), "claude-opus-5", "t", 3)
    assert not RV.replayable(t, same, "claude-opus-5", "t", 2)


def test_the_summary_carries_counts_and_never_the_notes():
    t = _trace()
    t.records.append(RV.PassRecord(1, review=_asks(notes=["G-1 prints OCCUPANT LOAD 70"])))
    s = json.dumps(t.summary())
    assert "OCCUPANT" not in s and json.loads(s)["notes"] == 1


# ══ merging readings ═══════════════════════════════════════════════════════
def test_a_reread_adds_to_the_readings_without_touching_the_first_pass():
    base = _readings(FieldReading(field="occupancy_group", value="B", quote="OCCUPANCY: B"))
    base.errors = {0: "failed: ConnectionError", 1: "refused: declined"}
    base.usage = {"input_tokens": 5}
    extra = _readings(FieldReading(field="occupancy_group", value="B", quote="OCCUPANCY: B"),
                      FieldReading(field="occupant_load", value="48", quote="LOAD OF 48"))
    extra.usage = {"input_tokens": 3}
    merged = RV.merge_readings(base, extra)
    assert [f.field for f in merged.sheets[0].fields] == ["occupancy_group", "occupant_load"]
    assert merged.errors == {1: "refused: declined"} and merged.usage == {"input_tokens": 8}
    assert len(base.sheets[0].fields) == 1 and 0 in base.errors


# ══ what the reviewer is shown ═════════════════════════════════════════════
def test_the_packet_carries_the_request_the_result_and_the_sheets_but_no_email(tmp_path):
    path = _set(tmp_path)
    facts, result = _run_pass(path)(_readings())
    options = ReviewOptions(min_severity="HIGH", notes="focus on egress",
                            email_to=["someone@example.com"])
    blocks = RV.packet(path, facts, result, options, None, 1, 3, [])
    text = " ".join(b["text"] for b in blocks)
    assert "focus on egress" in text and '"min_severity": "HIGH"' in text
    assert "someone@example.com" not in text
    assert "OCCUPANT LOAD OF 48 PERSONS" in text          # the sheet's own text
    for a in result.abstentions[:3]:
        assert a.rule_id in text


# ══ the request, through the real SDK ══════════════════════════════════════
def test_the_check_is_a_request_the_sdk_accepts_and_sends_as_designed():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": json.dumps({
                "meets_request": False,
                "rereads": [{"page": 1, "fields": ["occupant_load"], "hint": "top left"}],
                "notes": []})}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = anthropic.Anthropic(
        api_key="sk-ant-test", base_url="http://api.test",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    review, _ = check_result(client, ReviewerConfig.from_env(READER, {}), "system",
                             [{"type": "text", "text": "packet"}])
    assert review.rereads[0].fields == ["occupant_load"] and not review.meets_request
    body = seen["body"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
