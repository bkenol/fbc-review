"""The result check in the worker: its stage, its trace, its replay, and its floor."""
from __future__ import annotations

import json

import pytest

from conftest import FakeJobStore, FakeStorage, make_pdf
from fbcreview.ai.prompt import PROMPT_VERSION
from fbcreview.ai.readings import Readings, file_sha256
from fbcreview.ai.schema import FieldReading, RereadRequest, ResultReview, SheetReading
from fbcreview.options import ReviewOptions
from webapp import storage as storage_mod
from webapp.worker import AI_REVIEW, AI_STAGE, REVIEW_STAGE, run_review, stages_for

ON = {"FBC_AI_READING": "on", "ANTHROPIC_API_KEY": "sk-ant-test"}


@pytest.fixture
def ai_on(monkeypatch, tmp_path):
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FBC_AI_REVIEW", raising=False)
    monkeypatch.delenv("FBC_AI_MAX_PASSES", raising=False)
    monkeypatch.setenv("FBC_AI_CACHE_DIR", str(tmp_path / "ai-cache"))


def _job(store, files, job_id="job-rv", rerun_of=None, blob=None):
    blob = blob or storage_mod.upload_path(job_id, "Set.pdf")
    if blob not in files.blobs:
        files.blobs[blob] = make_pdf(pages=2)
    store.create(job_id=job_id, uid="u", email="allowed@example.com", filename="Set.pdf",
                 size_bytes=len(files.blobs[blob]), pages=2, options={}, upload_blob=blob,
                 stages=stages_for(False))
    run_review(job_id=job_id, uid="u", email="allowed@example.com", filename="Set.pdf",
               upload_blob=blob, options=ReviewOptions(), store=store, store_files=files,
               rerun_of=rerun_of)
    return store.get(job_id), blob


def _reader(calls):
    """Stands in for `read_document`: the first pass plants a value the sheet does
    not print; a focused re-read returns the one it does (make_pdf's OCCUPANT LOAD 70)."""
    def read_document(src, config, cache=None, identity=None, focus=None, **_kw):
        calls.append(focus)
        if focus:
            return Readings(identity or file_sha256(src), config.model, PROMPT_VERSION,
                            sheets={p: SheetReading(fields=[FieldReading(
                                field="occupant_load", value="70", quote="OCCUPANT LOAD 70")])
                                for p in focus})
        return Readings(identity or file_sha256(src), config.model, PROMPT_VERSION, sheets={
            0: SheetReading(sheet_number="G-0", fields=[
                FieldReading(field="occupant_load", value="999", quote="OCCUPANT LOAD: 999")])})
    return read_document


def _checker(calls, answers):
    def check_result(client, config, system, content):
        calls.append(content)
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a, {"input_tokens": 100, "output_tokens": 5}
    return check_result


ASK = ResultReview(meets_request=False, rereads=[RereadRequest(page=1, fields=["occupant_load"],
                                                               hint="bottom strip")])
MET = ResultReview(meets_request=True)


def test_the_check_is_a_stage_after_the_rules_only_with_ai_reading(monkeypatch):
    monkeypatch.delenv("FBC_AI_READING", raising=False)
    assert REVIEW_STAGE not in stages_for(False)
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FBC_AI_REVIEW", raising=False)
    stages = stages_for(False)
    assert stages.index(REVIEW_STAGE) == stages.index("Running rules") + 1
    assert stages.index(AI_STAGE) < stages.index(REVIEW_STAGE)
    monkeypatch.setenv("FBC_AI_REVIEW", "off")
    assert REVIEW_STAGE not in stages_for(False) and AI_STAGE in stages_for(False)


def test_the_trace_is_stored_and_the_summary_counts_it(ai_on, monkeypatch):
    reads, checks = [], []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _reader(reads))
    monkeypatch.setattr("fbcreview.ai.reviewer.check_result", _checker(checks, [ASK, MET]))
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done", record.get("error")
    trace = json.loads(files.blobs[f"outputs/job-rv/{AI_REVIEW}"])
    assert trace["passes"] >= 2 and reads[1] == {0: reads[1][0]}
    summary = record["summary"]["ai_review"]
    assert summary["passes"] == trace["passes"] and summary["max_passes"] == 3
    body = json.loads(files.blobs["outputs/job-rv/findings.json"])
    assert body["summary"]["ai_review"] == summary
    # The output contract is otherwise the one it always was.
    assert all({"key", "rect", "evidence"} <= set(f) for f in body["findings"])


def test_no_configuration_runs_more_than_three_passes(ai_on, monkeypatch):
    monkeypatch.setenv("FBC_AI_MAX_PASSES", "12")
    reads, checks = [], []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _reader(reads))
    monkeypatch.setattr("fbcreview.ai.reviewer.check_result", _checker(checks, [ASK]))
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done"
    assert record["summary"]["ai_review"]["passes"] <= 3 and len(checks) <= 3


def test_a_rerun_replays_the_check_with_no_call(ai_on, monkeypatch):
    reads, checks = [], []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _reader(reads))
    monkeypatch.setattr("fbcreview.ai.reviewer.check_result", _checker(checks, [ASK, MET]))
    store, files = FakeJobStore(), FakeStorage()
    _first, blob = _job(store, files, job_id="job-1")
    calls_before = (len(reads), len(checks))
    second, _ = _job(store, files, job_id="job-2", rerun_of="job-1", blob=blob)
    assert second["state"] == "done"
    assert (len(reads), len(checks)) == calls_before, "the re-run called the model"
    first = json.loads(files.blobs["outputs/job-1/findings.json"])["findings"]
    again = json.loads(files.blobs["outputs/job-2/findings.json"])["findings"]
    assert [f["key"] for f in first] == [f["key"] for f in again]


def test_a_failing_check_leaves_the_first_pass_and_a_finished_job(ai_on, monkeypatch):
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _reader([]))
    monkeypatch.setattr("fbcreview.ai.reviewer.check_result",
                        _checker([], [RuntimeError("API down")]))
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done", record.get("error")
    assert record["summary"]["ai_review"]["outcome"] == "review_failed"
    assert record["summary"]["ai_review"]["passes"] == 1


def test_with_the_check_off_nothing_is_checked_or_stored(ai_on, monkeypatch):
    monkeypatch.setenv("FBC_AI_REVIEW", "off")
    checks = []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _reader([]))
    monkeypatch.setattr("fbcreview.ai.reviewer.check_result", _checker(checks, [MET]))
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done" and not checks
    assert f"outputs/job-rv/{AI_REVIEW}" not in files.blobs
    assert record["summary"]["ai_review"] is None


def test_config_says_whether_results_are_checked(client, monkeypatch):
    monkeypatch.delenv("FBC_AI_READING", raising=False)
    assert client.get("/api/config").json()["ai_review"] is False
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FBC_AI_REVIEW", raising=False)
    on = client.get("/api/config").json()
    assert on["ai_review"] is True and REVIEW_STAGE in on["stages"]
