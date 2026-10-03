"""Service defects the post-merge review of drawing uploads found, each with the
input that showed it.

* Drawing jobs waited for the one CAD slot inside the review pool's threads, so
  two drawings queued behind a third held both of the pool's workers and every
  PDF review waited behind them.
* `FBC_STALE_RUNNING_MINUTES` went from 15 to 45 for drawings, and so for every
  PDF job too: an orphaned PDF review read "running" for three times as long.
"""
from __future__ import annotations

import datetime as dt
import io
import threading
import time

import pytest

from conftest import make_pdf, upload_form


def _wait_for(calls, n, timeout=10.0):
    end = time.monotonic() + timeout
    while len(calls) < n and time.monotonic() < end:
        time.sleep(0.01)
    return calls


def test_a_drawing_job_never_runs_on_a_pdf_review_thread(client, monkeypatch, tmp_path):
    from webapp import server

    calls = []
    monkeypatch.setattr(server, "run_review", lambda **kw: calls.append(
        (kw.get("source_format", "pdf"), threading.current_thread().name)))
    dxf = b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    r = client.post("/api/review", data={"review_options": "{}"},
                    files={"file": ("plan.dxf", io.BytesIO(dxf), "application/dxf")})
    assert r.status_code == 202, r.text
    r = client.post("/api/review", **upload_form(make_pdf()))
    assert r.status_code == 202, r.text
    ran = dict(_wait_for(calls, 2))
    assert ran["dxf"].startswith("drawing"), ran
    assert ran["pdf"].startswith("review"), ran


def _running(store, job_id, minutes_ago, source_format):
    store.create(job_id=job_id, uid="uid-alice", email="allowed@example.com",
                 filename="x", size_bytes=1, pages=1, options={}, upload_blob="u",
                 stages=[])
    store.update(job_id, state="running", source_format=source_format,
                 started_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago))


def test_an_orphaned_pdf_review_is_called_interrupted_after_fifteen_minutes(monkeypatch,
                                                                          tmp_path):
    """The sweep the local backend runs at startup, against its own records."""
    monkeypatch.setenv("FBC_BACKEND", "local")
    monkeypatch.setenv("FBC_BUCKET", str(tmp_path / "bucket"))     # dev_root: never the repo
    monkeypatch.delenv("FBC_STALE_RUNNING_MINUTES", raising=False)
    monkeypatch.delenv("FBC_STALE_DRAWING_MINUTES", raising=False)
    from webapp.config import settings
    settings.cache_clear()
    try:
        from webapp import devbackend
        store = devbackend.LocalJobStore()
        _running(store, "pdf-20", 20, "pdf")
        _running(store, "dwg-20", 20, "dwg")
        _running(store, "dwg-70", 70, "dwg")
        assert store.fail_stale_running() == 2
        assert store.get("pdf-20")["state"] == "error"
        assert store.get("dwg-20")["state"] == "running"     # a drawing may still be at work
        assert store.get("dwg-70")["state"] == "error"
    finally:
        settings.cache_clear()


def test_every_setting_the_adapter_reads_reaches_it():
    """The adapter subprocess gets a named list of variables, never the
    service's environment. A setting the adapter reads that is not on the list
    is silently its default in production — FBC_CAD_MAX_ENTITIES was, for one
    merge — so the list is checked against what the code reads."""
    import re
    from pathlib import Path

    from webapp.cadjob import _ADAPTER_ENV
    root = Path(__file__).resolve().parent.parent / "fbcreview"
    read = set()
    for path in [*(root / "cad").glob("*.py"), root / "read" / "cad.py"]:
        read |= set(re.findall(r"""environ(?:\.get)?\(?\[?["'](FBC_[A-Z0-9_]+)["']""",
                               path.read_text(encoding="utf-8")))
    assert read, "found no settings at all: the pattern is wrong"
    assert read <= set(_ADAPTER_ENV), read - set(_ADAPTER_ENV)
