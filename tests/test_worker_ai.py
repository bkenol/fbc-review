"""The AI reading stage in the worker: on only when configured, never fatal, replayed on a re-run."""
from __future__ import annotations

import json

import pytest

from conftest import FakeJobStore, FakeStorage, make_pdf
from fbcreview.ai.prompt import PROMPT_VERSION
from fbcreview.ai.readings import Readings, file_sha256
from fbcreview.ai.schema import FieldReading, SheetReading
from fbcreview.options import ReviewOptions
from webapp import storage as storage_mod
from webapp import worker
from webapp.worker import AI_STAGE, READINGS, run_review, stages_for

ON = {"FBC_AI_READING": "on", "ANTHROPIC_API_KEY": "sk-ant-test"}


@pytest.fixture
def ai_on(monkeypatch, tmp_path):
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FBC_AI_CACHE_DIR", str(tmp_path / "ai-cache"))


def _job(store, files, job_id="job-ai", rerun_of=None, blob=None):
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


def _fake_reading(calls):
    """Stands in for `fbcreview.ai.reader.read_document`, keying as it does."""
    def read_document(src, config, cache=None, identity=None, **_kw):
        calls.append(src)
        return Readings(identity or file_sha256(src), config.model, PROMPT_VERSION, sheets={
            0: SheetReading(sheet_number="G-0", fields=[
                FieldReading(field="occupant_load", value="999", quote="OCCUPANT LOAD: 999")])})
    return read_document


def test_the_ai_stage_is_listed_only_when_the_deployment_has_it_on(monkeypatch):
    monkeypatch.delenv("FBC_AI_READING", raising=False)
    assert AI_STAGE not in stages_for(False)
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    assert stages_for(False)[1] == AI_STAGE
    # After the rebuild, because the model reads the file the engine grounds against.
    assert stages_for(True).index(AI_STAGE) == stages_for(True).index(worker.CONVERT_STAGE) + 1


def test_with_ai_off_nothing_is_read_or_stored(monkeypatch):
    monkeypatch.delenv("FBC_AI_READING", raising=False)
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done"
    assert f"outputs/job-ai/{READINGS}" not in files.blobs
    assert record["summary"].get("ai_reading") is None


def test_readings_are_stored_beside_the_findings(ai_on, monkeypatch):
    calls = []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _fake_reading(calls))
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done", record.get("error")
    assert len(calls) == 1
    stored = json.loads(files.blobs[f"outputs/job-ai/{READINGS}"])
    assert stored["sheets"]["0"]["fields"][0]["value"] == "999"
    # The planted value is not on the sheet: it is read, stored, and rejected.
    ai = record["summary"]["ai_reading"]
    assert ai["proposals"] == 1 and ai["accepted"] == 0 and ai["rejected"] == 1


def test_a_rerun_replays_the_first_runs_readings(ai_on, monkeypatch):
    calls = []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _fake_reading(calls))
    store, files = FakeJobStore(), FakeStorage()
    _first, blob = _job(store, files, job_id="job-1")
    second, _ = _job(store, files, job_id="job-2", rerun_of="job-1", blob=blob)
    assert second["state"] == "done"
    assert len(calls) == 1, "the re-run paid for a second reading of the same file"
    assert files.blobs[f"outputs/job-2/{READINGS}"] == files.blobs[f"outputs/job-1/{READINGS}"]


def test_a_failing_reader_leaves_a_deterministic_review(ai_on, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("the API is down")
    monkeypatch.setattr("fbcreview.ai.reader.read_document", broken)
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done", record.get("error")
    assert record["summary"].get("ai_reading") is None


def test_a_rebuilt_set_is_replayed_too_though_its_bytes_never_repeat(ai_on, monkeypatch):
    """PyMuPDF writes a fresh document ID on every save, so a set rebuilt from
    scanned sheets hashes differently on every run. The readings are keyed by
    the upload and the rebuild's parameters instead, which do repeat."""
    import pymupdf
    from webapp import convert

    def fake_convert(src, pages, dest):
        doc = pymupdf.open(src)
        doc[0].insert_text((40, 60), "OCR TEXT", fontsize=8)
        doc.save(dest)
        return None
    monkeypatch.setattr(convert, "convert", fake_convert)
    calls = []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _fake_reading(calls))

    store, files = FakeJobStore(), FakeStorage()
    blob = storage_mod.upload_path("job-1", "Set.pdf")
    files.blobs[blob] = make_pdf(pages=2)
    for job_id, parent in (("job-1", None), ("job-2", "job-1")):
        store.create(job_id=job_id, uid="u", email="allowed@example.com", filename="Set.pdf",
                     size_bytes=len(files.blobs[blob]), pages=2, options={}, upload_blob=blob,
                     stages=stages_for(True))
        run_review(job_id=job_id, uid="u", email="allowed@example.com", filename="Set.pdf",
                   upload_blob=blob, options=ReviewOptions(), store=store, store_files=files,
                   convert_raster=True, raster_pages=[0], rerun_of=parent)
        assert store.get(job_id)["state"] == "done", store.get(job_id).get("error")
    assert len(calls) == 1, "the re-run of a rebuilt set paid for a second reading"
    first = json.loads(files.blobs[f"outputs/job-1/{READINGS}"])
    assert first["file_sha256"] != file_sha256_of(files.blobs[blob])


def file_sha256_of(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_config_says_whether_this_deployment_reads_with_ai(client, monkeypatch):
    """The client says "zero model calls" only where that is true."""
    monkeypatch.delenv("FBC_AI_READING", raising=False)
    off = client.get("/api/config").json()
    assert off["ai_reading"] is False and AI_STAGE not in off["stages"]
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    on = client.get("/api/config").json()
    assert on["ai_reading"] is True and AI_STAGE in on["stages"]


def test_findings_json_carries_keys_places_and_evidence(ai_on, monkeypatch):
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _fake_reading([]))
    store, files = FakeJobStore(), FakeStorage()
    record, _ = _job(store, files)
    assert record["state"] == "done", record.get("error")
    body = json.loads(files.blobs["outputs/job-ai/findings.json"])
    keys = [f["key"] for f in body["findings"]]
    assert len(keys) == len(set(keys))
    assert all("rect" in f and "evidence" in f for f in body["findings"])
    assert body["summary"]["ai_reading"]["rejected"] == 1
