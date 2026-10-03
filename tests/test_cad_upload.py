"""Admitting a drawing: a DWG, a DXF, or a zip of them, at `POST /api/review`.

What is checked here is the door, not the conversion. Admission decides what an
upload is from its bytes, names it after that, and refuses everything it can
refuse without reading the drawing — an old DWG, a hostile zip, a DWG on a
deployment with no converter — before a job record or a blob exists. The
conversion itself runs in the worker (`tests/test_worker_cad.py`).

No client drawing is used. Every file is built here: a DWG is its six-byte
version code and padding, which is all admission reads of one.
"""
from __future__ import annotations

import io
import json
import stat
import zipfile

import pytest

from conftest import make_pdf
from webapp import storage as storage_mod
from webapp.config import settings
from webapp.worker import CAD_STAGE, STAGES

DXF_TEXT = (
    "999\nmade by a test\n0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nENDSEC\n"
    "0\nEOF\n"
)


def dwg(version: bytes = b"AC1032") -> bytes:
    return version + b"\x00" * 6000


def dxf() -> bytes:
    return DXF_TEXT.encode("ascii")


def binary_dxf() -> bytes:
    return b"AutoCAD Binary DXF\r\n\x1a\x00" + b"\x00" * 200


def make_zip(members, *, compress=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compress) as zf:
        for item in members:
            if isinstance(item, zipfile.ZipInfo):
                zf.writestr(item, b"x")
            else:
                name, data = item
                zf.writestr(name, data)
    return buf.getvalue()


def encrypted_flag(data: bytes, member: bytes) -> bytes:
    """Set the 'encrypted' bit on one member's central-directory entry.

    `zipfile` cannot write an encrypted member, and admission reads only the
    directory, so the flag is all a refusal can be tested on.
    """
    buf = bytearray(data)
    at = buf.find(b"PK\x01\x02")
    while at != -1:
        name_len = int.from_bytes(buf[at + 28:at + 30], "little")
        if bytes(buf[at + 46:at + 46 + name_len]) == member:
            flags = int.from_bytes(buf[at + 8:at + 10], "little") | 0x1
            buf[at + 8:at + 10] = flags.to_bytes(2, "little")
        at = buf.find(b"PK\x01\x02", at + 4)
    return bytes(buf)


def post_review(client, body: bytes, name: str, options: str = "{}"):
    return client.post(
        "/api/review",
        files={"file": (name, io.BytesIO(body), "application/octet-stream")},
        data={"review_options": options},
    )


def envelope(response):
    body = response.json()
    assert set(body) == {"error"}, body
    assert body["error"]["code"] and body["error"]["message"]
    assert "Traceback" not in json.dumps(body)
    return body["error"]


@pytest.fixture
def submitted(monkeypatch):
    """Record what would have been handed to the worker, rather than run it.

    Admission is under test here; a real run would start a CAD subprocess in
    the background of every test.
    """
    from webapp import server

    calls = []
    monkeypatch.setattr(server, "run_review", lambda **kw: calls.append(kw))
    return calls


@pytest.fixture
def converter(monkeypatch, tmp_path):
    """A DWG converter is "installed": an executable at FBC_DWG2DXF. Admission
    checks only that it is there; it never runs it."""
    exe = tmp_path / "dwg2dxf"
    exe.write_text("#!/bin/sh\necho 'dwg2dxf 0.0-test'\n")
    exe.chmod(0o755)
    monkeypatch.setenv("FBC_DWG2DXF", str(exe))
    return exe


@pytest.fixture
def no_converter(monkeypatch, tmp_path):
    monkeypatch.setenv("FBC_DWG2DXF", str(tmp_path / "not-installed"))


def assert_nothing_admitted(client):
    assert client.fake_store.docs == {}
    assert client.fake_files.blobs == {}


# ── what the bytes are ────────────────────────────────────────────────────
def test_a_dwg_is_admitted_as_a_drawing_job(client, submitted, converter):
    r = post_review(client, dwg(), "EVERGREEN_BLDG_1.dwg")
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    record = client.fake_store.get(job_id)
    assert record["filename"] == "EVERGREEN_BLDG_1.dwg"
    assert record["upload_blob"] == storage_mod.upload_path(job_id, "EVERGREEN_BLDG_1.dwg")
    assert record["source_format"] == "dwg"
    # Unknown until the worker has plotted it, and written then.
    assert record["pages"] == 0 and record["source"] is None
    assert record["stages"][0] == CAD_STAGE
    assert record["stages"][1:] == STAGES
    assert client.fake_files.blobs[record["upload_blob"]].startswith(b"AC1032")

    (call,) = submitted
    assert call["source_format"] == "dwg"
    assert call["convert_raster"] is False
    assert call["raster_pages"] == [] and call["raster_regions"] == {}


@pytest.mark.parametrize("body,label", [(dxf(), "ascii"), (binary_dxf(), "binary")])
def test_a_dxf_is_recognised_from_its_bytes(client, submitted, no_converter, body, label):
    # No DWG converter is needed for a DXF: ezdxf reads it directly.
    r = post_review(client, body, f"plan-{label}.dxf")
    assert r.status_code == 202, r.text
    record = client.fake_store.get(r.json()["id"])
    assert record["source_format"] == "dxf"
    assert record["filename"] == f"plan-{label}.dxf"


def test_a_short_dxf_under_the_sniff_window_is_still_read(client, submitted):
    body = b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    assert len(body) < 4096
    r = post_review(client, body, "tiny.dxf")
    assert r.status_code == 202, r.text


def test_text_named_like_a_drawing_is_refused(client, submitted):
    r = post_review(client, b"this is a list of drawings, not a drawing", "plan.dwg")
    assert r.status_code == 415
    assert envelope(r)["code"] == "unsupported_media_type"
    assert_nothing_admitted(client)


def test_a_drawing_sent_under_a_pdf_name_is_named_for_what_it_is(client, submitted, converter):
    r = post_review(client, dwg(), "set.pdf")
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    record = client.fake_store.get(job_id)
    assert record["filename"] == "set.dwg"
    assert f"uploads/{job_id}/set.dwg" in client.fake_files.blobs


def test_an_old_dwg_is_refused_with_the_release_it_needs(client, submitted, converter):
    r = post_review(client, dwg(b"AC1009"), "old.dwg")
    assert r.status_code == 400
    err = envelope(r)
    assert err["code"] == "unsupported_cad_version"
    assert "AutoCAD 2000" in err["message"]
    assert_nothing_admitted(client)


def test_a_dwg_without_a_converter_is_refused_at_the_door(client, submitted, no_converter):
    r = post_review(client, dwg(), "plan.dwg")
    assert r.status_code == 422
    err = envelope(r)
    assert err["code"] == "cad_unavailable"
    assert "DXF" in err["message"]
    assert_nothing_admitted(client)


# ── zips ──────────────────────────────────────────────────────────────────
def test_a_zip_of_drawings_is_admitted(client, submitted, converter):
    body = make_zip([("SET/A-101.dwg", dwg()), ("SET/X-BASE.dxf", dxf()),
                     ("SET/notes.txt", b"transmittal")])
    r = post_review(client, body, "Permit Set.zip")
    assert r.status_code == 202, r.text
    record = client.fake_store.get(r.json()["id"])
    assert record["source_format"] == "zip"
    assert record["filename"] == "Permit Set.zip"
    assert submitted[0]["source_format"] == "zip"


def test_a_zip_holding_a_dwg_needs_the_converter(client, submitted, no_converter):
    r = post_review(client, make_zip([("A-101.dwg", dwg())]), "set.zip")
    assert r.status_code == 422
    assert envelope(r)["code"] == "cad_unavailable"
    assert_nothing_admitted(client)


def test_a_zip_of_dxfs_needs_no_converter(client, submitted, no_converter):
    r = post_review(client, make_zip([("A-101.dxf", dxf())]), "set.zip")
    assert r.status_code == 202, r.text


def _symlink() -> zipfile.ZipInfo:
    info = zipfile.ZipInfo("A-101.dxf")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize("label,members,status,code", [
    ("parent path", [("../../etc/A-101.dxf", dxf())], 400, "unsafe_archive"),
    ("absolute path", [("/etc/A-101.dxf", dxf())], 400, "unsafe_archive"),
    ("drive letter", [("C:/Projects/A-101.dxf", dxf())], 400, "unsafe_archive"),
    ("nested archive", [("A-101.dxf", dxf()), ("more.zip", b"PK\x03\x04")], 400,
     "unsafe_archive"),
    ("symlink", [_symlink()], 400, "unsafe_archive"),
    ("no drawing", [("readme.txt", b"hello")], 415, "unsupported_media_type"),
])
def test_a_hostile_or_empty_zip_is_refused_before_anything_exists(
        client, submitted, converter, label, members, status, code):
    r = post_review(client, make_zip(members), f"{label}.zip")
    assert r.status_code == status, (label, r.text)
    assert envelope(r)["code"] == code
    assert_nothing_admitted(client)
    assert submitted == []


def test_a_zip_bomb_is_refused_from_its_directory(client, submitted, converter):
    # 16 MB of zeros deflates to about 16 KB: a ratio no drawing reaches.
    body = make_zip([("A-101.dxf", b"0" * (16 * 1024 * 1024))])
    assert len(body) < 200_000
    r = post_review(client, body, "bomb.zip")
    assert r.status_code == 413
    assert envelope(r)["code"] == "unsafe_archive"
    assert_nothing_admitted(client)


def test_a_zip_with_too_many_members_is_refused(client, submitted, converter):
    from fbcreview.cad.source import MAX_MEMBERS

    members = [(f"sheet-{i:04d}.dxf", dxf()) for i in range(MAX_MEMBERS + 1)]
    r = post_review(client, make_zip(members, compress=zipfile.ZIP_STORED), "many.zip")
    assert r.status_code == 413
    err = envelope(r)
    assert err["code"] == "unsafe_archive"
    assert str(MAX_MEMBERS) in err["message"]
    assert_nothing_admitted(client)


def test_a_password_protected_drawing_is_refused(client, submitted, converter):
    body = encrypted_flag(make_zip([("A-101.dxf", dxf())]), b"A-101.dxf")
    r = post_review(client, body, "locked.zip")
    assert r.status_code == 400
    err = envelope(r)
    assert err["code"] == "unsafe_archive"
    assert "password" in err["message"]
    assert_nothing_admitted(client)


def test_a_damaged_zip_is_refused_as_unreadable(client, submitted, converter):
    r = post_review(client, b"PK\x03\x04" + b"\x00" * 5000, "broken.zip")
    assert r.status_code == 400
    assert envelope(r)["code"] == "corrupt_cad"
    assert_nothing_admitted(client)


def test_an_oversized_drawing_is_refused_while_streaming(client, submitted, monkeypatch):
    settings.cache_clear()
    monkeypatch.setenv("FBC_MAX_UPLOAD_MB", "1")
    settings.cache_clear()
    r = post_review(client, dxf() + b"999\n" + b"x" * (2 * 1024 * 1024), "big.dxf")
    assert r.status_code == 413
    assert envelope(r)["code"] == "payload_too_large"
    assert_nothing_admitted(client)
    settings.cache_clear()


# ── prefill ───────────────────────────────────────────────────────────────
def test_prefill_on_a_drawing_says_suggestions_come_from_a_pdf(client, submitted):
    r = client.post("/api/prefill",
                    files={"file": ("plan.dxf", io.BytesIO(dxf()), "application/dxf")})
    assert r.status_code == 422
    err = envelope(r)
    assert err["code"] == "prefill_not_available"
    assert "PDF" in err["message"] and "review runs" in err["message"]
    assert_nothing_admitted(client)


def test_prefill_refuses_a_hostile_zip_the_way_a_review_does(client, submitted, converter):
    body = make_zip([("../A-101.dxf", dxf())])
    r = client.post("/api/prefill", files={"file": ("set.zip", io.BytesIO(body), "x")})
    assert r.status_code == 400
    assert envelope(r)["code"] == "unsafe_archive"


def test_prefill_still_reads_a_pdf(client):
    r = client.post("/api/prefill",
                    files={"file": ("set.pdf", io.BytesIO(make_pdf()), "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "set.pdf"


# ── the finished job ──────────────────────────────────────────────────────
SUMMARY = {
    "sheets": 1, "pages": 1, "cad_layers": 4, "annotations": 0, "marked": 0,
    "counts": {}, "open": 0, "verified": 0, "abstentions": [], "rules_run": 0,
    "scale_pages": 1, "pdf_bytes": 9, "pdf_name": "Tower — CODE REVIEW.pdf",
    "findings_count": 0, "source_format": "dwg",
}

CAD = {
    "format": "dwg", "drawings": 1, "sheets": 1, "sheets_identified": 1, "layers": 4,
    "viewports": 1, "attributes": 1, "dimensions": 0, "claims": 1,
    "converter": "dwg2dxf 0.14", "render_version": "cad-render-1", "seconds": 4.2,
    "warnings": ["Title Block.dwg was not supplied, so the xref is not drawn."],
}


def _done_cad_job(client, job_id="job-cad", markup_dxf=True):
    store = client.fake_store
    store.create(job_id=job_id, uid="uid-alice", email="allowed@example.com",
                 filename="Tower.dwg", size_bytes=10, pages=0, options={},
                 upload_blob=storage_mod.upload_path(job_id, "Tower.dwg"),
                 stages=[CAD_STAGE, *STAGES])
    fields = {"source_format": "dwg", "pages": 1, "cad": CAD,
              "viewer_blob": storage_mod.output_path(job_id, storage_mod.SOURCE_PDF)}
    if markup_dxf:
        fields["markup_dxf"] = True
    store.update(job_id, **fields)
    store.mark_done(job_id, dict(SUMMARY))
    return job_id


def test_a_drawing_jobs_viewer_reads_the_plotted_pdf_not_the_dwg(client):
    job_id = _done_cad_job(client)
    body = client.get(f"/api/jobs/{job_id}").json()

    dl = body["downloads"]
    assert f"outputs/{job_id}/source.pdf" in dl["source_pdf"]
    assert "Tower.dwg" not in dl["source_pdf"]
    assert f"outputs/{job_id}/{storage_mod.MARKUP_DXF}" in dl["markup_dxf"]
    assert "Tower — CODE REVIEW (DXF).zip" in dl["markup_dxf"]

    assert body["source_format"] == "dwg"
    assert body["cad"]["sheets"] == 1 and body["cad"]["converter"] == "dwg2dxf 0.14"
    assert body["summary"]["source_format"] == "dwg"
    assert body["stage_label"] == STAGES[-1]


def test_no_dxf_link_when_none_was_written(client):
    job_id = _done_cad_job(client, markup_dxf=False)
    dl = client.get(f"/api/jobs/{job_id}").json()["downloads"]
    assert dl["markup_dxf"] == ""
    assert f"outputs/{job_id}/source.pdf" in dl["source_pdf"]


def test_a_pdf_job_is_unchanged(client):
    r = client.post("/api/review",
                    files={"file": ("set.pdf", io.BytesIO(make_pdf()), "application/pdf")},
                    data={"review_options": "{}"})
    job_id = r.json()["id"]
    client.fake_store.done.wait(timeout=60)
    client.fake_store.mark_done(job_id, {k: v for k, v in SUMMARY.items()
                                         if k != "source_format"})
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["source_format"] == "pdf"
    assert body["cad"] is None
    assert f"uploads/{job_id}/set.pdf" in body["downloads"]["source_pdf"]
    assert body["downloads"]["markup_dxf"] == ""
    assert "source_format" not in client.fake_store.get(job_id)


def test_a_rerun_of_a_drawing_replots_from_the_first_upload(client, submitted):
    job_id = _done_cad_job(client)
    uploads_before = {b for b in client.fake_files.blobs if b.startswith("uploads/")}

    r = client.post(f"/api/jobs/{job_id}/rerun",
                    json={"declaration": {"stories": 2}, "convert_raster": True})
    assert r.status_code == 202, r.text
    new_id = r.json()["id"]

    record = client.fake_store.get(new_id)
    assert record["stages"][0] == CAD_STAGE
    assert record["source_format"] == "dwg"
    assert record["upload_blob"] == client.fake_store.get(job_id)["upload_blob"]
    (call,) = submitted
    assert call["source_format"] == "dwg" and call["rerun_of"] == job_id
    # A plot made here has no scanned sheets to rebuild, whatever was asked.
    assert call["convert_raster"] is False
    assert {b for b in client.fake_files.blobs if b.startswith("uploads/")} == uploads_before


def test_a_record_from_before_the_field_existed_reads_its_format_from_its_name(client):
    from webapp import server

    assert server._source_format({"filename": "Tower.dxf"}) == "dxf"
    assert server._source_format({"filename": "Tower.pdf"}) == "pdf"
    assert server._source_format({"filename": "x", "source_format": "zip"}) == "zip"
    assert server._source_format({"filename": "x.dwg", "source_format": "nonsense"}) == "dwg"


# ── what the client is told ───────────────────────────────────────────────
def test_config_lists_what_admission_takes(client, converter):
    from fbcreview.cad import convert

    convert._version_of.cache_clear()
    body = client.get("/api/config").json()
    assert body["cad_available"] is True
    assert body["accepted_formats"] == ["pdf", "dwg", "dxf", "zip"]
    # The default list is untouched: a PDF job's stages are what they were.
    assert len(body["stages"]) == 5 and CAD_STAGE not in body["stages"]


def test_config_without_a_converter_still_takes_dxf(client, no_converter):
    body = client.get("/api/config").json()
    assert body["cad_available"] is False
    assert body["accepted_formats"] == ["pdf", "dxf", "zip"]


def test_the_schema_publishes_the_drawing_fields(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "markup_dxf" in schemas["Downloads"]["properties"]
    assert "source_format" in schemas["Job"]["properties"]
    assert "cad" in schemas["Job"]["properties"]
    assert {"sheets", "layers", "viewports", "warnings"} <= set(
        schemas["CadReport"]["properties"])
    assert {"accepted_formats", "cad_available"} <= set(
        schemas["ConfigResponse"]["properties"])


# ── the filesystem backend serves the zipped DXF as a zip ─────────────────
@pytest.fixture
def local_client(monkeypatch, tmp_path, user):
    """The filesystem backend, which serves artefacts itself (share.sh, the tunnel)."""
    from fastapi.testclient import TestClient

    from webapp import server

    settings.cache_clear()
    monkeypatch.setenv("FBC_BACKEND", "local")
    monkeypatch.setenv("FBC_BUCKET", str(tmp_path))
    monkeypatch.setenv("FBC_ARTEFACT_SECRET", "a-test-signing-key")
    monkeypatch.delenv("FBC_DEV_UNSAFE_AUTH", raising=False)
    settings.cache_clear()
    for cached in (server._dev_jobs, server._dev_files, server._dev_feedback):
        cached.cache_clear()
    server.app.dependency_overrides[server.current_user] = lambda: user
    with TestClient(server.app) as c:
        yield c
    server.app.dependency_overrides.clear()
    for cached in (server._dev_jobs, server._dev_files, server._dev_feedback):
        cached.cache_clear()
    settings.cache_clear()


def test_the_local_backend_serves_the_marked_up_drawing_as_a_zip(local_client):
    from webapp import server

    job_id = "job-local-cad"
    files, jobs = server._dev_files(), server._dev_jobs()
    blobs = {storage_mod.MARKUP: b"%PDF-1.7\n", storage_mod.FINDINGS: b"{}",
             storage_mod.SOURCE_PDF: b"%PDF-1.7\nplotted\n", storage_mod.MARKUP_DXF: b"PK\x05\x06"}
    for name, body in blobs.items():
        path = files.dir / storage_mod.output_path(job_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    jobs.create(job_id=job_id, uid="uid-alice", email="allowed@example.com",
                filename="Tower.dxf", size_bytes=9, pages=1, options={},
                upload_blob=storage_mod.upload_path(job_id, "Tower.dxf"),
                stages=[CAD_STAGE, *STAGES])
    jobs.update(job_id, source_format="dxf", markup_dxf=True,
                viewer_blob=storage_mod.output_path(job_id, storage_mod.SOURCE_PDF))
    jobs.mark_done(job_id, {k: v for k, v in SUMMARY.items() if k != "source_format"})

    dl = local_client.get(f"/api/jobs/{job_id}").json()["downloads"]
    fetched = local_client.get(dl["markup_dxf"])
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "application/zip"
    assert fetched.content == b"PK\x05\x06"
    viewer = local_client.get(dl["source_pdf"])
    assert viewer.status_code == 200 and viewer.content.endswith(b"plotted\n")
