"""The worker on a drawing upload: plotted in a subprocess, reviewed as the plot.

What is checked is the shell around `fbcreview/cad`: the drawing is plotted by
`python -m fbcreview.cad` in its own process, the plot becomes the file every
later stage reads, four artefacts are published, every failure is typed, and
nothing — not the scratch directory, not the file name — outlives the run.

No client drawing is used and no network is touched. The DXF is built here with
ezdxf; a DWG is its version code and padding, "converted" by a stand-in
`dwg2dxf` that copies a prepared DXF to where it was asked to write, so CI needs
no LibreDWG.
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import logging
import sys
import time
import zipfile
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from conftest import FakeJobStore, FakeStorage  # noqa: E402
from fbcreview.options import ReviewOptions  # noqa: E402
from webapp import storage as storage_mod  # noqa: E402
from webapp.config import settings  # noqa: E402
from webapp.models import CadReport, FindingsDocument  # noqa: E402
from webapp.worker import CAD_STAGE, run_review, stages_for  # noqa: E402


def make_dxf(path: Path) -> Path:
    """A one-sheet permit drawing: a paper-space layout with a title block whose
    attribute names the sheet, a viewport onto model space at 1/4" = 1'-0", and
    the risk category printed as two TEXT entities the way a code block is."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1                       # inches
    msp = doc.modelspace()
    doc.layers.add("A-WALL")
    doc.layers.add("A-ANNO-TEXT")
    for i in range(40):
        msp.add_line((i * 24, 0), (i * 24, 480), dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([(0, 0), (960, 0), (960, 480), (0, 480)], close=True,
                       dxfattribs={"layer": "A-WALL"})
    msp.add_text("OFFICE 101", height=12,
                 dxfattribs={"layer": "A-ANNO-TEXT"}).set_placement((100, 200))

    block = doc.blocks.new("TITLE")
    block.add_attdef("SHEET_NO", (30, 1), dxfattribs={"height": 0.25, "prompt": "SHEET No."})

    layout = doc.layouts.new("A101 PLAN")
    layout.page_setup(size=(36, 24), margins=(0.5, 0.5, 0.5, 0.5), units="inch")
    layout.add_viewport(center=(14, 12), size=(24, 16), view_center_point=(480, 240),
                        view_height=16 * 48)
    layout.add_blockref("TITLE", (0, 0)).add_auto_attribs({"SHEET_NO": "A-101"})
    layout.add_text("RISK CATEGORY:", height=0.2).set_placement((28, 20))
    layout.add_text("III", height=0.2).set_placement((31, 20))
    layout.add_text("OCCUPANCY: BUSINESS", height=0.2).set_placement((28, 19))
    doc.saveas(str(path))
    return path


def dwg_bytes() -> bytes:
    return b"AC1032" + b"\x00" * 6000


def fake_dwg2dxf(tmp_path: Path, prepared: Path, *, sleep: float = 0.0) -> Path:
    """A stand-in for LibreDWG's `dwg2dxf`: answers `--version`, and for
    `-y -o OUT IN` copies a prepared DXF to OUT."""
    exe = tmp_path / "dwg2dxf"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import shutil, sys, time\n"
        "if '--version' in sys.argv:\n"
        "    print('dwg2dxf 0.0-test'); sys.exit(0)\n"
        f"time.sleep({sleep!r})\n"
        f"shutil.copyfile({str(prepared)!r}, sys.argv[sys.argv.index('-o') + 1])\n"
    )
    exe.chmod(0o755)
    return exe


def run_cad(store, files, data: bytes, *, job_id="job-cad", filename="Tower.dxf",
            fmt="dxf", rerun_of=None, blob=None):
    blob = blob or storage_mod.upload_path(job_id, filename)
    files.blobs.setdefault(blob, data)
    store.create(job_id=job_id, uid="uid-alice", email="allowed@example.com",
                 filename=filename, size_bytes=len(data), pages=0, options={},
                 upload_blob=blob, stages=stages_for(False, cad=True))
    store.update(job_id, source_format=fmt)
    run_review(job_id=job_id, uid="uid-alice", email="allowed@example.com",
               filename=filename, upload_blob=blob, options=ReviewOptions(),
               store=store, store_files=files, rerun_of=rerun_of, source_format=fmt)
    return store.get(job_id)


@pytest.fixture(scope="module")
def drawing(tmp_path_factory) -> bytes:
    return make_dxf(tmp_path_factory.mktemp("dxf") / "tower.dxf").read_bytes()


@pytest.fixture(autouse=True)
def _fresh_settings():
    settings.cache_clear()
    yield
    settings.cache_clear()


# ── the round trip ────────────────────────────────────────────────────────
def test_a_dxf_is_plotted_reviewed_and_published(drawing):
    store, files = FakeJobStore(), FakeStorage()
    record = run_cad(store, files, drawing)

    assert record["state"] == "done", record.get("error")
    for name in (storage_mod.MARKUP, storage_mod.FINDINGS, storage_mod.SOURCE_PDF,
                 storage_mod.MARKUP_DXF):
        assert storage_mod.output_path("job-cad", name) in files.blobs, name
    assert files.blobs["outputs/job-cad/markup.pdf"].startswith(b"%PDF-")
    assert files.blobs["outputs/job-cad/source.pdf"].startswith(b"%PDF-")
    # The upload is left as it was: nothing under uploads/ is written by a review.
    assert [b for b in files.blobs if b.startswith("uploads/")] == ["uploads/job-cad/Tower.dxf"]

    assert record["viewer_blob"] == "outputs/job-cad/source.pdf"
    assert record["markup_dxf"] is True
    assert record["pages"] == 1
    assert record["source"]["reviewable_pages"] == 1
    report = CadReport.model_validate(record["cad"])
    assert (report.format, report.sheets, report.sheets_identified) == ("dxf", 1, 1)
    assert report.viewports == 1 and report.attributes == 1 and report.converter == ""


def test_the_findings_document_still_validates_and_says_what_was_uploaded(drawing):
    store, files = FakeJobStore(), FakeStorage()
    run_cad(store, files, drawing)
    document = FindingsDocument.model_validate_json(files.blobs["outputs/job-cad/findings.json"])
    assert document.summary.source_format == "dxf"
    assert document.summary.findings_count == len(document.findings)
    # The sheet number came from the title block's attribute, not the tab name.
    assert [s.code for s in document.summary.sheet_index] == ["A-101"]
    # The layer count is the drawing's own table, not the plot's.
    assert document.summary.cad_layers >= 3


def test_the_marked_up_drawing_is_a_zipped_dxf_named_for_the_upload(drawing):
    store, files = FakeJobStore(), FakeStorage()
    run_cad(store, files, drawing)
    with zipfile.ZipFile(io.BytesIO(files.blobs["outputs/job-cad/markup-dxf.zip"])) as zf:
        names = zf.namelist()
        dxf_name = next(n for n in names if n.endswith(".dxf"))
        assert dxf_name.startswith("Tower")
        text = zf.read(dxf_name).decode("utf-8", "replace")
    assert "FBC-REVIEW" in text


def test_every_stage_is_walked_and_the_last_is_reached(drawing):
    store, files = FakeJobStore(), FakeStorage()
    seen = []
    real = store.mark_stage
    store.mark_stage = lambda job_id, stage: (seen.append(stage), real(job_id, stage))
    record = run_cad(store, files, drawing)
    stages = stages_for(False, cad=True)
    assert stages[0] == CAD_STAGE
    assert record["stage"] == len(stages) - 1
    assert seen == list(range(1, len(stages)))


def test_scratch_is_removed(drawing, monkeypatch):
    created = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def tracking(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr("webapp.worker.tempfile.mkdtemp", tracking)
    store, files = FakeJobStore(), FakeStorage()
    assert run_cad(store, files, drawing)["state"] == "done"
    assert created
    for path in created:
        assert not path.exists(), f"{path} survived the run"


def test_logs_never_carry_the_file_name_or_the_drawings_text(drawing, caplog):
    store, files = FakeJobStore(), FakeStorage()
    secret = "CONFIDENTIAL CLIENT PROJECT.dxf"
    with caplog.at_level(logging.DEBUG):
        record = run_cad(store, files, drawing, job_id="job-log", filename=secret)
    assert record["state"] == "done", record.get("error")
    blob = "".join(
        f"{r.getMessage()} {json.dumps(getattr(r, '__dict__', {}), default=str)}"
        for r in caplog.records)
    assert "CONFIDENTIAL" not in blob
    for drawn in ("A101 PLAN", "RISK CATEGORY", "A-WALL", "%PDF-"):
        assert drawn not in blob
    assert "job-log" in blob


# ── a DWG, through a stand-in converter ───────────────────────────────────
def test_a_dwg_is_converted_then_reviewed(drawing, tmp_path, monkeypatch):
    prepared = tmp_path / "prepared.dxf"
    prepared.write_bytes(drawing)
    monkeypatch.setenv("FBC_DWG2DXF", str(fake_dwg2dxf(tmp_path, prepared)))
    store, files = FakeJobStore(), FakeStorage()
    record = run_cad(store, files, dwg_bytes(), filename="Tower.dwg", fmt="dwg")
    assert record["state"] == "done", record.get("error")
    assert record["cad"]["format"] == "dwg"
    assert record["cad"]["converter"] == "dwg2dxf 0.0-test"
    assert "outputs/job-cad/markup-dxf.zip" in files.blobs


def test_a_dwg_with_no_converter_fails_typed_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("FBC_DWG2DXF", str(tmp_path / "not-installed"))
    store, files = FakeJobStore(), FakeStorage()
    record = run_cad(store, files, dwg_bytes(), filename="Tower.dwg", fmt="dwg")
    assert record["state"] == "error"
    assert record["error_code"] == "cad_unavailable"
    assert "DXF" in record["error"]
    assert "Traceback" not in record["error"] and "webapp" not in record["error"]
    assert not any(b.startswith("outputs/") for b in files.blobs)


def test_a_converter_past_the_deadline_is_stopped(drawing, tmp_path, monkeypatch):
    from webapp import cadjob

    prepared = tmp_path / "prepared.dxf"
    prepared.write_bytes(drawing)
    monkeypatch.setenv("FBC_DWG2DXF", str(fake_dwg2dxf(tmp_path, prepared, sleep=60)))
    quick = dataclasses.replace(settings(), cad_timeout_seconds=3)
    monkeypatch.setattr(cadjob, "settings", lambda: quick)
    store, files = FakeJobStore(), FakeStorage()
    t0 = time.monotonic()
    record = run_cad(store, files, dwg_bytes(), filename="Tower.dwg", fmt="dwg")
    assert time.monotonic() - t0 < 30, "the deadline did not stop the converter"
    assert record["state"] == "error"
    assert record["error_code"] == "corrupt_cad"
    assert "longer than" in record["error"]


def test_an_unreadable_drawing_says_so_without_blaming_a_scan(tmp_path):
    store, files = FakeJobStore(), FakeStorage()
    broken = b"0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n"          # sniffs as DXF, reads as nothing
    record = run_cad(store, files, broken)
    assert record["state"] == "error"
    assert record["error_code"] == "corrupt_cad"
    assert "scanned" not in record["error"]


def test_a_drawing_over_the_page_cap_is_refused_after_plotting(drawing, monkeypatch):
    monkeypatch.setenv("FBC_MAX_PAGES", "0")
    settings.cache_clear()
    store, files = FakeJobStore(), FakeStorage()
    record = run_cad(store, files, drawing)
    assert record["state"] == "error"
    assert record["error_code"] == "too_many_pages"
    assert "outputs/job-cad/source.pdf" not in files.blobs


def test_a_failing_dxf_markup_still_finishes_the_review(drawing, monkeypatch):
    from webapp import cadjob

    def broken(*_a, **_k):
        raise RuntimeError("markup exploded")

    monkeypatch.setattr(cadjob, "markup", broken)
    store, files = FakeJobStore(), FakeStorage()
    record = run_cad(store, files, drawing)
    assert record["state"] == "done", record.get("error")
    assert "outputs/job-cad/markup.pdf" in files.blobs
    assert "outputs/job-cad/markup-dxf.zip" not in files.blobs
    assert not record.get("markup_dxf")


def test_a_markup_subprocess_that_fails_is_a_smaller_delivery(drawing, monkeypatch):
    from webapp import cadjob

    real = cadjob._run

    def failing(args, cwd, timeout):
        if args[0] == "markup":
            args = ["markup", str(cwd), "/nonexistent/findings.json", args[3]]
        return real(args, cwd, timeout)

    monkeypatch.setattr(cadjob, "_run", failing)
    store, files = FakeJobStore(), FakeStorage()
    record = run_cad(store, files, drawing)
    assert record["state"] == "done", record.get("error")
    assert "outputs/job-cad/markup-dxf.zip" not in files.blobs


# ── AI readings of a drawing ──────────────────────────────────────────────
ON = {"FBC_AI_READING": "on", "ANTHROPIC_API_KEY": "sk-ant-test"}


@pytest.fixture
def ai_on(monkeypatch, tmp_path):
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FBC_AI_CACHE_DIR", str(tmp_path / "ai-cache"))


def _reader(calls):
    from fbcreview.ai.prompt import PROMPT_VERSION
    from fbcreview.ai.readings import Readings, file_sha256
    from fbcreview.ai.schema import FieldReading, SheetReading

    def read_document(src, config, cache=None, identity=None, focus=None, **_kw):
        calls.append(identity)
        return Readings(identity or file_sha256(src), config.model, PROMPT_VERSION, sheets={
            0: SheetReading(sheet_number="A-101", fields=[
                FieldReading(field="occupant_load", value="999", quote="OCCUPANT LOAD 999")])})
    return read_document


def test_a_rerun_of_a_drawing_replays_its_readings(drawing, ai_on, monkeypatch, tmp_path):
    """The plotted PDF's bytes differ on every run, so readings are keyed by the
    drawing and what plotted it (`plotted_identity`) — and a re-run pays nothing."""
    from fbcreview.ai.readings import file_sha256, plotted_identity
    from fbcreview.cad import ingest

    calls = []
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _reader(calls))
    store, files = FakeJobStore(), FakeStorage()
    first = run_cad(store, files, drawing, job_id="job-1")
    assert first["state"] == "done", first.get("error")
    assert first["stages"][:3] == [CAD_STAGE, "Reading the PDF", "Reading sheets with AI"]

    second = run_cad(store, files, drawing, job_id="job-2", rerun_of="job-1",
                     blob=first["upload_blob"])
    assert second["state"] == "done", second.get("error")
    assert len(calls) == 1, "the re-run paid for a second reading of the same drawing"

    # A fresh, independent plot of the same drawing gives the same key: it is
    # derived from the drawing and the plot's text layer, never the PDF's bytes.
    upload = tmp_path / "again.dxf"
    upload.write_bytes(drawing)
    again = ingest(str(upload), str(tmp_path / "again"), name="tower.dxf")
    expected = plotted_identity(hashlib.sha256(drawing).hexdigest(), again.data, again.pdf_path)
    assert len(expected) == 64 and all(c in "0123456789abcdef" for c in expected)
    assert expected != hashlib.sha256(drawing).hexdigest()
    assert calls == [expected]
    stored = json.loads(files.blobs["outputs/job-1/readings.json"])
    assert stored["file_sha256"] == expected
    assert files.blobs["outputs/job-2/readings.json"] == files.blobs["outputs/job-1/readings.json"]
    # Not the plotted PDF's own hash, which no later run would ever produce again.
    plotted = tmp_path / "plotted.pdf"
    plotted.write_bytes(files.blobs["outputs/job-1/source.pdf"])
    assert expected != file_sha256(str(plotted))


def test_every_ai_review_pass_keeps_the_drawings_facts(drawing, ai_on, monkeypatch):
    """Each pass rebuilds the facts. A pass rebuilt without the sidecar would
    drop what was read from the drawing and review a smaller set."""
    from fbcreview.ai.schema import RereadRequest, ResultReview
    from webapp import worker

    monkeypatch.delenv("FBC_AI_REVIEW", raising=False)
    monkeypatch.setattr("fbcreview.ai.reader.read_document", _reader([]))
    answers = [ResultReview(meets_request=False, rereads=[
        RereadRequest(page=1, fields=["occupant_load"], hint="code block")]),
        ResultReview(meets_request=True)]
    checks = []

    def check_result(client, config, system, content):
        checks.append(1)
        return answers[min(len(checks), len(answers)) - 1], {"input_tokens": 1,
                                                             "output_tokens": 1}

    monkeypatch.setattr("fbcreview.ai.reviewer.check_result", check_result)
    built = []
    real = worker.build_facts

    def recording(path, readings=None, cad=None):
        built.append(cad is not None)
        return real(path, readings=readings, cad=cad)

    monkeypatch.setattr(worker, "build_facts", recording)
    store, files = FakeJobStore(), FakeStorage()
    record = run_cad(store, files, drawing)
    assert record["state"] == "done", record.get("error")
    assert len(built) >= 2, "the review loop never rebuilt the facts"
    assert all(built), "a pass was built without the drawing's sidecar"
