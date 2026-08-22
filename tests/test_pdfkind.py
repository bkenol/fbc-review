"""Source classification: is this set actually readable?

The failure this prevents is the quiet one. A scanned permit set runs through
the engine without error and yields no findings, and no findings looks exactly
like a clean set. These tests pin the distinction.
"""
from __future__ import annotations

import io
import json

import pymupdf
import pytest

from conftest import make_blank_pdf, make_pdf, make_raster_pdf
from webapp import pdfkind


def profile_of(data: bytes) -> pdfkind.DocumentProfile:
    return pdfkind.profile("", pymupdf.open("pdf", data))


def post_review(client, pdf: bytes, options: str = "{}", name: str = "set.pdf"):
    return client.post(
        "/api/review",
        files={"file": (name, io.BytesIO(pdf), "application/pdf")},
        data={"review_options": options},
    )


# ── classification ────────────────────────────────────────────────────────
def test_plotted_vector_sheet_is_recognised():
    prof = profile_of(make_pdf(pages=2))
    assert prof.kind == "vector"
    assert prof.reviewable_pages == 2
    assert prof.raster_pages == []
    assert all(s.kind == "vector" for s in prof.sheets)
    assert prof.sheets[0].vector_items >= 120
    assert prof.sheets[0].live_chars >= 120


def test_scanned_sheet_is_recognised_as_raster():
    prof = profile_of(make_raster_pdf(pages=2))
    assert prof.kind == "raster"
    assert prof.reviewable_pages == 0
    assert prof.raster_pages == [0, 1]
    assert prof.sheets[0].image_coverage > 0.9
    assert prof.sheets[0].live_chars == 0
    assert "nothing here for it to read" in prof.summary


def test_blank_pdf_is_recognised():
    prof = profile_of(make_blank_pdf(pages=1))
    assert prof.kind == "blank"
    assert prof.sheets[0].kind == "blank"


def test_mixed_set_names_the_unreadable_sheets():
    """The real case from ARCHITECTURE.md section 6: one rasterised sheet in an
    otherwise vector set."""
    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open("pdf", make_pdf(pages=3)))
    doc.insert_pdf(pymupdf.open("pdf", make_raster_pdf(pages=1)))
    merged = doc.tobytes()
    doc.close()

    prof = profile_of(merged)
    assert prof.kind == "mixed"
    assert prof.reviewable_pages == 3
    assert prof.raster_pages == [3]
    assert prof.needs_conversion is True
    assert "not checked rather than as passing" in prof.summary


def test_profile_is_serialisable_for_firestore():
    prof = profile_of(make_pdf(pages=2))
    encoded = json.dumps(prof.to_dict())
    assert json.loads(encoded)["kind"] == "vector"


# ── admission ─────────────────────────────────────────────────────────────
def test_a_scanned_set_is_refused_rather_than_reviewed_to_nothing(client):
    r = post_review(client, make_raster_pdf(pages=2), name="scanned.pdf")
    assert r.status_code == 422

    err = r.json()["error"]
    assert err["code"] == "raster_pdf"
    assert "no live text" in err["message"]
    assert "Re-plot the set" in err["message"]

    # Refused at the door: no job, no upload.
    assert client.fake_store.docs == {}
    assert client.fake_files.blobs == {}


def test_a_blank_pdf_is_refused(client):
    r = post_review(client, make_blank_pdf(), name="empty-pages.pdf")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "corrupt_pdf"


def test_a_mixed_set_is_accepted_and_the_job_records_which_sheets_are_unreadable(client):
    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open("pdf", make_pdf(pages=2)))
    doc.insert_pdf(pymupdf.open("pdf", make_raster_pdf(pages=1)))
    merged = doc.tobytes()
    doc.close()

    r = post_review(client, merged, name="mixed.pdf")
    assert r.status_code == 202

    body = client.get(f"/api/jobs/{r.json()['id']}").json()
    source = body["source"]
    assert source["kind"] == "mixed"
    assert source["raster_pages"] == [2]
    assert source["reviewable_pages"] == 2
    assert len(source["sheets"]) == 3
    assert source["sheets"][2]["kind"] == "raster"


def test_source_profile_is_published_in_the_schema(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "SourceProfile" in schemas
    assert "SheetProfile" in schemas
    assert "source" in schemas["Job"]["properties"]


# ── pasted tables inside otherwise-readable sheets ────────────────────────
def test_a_pasted_table_is_found_on_an_otherwise_vector_sheet():
    """The ITEC case. The sheet is genuinely vector and classifies as such, so
    a whole-sheet raster check never fires — while the code-analysis rows the
    rules need sit inside a pasted image, unreadable."""
    from conftest import make_pdf_with_pasted_table

    prof = profile_of(make_pdf_with_pasted_table(pages=1))

    assert prof.kind == "vector"
    assert prof.raster_pages == []          # nothing is a *scan*
    assert prof.region_pages == [0]         # but content is pasted in
    assert prof.needs_conversion is True

    sheet = prof.sheets[0]
    assert sheet.kind == "vector"
    assert sheet.has_readable_regions is True
    assert len(sheet.raster_regions) == 1
    assert sheet.raster_regions[0].megapixels > 0.4


def test_a_clean_vector_sheet_reports_no_regions():
    prof = profile_of(make_pdf(pages=2))
    assert prof.region_pages == []
    assert all(not s.has_readable_regions for s in prof.sheets)


def test_the_summary_says_a_pasted_table_needs_ocr():
    from conftest import make_pdf_with_pasted_table

    prof = profile_of(make_pdf_with_pasted_table(pages=1))
    assert "pixels" in prof.summary
    assert "OCR" in prof.summary


def test_regions_reach_the_job_and_the_schema(client):
    from conftest import make_pdf_with_pasted_table

    r = post_review(client, make_pdf_with_pasted_table(pages=1), name="pasted.pdf")
    assert r.status_code == 202

    source = client.get(f"/api/jobs/{r.json()['id']}").json()["source"]
    assert source["region_pages"] == [0]
    assert source["sheets"][0]["raster_regions"]

    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "RasterRegion" in schemas
    assert "region_pages" in schemas["SourceProfile"]["properties"]
