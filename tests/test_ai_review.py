"""The result reviewer: it checks, corrects and verifies — labelled, evidenced, three passes.

`CLAUDE.md`, rule 7, as amended by the owner on 2026-09-28 to let the reviewer
change findings directly. The earlier version of this file held the opposite
("it can say nothing but re-reads and notes"; "the result is always the
rules' own"): those tests encoded the withdrawn rule and are replaced here by
the rule that took its place. Each clause of it is held below:

* the reviewer may revise, add and withdraw findings, and re-read sheets;
* adding, withdrawing, or moving a severity or status needs a quote printed
  on the sheet — without one the edit is not applied;
* every applied edit is labelled on the finding; a withdrawn finding becomes
  an abstention, never a silent pass;
* pass 1 checks, pass 2 edits, pass 3 verifies — never more than three;
* any failure keeps the last good state; the whole loop replays with no call.

No test makes a network call: the checks and re-reads are functions written
here, and the request shape goes through the real SDK over a mock transport.
"""
from __future__ import annotations

import json

import pymupdf
import pytest

from fbcreview.ai import review as RV
from fbcreview.ai.reader import ReaderConfig
from fbcreview.ai.readings import Readings
from fbcreview.ai.reviewer import ReviewerConfig, check_result, system_prompt
from fbcreview.ai.schema import (EDIT_ALLOWED_KEYS, REREAD_ALLOWED_KEYS, REVIEW_ALLOWED_KEYS,
                                 FieldReading, FindingEdit, RereadRequest, ResultReview,
                                 SheetReading)
from fbcreview.options import ReviewOptions
from fbcreview.payload import findings_payload
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all

NOTE = "THIS TENANT SPACE IS DESIGNED FOR AN OCCUPANT LOAD OF 48 PERSONS."
ROWS = ("OCCUPANCY: BUSINESS", "RISK CATEGORY: III", "TOTAL OCCUPANT LOAD: 48", NOTE,
        "DOOR 104 IS 2'-8\" TO RESTROOM")
READER = ReaderConfig()


def _set(tmp_path, rows=ROWS):
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


def _review(*edits, rereads=(), met=False, notes=()):
    return ResultReview(meets_request=met, rereads=[RereadRequest(**r) for r in rereads],
                        edits=[FindingEdit(**e) for e in edits], notes=list(notes))


class _Checks:
    """A reviewer that answers from a script, one answer per pass, and keeps what it saw."""

    def __init__(self, *answers):
        self.answers, self.calls, self.seen = list(answers), 0, []

    def __call__(self, facts, state, number, history):
        self.calls += 1
        self.seen.append(state)
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer, {"input_tokens": 10, "output_tokens": 2}


def _raise(*_a, **_k):
    raise AssertionError("a replay reached the model")


def _loop(path, checks, reread=_raise, trace=None, replay=None, readings=None):
    trace = trace or _trace()
    readings = readings or _readings()
    facts, result, final = RV.review_loop(_run_pass(path)(readings), readings, _run_pass(path),
                                          checks, reread, trace, replay=replay, pdf_path=path)
    return facts, result, final, trace


SEVERITY_UP = {"op": "revise", "key": "M-03", "severity": "HIGH", "quote": "RISK CATEGORY: III",
               "reason": "Risk Category III drives the structural design; overstating it costs."}


# ══ what the reviewer may say ══════════════════════════════════════════════
def test_a_review_carries_rereads_edits_and_notes_and_nothing_else():
    assert set(ResultReview.model_fields) == REVIEW_ALLOWED_KEYS
    assert set(RereadRequest.model_fields) == REREAD_ALLOWED_KEYS
    assert set(FindingEdit.model_fields) == EDIT_ALLOWED_KEYS


def test_the_reviewer_is_told_to_edit_and_what_an_edit_needs():
    prompt = system_prompt()
    assert "Edit the findings directly" in prompt
    assert "give a quote" in prompt and "is not applied" in prompt
    assert "occupant_load" in prompt
    assert system_prompt() == prompt                    # the cached prefix never varies


def test_each_pass_is_told_its_job_and_the_second_is_the_active_one(tmp_path):
    path = _set(tmp_path)
    facts, result = _run_pass(path)(_readings())
    state = RV.apply_edits(result, [], RV.Sheets(path), [s.code for s in facts.sheets])
    jobs = [RV.packet(path, facts, state, None, None, n, 3, [])[0]["text"] for n in (1, 2, 3)]
    assert "CHECK" in jobs[0] and "EDIT" in jobs[1] and "VERIFY" in jobs[2]
    assert "be active" in jobs[1] and "Act on every note" in jobs[1]
    # With one pass allowed, that pass is the edit pass — the only chance to correct.
    assert "EDIT" in RV.packet(path, facts, state, None, None, 1, 1, [])[0]["text"]


# ══ the switch and the limit ═══════════════════════════════════════════════
def test_the_reviewer_is_off_whenever_ai_reading_is():
    assert ReviewerConfig.from_env(None, {}) is None
    assert ReviewerConfig.from_env(READER, {}) is not None
    assert ReviewerConfig.from_env(READER, {"FBC_AI_REVIEW": "off"}) is None


@pytest.mark.parametrize("given,used", [("", 3), ("2", 2), ("1", 1), ("10", 3), ("0", 1),
                                        ("-4", 1), ("three", 3)])
def test_no_configuration_can_ask_for_more_than_three_passes(given, used):
    assert ReviewerConfig.from_env(READER, {"FBC_AI_MAX_PASSES": given}).max_passes == used


def test_the_loop_itself_holds_the_limit_whatever_the_trace_says(tmp_path):
    path = _set(tmp_path)
    titles = [_review({"op": "revise", "key": "M-03", "title": f"Title {n}", "reason": "clearer"})
              for n in range(6)]
    checks = _Checks(*titles)
    _, _, _, trace = _loop(path, checks, trace=_trace(max_passes=10))
    assert trace.passes == 3 and checks.calls == 3 and trace.outcome == RV.LIMIT


# ══ editing ════════════════════════════════════════════════════════════════
def test_a_severity_backed_by_the_sheet_is_changed_and_labelled(tmp_path):
    path = _set(tmp_path)
    facts, result, _, trace = _loop(path, _Checks(_review(SEVERITY_UP, met=True)))
    f = next(f for f in result.findings if f.fid == "M-03")
    assert f.severity == "HIGH" and f.status == "OPEN"
    assert "[Revised by AI review, pass 1:" in f.result
    label = trace.revision_map()[("M-03", f.rule_id, f.sheet, f.page, f.scenario)]
    assert label["op"] == "revise" and label["was"] == {"severity": "MEDIUM"}
    assert label["quote"] == "RISK CATEGORY: III"
    body = findings_payload(path, facts, result.findings, revisions=trace.revision_map())
    assert next(d for d in body if d["fid"] == "M-03")["ai_revision"]["changed"] == ["severity"]


def test_a_severity_change_without_a_quote_on_the_sheet_is_not_applied(tmp_path):
    path = _set(tmp_path)
    unbacked = dict(SEVERITY_UP, quote="RISK CATEGORY: IV")
    _, result, _, trace = _loop(path, _Checks(_review(unbacked, met=True)))
    assert next(f for f in result.findings if f.fid == "M-03").severity == "MEDIUM"
    assert trace.records[0].rejected == [
        {"edit": 0, "why": "changing a severity or status needs a quote printed on the sheet"}]
    assert trace.summary()["edits_rejected"] == 1 and not trace.revisions


def test_wording_can_be_revised_without_a_quote(tmp_path):
    path = _set(tmp_path)
    edit = {"op": "revise", "key": "M-03", "result": "The set assigns RC III to 48 people.",
            "remedy": "Use Risk Category II.", "reason": "Plainer, and says what to do."}
    _, result, _, _ = _loop(path, _Checks(_review(edit, met=True)))
    f = next(f for f in result.findings if f.fid == "M-03")
    assert f.result.startswith("The set assigns RC III to 48 people.")
    assert f.action == "Use Risk Category II." and f.severity == "MEDIUM"


def test_a_finding_the_rules_missed_is_added_where_the_sheet_prints_it(tmp_path):
    path = _set(tmp_path)
    add = {"op": "add", "severity": "HIGH", "title": "Door 104 is under 32 in. clear",
           "result": "A 2'-8\" leaf gives about 30 in. clear.", "code": "FBC-B 1010.1.1",
           "remedy": "Use a 3'-0\" leaf.", "page": 1, "quote": "DOOR 104 IS 2'-8\"",
           "reason": "The door is printed on G-1; no rule read it."}
    facts, result, _, trace = _loop(path, _Checks(_review(add, met=True)))
    f = next(f for f in result.findings if f.fid == "AI-01")
    assert (f.rule_id, f.status, f.severity, f.sheet) == (RV.AI_RULE, "OPEN", "HIGH", "G-1")
    assert f.box is not None and "[Raised by AI review, pass 1:" in f.result
    assert "not by a rule in the hand-verified corpus" in f.checked
    assert trace.summary()["findings_added"] == 1
    body = findings_payload(path, facts, result.findings, revisions=trace.revision_map())
    added = next(d for d in body if d["fid"] == "AI-01")
    assert added["ai_revision"]["op"] == "add" and added["rect"] is not None


def test_a_withdrawn_finding_is_an_abstention_not_a_pass(tmp_path):
    path = _set(tmp_path)
    withdraw = {"op": "withdraw", "key": "M-03", "quote": "TOTAL OCCUPANT LOAD: 48",
                "reason": "Test withdrawal."}
    _, result, _, trace = _loop(path, _Checks(_review(withdraw, met=True)))
    assert not any(f.fid == "M-03" for f in result.findings)
    a = next(a for a in result.abstentions if a.reason == "withdrawn by the AI result review")
    assert a.rule_id == "XSHEET.RISK_CATEGORY" and "M-03" in a.detail
    assert trace.summary()["findings_withdrawn"] == 1


@pytest.mark.parametrize("edit,why", [
    ({"op": "revise", "key": "Z-99", "title": "x", "reason": "r"}, "no finding has the key 'Z-99'"),
    ({"op": "revise", "key": "M-03", "status": "PASS", "severity": "HIGH",
      "quote": "RISK CATEGORY: III", "reason": "r"},
     "a passing check cannot carry a problem severity"),
    ({"op": "revise", "key": "M-03", "title": "x", "reason": " "}, "an edit needs a reason"),
    ({"op": "add", "severity": "LOW", "title": "t", "result": "r", "page": 1,
      "quote": "NOT PRINTED ANYWHERE", "reason": "r"}, "the quote was not found on that page"),
    ({"op": "withdraw", "key": "M-03", "reason": "r"},
     "withdrawing a finding needs a quote printed on the sheet"),
])
def test_an_edit_that_does_not_hold_up_is_recorded_and_not_applied(tmp_path, edit, why):
    path = _set(tmp_path)
    first = _run_pass(path)(_readings())
    _, result, _, trace = _loop(path, _Checks(_review(edit, met=True)))
    assert trace.records[0].rejected == [{"edit": 0, "why": why}]
    assert [f.to_dict() for f in result.findings] == [f.to_dict() for f in first[1].findings]


def test_a_verified_severity_makes_a_pass_and_an_open_status_needs_a_severity(tmp_path):
    path = _set(tmp_path)
    to_pass = dict(SEVERITY_UP, severity="VERIFIED")
    _, result, _, _ = _loop(path, _Checks(_review(to_pass, met=True)))
    f = next(f for f in result.findings if f.fid == "M-03")
    assert (f.status, f.severity) == ("PASS", "VERIFIED")


# ══ the passes ═════════════════════════════════════════════════════════════
def test_the_check_pass_hands_its_notes_to_the_edit_pass(tmp_path):
    """Pass 1 changes nothing but notes a problem; the loop goes on to pass 2,
    which sees the note and acts on it."""
    path = _set(tmp_path)
    checks = _Checks(_review(notes=["M-03 understates the risk."]),
                     _review(SEVERITY_UP), _review(met=True))
    history = []
    real = checks.__call__

    def spy(facts, state, number, earlier):
        history.append([h.review.notes for h in earlier if h.review])
        return real(facts, state, number, earlier)
    _, result, _, trace = _loop(path, spy)
    assert history[1] == [["M-03 understates the risk."]]
    assert [r.mode for r in trace.records] == [RV.CHECK, RV.EDIT, RV.VERIFY]
    assert next(f for f in result.findings if f.fid == "M-03").severity == "HIGH"
    assert trace.outcome == RV.MET and trace.passes == 3


def test_an_edit_pass_that_changes_nothing_ends_the_loop(tmp_path):
    path = _set(tmp_path)
    checks = _Checks(_review(notes=["hm"]), _review(notes=["still hm"]))
    _, _, _, trace = _loop(path, checks)
    assert trace.outcome == RV.NO_CHANGE and trace.passes == 2 and checks.calls == 2


def test_edits_survive_a_reread_and_its_rules_run(tmp_path):
    path = _set(tmp_path)
    checks = _Checks(_review(SEVERITY_UP, rereads=[{"page": 1, "fields": ["occupant_load"]}]),
                     _review(met=True))

    def reread(_focus):
        return _readings(FieldReading(field="occupant_load", value="48",
                                      quote="OCCUPANT LOAD OF 48 PERSONS"))
    facts, result, _, trace = _loop(path, checks, reread=reread)
    assert trace.rule_runs == 2
    assert next(f for f in result.findings if f.fid == "M-03").severity == "HIGH"


# ══ every way it stops leaves the last good state ══════════════════════════
def test_a_satisfied_reviewer_with_no_edits_leaves_the_rules_result(tmp_path):
    path = _set(tmp_path)
    first = _run_pass(path)(_readings())
    _, result, _, trace = _loop(path, _Checks(_review(met=True)))
    assert [f.to_dict() for f in result.findings] == [f.to_dict() for f in first[1].findings]
    assert trace.passes == 1 and trace.outcome == RV.MET


def test_a_failed_pass_keeps_the_edits_already_made(tmp_path):
    path = _set(tmp_path)
    checks = _Checks(_review(SEVERITY_UP), RuntimeError("API down"))
    _, result, _, trace = _loop(path, checks)
    assert trace.outcome == RV.FAILED and trace.records[1].error == "RuntimeError"
    assert next(f for f in result.findings if f.fid == "M-03").severity == "HIGH"


def test_a_failed_reread_keeps_the_last_good_state(tmp_path):
    path = _set(tmp_path)

    def broken(_focus):
        raise ConnectionError("overloaded")
    checks = _Checks(_review(rereads=[{"page": 1, "fields": ["occupant_load"]}]))
    first = _run_pass(path)(_readings())
    _, result, _, trace = _loop(path, checks, reread=broken)
    assert trace.outcome == RV.REREAD_FAILED and trace.records[0].error == "ConnectionError"
    assert [f.to_dict() for f in result.findings] == [f.to_dict() for f in first[1].findings]


def test_a_reread_value_the_sheet_does_not_print_is_rejected_like_any_other(tmp_path):
    path = _set(tmp_path, rows=("OCCUPANCY: BUSINESS", NOTE))
    checks = _Checks(_review(rereads=[{"page": 1, "fields": ["occupant_load"]}]),
                     _review(met=True))

    def reread(_focus):
        return _readings(FieldReading(field="occupant_load", value="999",
                                      quote="OCCUPANT LOAD: 999"))
    facts, _, _, _ = _loop(path, checks, reread=reread)
    assert facts.store.resolve("occupant_load") is None
    assert any(r["value"] == "999" for r in facts.store.rejected)


def test_a_page_and_fact_already_reread_is_not_asked_for_again():
    review = _review(rereads=[{"page": 1, "fields": ["occupant_load", "building_area_sf"]}])
    focus = RV.plan_rereads(review, 1, tried={(0, "occupant_load")})
    assert "- building_area_sf" in focus[0] and "- occupant_load" not in focus[0]
    assert RV.plan_rereads(review, 1, tried={(0, "occupant_load"), (0, "building_area_sf")}) == {}


def test_one_pass_rereads_a_bounded_number_of_sheets():
    review = _review(rereads=[{"page": p, "fields": ["occupant_load"]} for p in range(1, 21)])
    assert len(RV.plan_rereads(review, 20, set())) == RV.MAX_REREAD_SHEETS


# ══ replayable ═════════════════════════════════════════════════════════════
def test_a_stored_trace_replays_to_the_same_result_with_no_call(tmp_path):
    path = _set(tmp_path)
    add = {"op": "add", "severity": "HIGH", "title": "Door 104", "result": "Too narrow.",
           "page": 1, "quote": "DOOR 104 IS 2'-8\"", "reason": "Printed on G-1."}
    checks = _Checks(_review(notes=["n"]), _review(SEVERITY_UP, add), _review(met=True))
    _, result, _, live = _loop(path, checks)
    stored = RV.ReviewTrace.from_json(json.loads(json.dumps(live.to_json())))
    _, replayed, _, again = _loop(path, _raise, replay=stored)
    assert [f.to_dict() for f in replayed.findings] == [f.to_dict() for f in result.findings]
    assert again.summary() == live.summary() and again.revisions == live.revisions


def test_a_trace_is_only_replayed_on_the_readings_it_was_made_from():
    t = _trace()
    same = Readings("sha", "claude-opus-5", "test")
    assert RV.replayable(t, same, "claude-opus-5", "t", 3)
    assert not RV.replayable(t, Readings("other", "claude-opus-5", "test"), "claude-opus-5", "t", 3)
    assert not RV.replayable(t, Readings("sha", "claude-opus-5", "v2"), "claude-opus-5", "t", 3)
    assert not RV.replayable(t, same, "claude-opus-5", "t", 2)


def test_the_summary_carries_counts_and_never_notes_or_reasons(tmp_path):
    path = _set(tmp_path)
    _, _, _, trace = _loop(path, _Checks(_review(SEVERITY_UP, met=True,
                                                 notes=["G-1 prints RISK CATEGORY: III"])))
    s = json.dumps(trace.summary())
    assert "RISK" not in s and "overstating" not in s
    assert json.loads(s)["notes"] == 1 and json.loads(s)["findings_revised"] == 1


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
def test_the_packet_carries_keys_the_request_and_the_sheets_but_no_email(tmp_path):
    path = _set(tmp_path)
    facts, result = _run_pass(path)(_readings())
    state = RV.apply_edits(result, [(1, FindingEdit(**SEVERITY_UP))], RV.Sheets(path),
                           [s.code for s in facts.sheets])
    options = ReviewOptions(min_severity="HIGH", notes="focus on egress",
                            email_to=["someone@example.com"])
    blocks = RV.packet(path, facts, state, options, None, 2, 3, [])
    text = " ".join(b["text"] for b in blocks)
    assert "focus on egress" in text and '"min_severity": "HIGH"' in text
    assert "someone@example.com" not in text
    assert '"key": "M-03"' in text and '"ai_review"' in text
    assert "OCCUPANT LOAD OF 48 PERSONS" in text          # the sheet's own text


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
                "edits": [{"op": "revise", "key": "M-03", "severity": "HIGH",
                           "quote": "RISK CATEGORY: III", "reason": "why"}],
                "notes": []})}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = anthropic.Anthropic(
        api_key="sk-ant-test", base_url="http://api.test",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    review, _ = check_result(client, ReviewerConfig.from_env(READER, {}), "system",
                             [{"type": "text", "text": "packet"}])
    assert review.edits[0].severity == "HIGH" and review.rereads[0].fields == ["occupant_load"]
    body = seen["body"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
