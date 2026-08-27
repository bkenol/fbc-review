"""Rebuilding scanned sheets.

The load-bearing test in this file is
`test_traced_layer_must_never_impersonate_the_egress_layer`. Everything else is
mechanics; that one is a safety property.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

import pymupdf
import pytest

from conftest import make_pdf, make_raster_pdf
from webapp import convert, pdfkind


def write(data: bytes, name: str) -> str:
    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(data)
    return str(path)


def mixed_set(vector_pages: int = 1, raster_pages: int = 1) -> bytes:
    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open("pdf", make_pdf(pages=vector_pages)))
    doc.insert_pdf(pymupdf.open("pdf", make_raster_pdf(pages=raster_pages)))
    data = doc.tobytes()
    doc.close()
    return data


def post_review(client, pdf: bytes, options: str = "{}", name: str = "set.pdf"):
    return client.post(
        "/api/review",
        files={"file": (name, io.BytesIO(pdf), "application/pdf")},
        data={"review_options": options},
    )


# ── the safety property ───────────────────────────────────────────────────
def test_traced_layer_must_never_impersonate_the_egress_layer():
    """MEASURE.EGRESS_EXTENT selects geometry by matching the CAD layer name
    against "egress path", then reports the longest straight run as a travel
    distance at CRITICAL severity.

    Tracing recovers lines but not what they mean. If the traced layer were
    named so that rule matched it, the longest straight run on a scanned sheet
    — very often the title-block border — would be reported as an exit access
    travel distance, with a citation, confidently. That is worse than not
    measuring, so it is pinned here rather than left to a comment.
    """
    from fbcreview.rules.r_geometry import EGRESS_LAYER

    assert EGRESS_LAYER not in convert.TRACED_LAYER.lower()
    assert "unclassified" in convert.TRACED_LAYER


def test_a_converted_set_leaves_the_egress_rule_abstaining():
    """End to end: after conversion, no path claims to be an egress path."""
    from fbcreview.rules.r_geometry import EGRESS_LAYER

    src = write(mixed_set(), "mixed.pdf")
    before = pdfkind.profile(src)
    dest = src.replace("mixed", "converted")
    convert.convert(src, before.raster_pages, dest)

    doc = pymupdf.open(dest)
    try:
        layers = {
            path.get("layer")
            for number in range(doc.page_count)
            for path in doc[number].get_drawings()
        }
    finally:
        doc.close()

    assert not any(EGRESS_LAYER in (name or "").lower() for name in layers)


# ── mechanics ─────────────────────────────────────────────────────────────
def test_support_reports_what_is_actually_installed():
    caps = convert.support()
    assert isinstance(caps.ocr, bool)
    assert isinstance(caps.vectorise, bool)
    assert caps.detail


def test_vector_pages_are_copied_through_untouched():
    """Re-rendering a good sheet would destroy the geometry the engine wants."""
    src = write(mixed_set(vector_pages=2), "mixed.pdf")
    before = pdfkind.profile(src)
    dest = src.replace("mixed", "converted")
    convert.convert(src, before.raster_pages, dest)

    after = pdfkind.profile(dest)
    for page in range(2):
        assert after.sheets[page].kind == "vector"
        assert after.sheets[page].vector_items == before.sheets[page].vector_items
        assert after.sheets[page].live_chars == before.sheets[page].live_chars


@pytest.mark.skipif(not convert.support().vectorise, reason="OpenCV not installed")
def test_raster_linework_becomes_real_vector_paths_on_a_named_layer():
    src = write(mixed_set(), "mixed.pdf")
    before = pdfkind.profile(src)
    dest = src.replace("mixed", "converted")
    report = convert.convert(src, before.raster_pages, dest)

    assert report.total_segments > 0
    assert report.vectorise_used is True

    doc = pymupdf.open(dest)
    try:
        assert convert.TRACED_LAYER in {v["name"] for v in doc.get_ocgs().values()}
        layers = {p.get("layer") for p in doc[1].get_drawings()}
    finally:
        doc.close()

    # The paths carry the layer — without that, page_geometry() reports nothing.
    assert convert.TRACED_LAYER in layers


@pytest.mark.skipif(not convert.support().ocr, reason="Tesseract not installed")
def test_ocr_recovers_a_live_text_layer():
    src = write(make_raster_pdf(pages=1), "scan.pdf")
    dest = src.replace("scan", "converted")
    report = convert.convert(src, [0], dest, do_vectorise=False)

    assert report.ocr_used is True
    doc = pymupdf.open(dest)
    try:
        assert doc.page_count == 1
    finally:
        doc.close()


def test_missing_ocr_is_degraded_not_fatal():
    """Where Tesseract is absent the sheet still survives as an image, and the
    report says OCR was not used rather than pretending it was."""
    src = write(make_raster_pdf(pages=1), "scan.pdf")
    dest = src.replace("scan", "converted")
    report = convert.convert(src, [0], dest, do_ocr=False, do_vectorise=False)

    assert report.ocr_used is False
    assert Path(dest).exists()
    doc = pymupdf.open(dest)
    try:
        assert doc.page_count == 1
    finally:
        doc.close()


def test_report_is_serialisable_for_firestore():
    src = write(mixed_set(), "mixed.pdf")
    before = pdfkind.profile(src)
    dest = src.replace("mixed", "converted")
    report = convert.convert(src, before.raster_pages, dest)

    encoded = json.dumps(report.to_dict())
    assert json.loads(encoded)["traced_layer"] == convert.TRACED_LAYER


# ── opt-in wiring ─────────────────────────────────────────────────────────
def test_scanned_set_is_refused_without_the_option_and_the_message_offers_it(client):
    r = post_review(client, make_raster_pdf(pages=1), name="scanned.pdf")
    assert r.status_code == 422
    message = r.json()["error"]["message"]
    assert "Rebuild scanned sheets" in message
    assert "minutes rather than seconds" in message


def test_scanned_set_is_accepted_with_the_option(client):
    r = post_review(
        client,
        make_raster_pdf(pages=1),
        options=json.dumps({"convert_raster": True}),
        name="scanned.pdf",
    )
    assert r.status_code == 202

    body = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert body["options"]["convert_raster"] is True
    assert body["source"]["kind"] == "raster"


def test_conversion_adds_its_own_stage_only_when_it_will_run(client):
    plain = post_review(client, make_pdf(pages=1))
    plain_stages = client.get(f"/api/jobs/{plain.json()['id']}").json()["stages"]
    assert convert.TRACED_LAYER not in plain_stages
    assert "Rebuilding scanned sheets" not in plain_stages

    converted = post_review(
        client, mixed_set(), options=json.dumps({"convert_raster": True}), name="mixed.pdf"
    )
    stages = client.get(f"/api/jobs/{converted.json()['id']}").json()["stages"]
    assert "Rebuilding scanned sheets" in stages
    assert len(stages) == len(plain_stages) + 1


def test_convert_raster_is_not_passed_to_the_engine(client):
    """fbcreview.options.ReviewOptions has no such field; splatting the wire
    model into it would raise TypeError inside the request."""
    import dataclasses

    from fbcreview.options import ReviewOptions as EngineOptions

    assert "convert_raster" not in {f.name for f in dataclasses.fields(EngineOptions)}

    r = post_review(
        client, make_pdf(pages=1), options=json.dumps({"convert_raster": True})
    )
    assert r.status_code == 202


def test_conversion_report_is_published_in_the_schema(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "ConversionReport" in schemas
    assert "ConvertedPage" in schemas
    assert "convert_raster" in schemas["ReviewOptions"]["properties"]


# ── reading tables pasted onto readable sheets ────────────────────────────
def pasted_set(pages: int = 1) -> bytes:
    from conftest import make_pdf_with_pasted_table

    return make_pdf_with_pasted_table(pages=pages)


def test_region_read_leaves_the_vector_content_alone():
    """Only the pasted image is read. The sheet's own geometry and live text
    must survive untouched — re-rendering a good sheet would destroy exactly
    what the engine wants."""
    src = write(pasted_set(), "pasted.pdf")
    before = pdfkind.profile(src)
    regions = {p: [(r.x0, r.y0, r.x1, r.y1) for r in before.sheets[p].raster_regions]
               for p in before.region_pages}

    dest = src.replace("pasted", "read")
    convert.read_regions(src, regions, dest)

    after = pdfkind.profile(dest)
    assert after.sheets[0].vector_items == before.sheets[0].vector_items
    assert after.sheets[0].live_chars >= before.sheets[0].live_chars


@pytest.mark.skipif(not convert.support().ocr, reason="Tesseract not installed")
def test_region_read_recovers_the_pasted_rows():
    src = write(pasted_set(), "pasted.pdf")
    before = pdfkind.profile(src)
    regions = {p: [(r.x0, r.y0, r.x1, r.y1) for r in before.sheets[p].raster_regions]
               for p in before.region_pages}

    dest = src.replace("pasted", "read")
    report = convert.read_regions(src, regions, dest)
    assert report.ocr_used is True
    assert report.total_chars > 0

    text = pymupdf.open(dest)[0].get_text().upper()
    # Word boundaries must survive the round trip. Writing the OCR back word by
    # word fuses adjacent cells into "MIXEDOCCUPANCY", which no label match
    # would ever find, so whole lines are written instead.
    assert "MIXED OCCUPANCY" in text
    assert "508.4" in text


def test_region_read_without_ocr_is_a_no_op_not_a_failure():
    src = write(pasted_set(), "pasted.pdf")
    dest = src.replace("pasted", "read")
    # An empty region map is the "nothing to do" case and must still produce a
    # usable file rather than raising.
    report = convert.read_regions(src, {}, dest)
    assert report.converted_pages == []
    assert Path(dest).exists()
    assert pymupdf.open(dest).page_count == 1


def test_a_pasted_table_set_gets_the_rebuild_stage_when_opted_in(client):
    plain = post_review(client, make_pdf(pages=1))
    plain_stages = client.get(f"/api/jobs/{plain.json()['id']}").json()["stages"]

    pasted = post_review(
        client, pasted_set(), options=json.dumps({"convert_raster": True}), name="pasted.pdf"
    )
    stages = client.get(f"/api/jobs/{pasted.json()['id']}").json()["stages"]
    assert "Rebuilding scanned sheets" in stages
    assert len(stages) == len(plain_stages) + 1


# ── rotated sheets ────────────────────────────────────────────────────────
def rotated_pasted_set(rotation: int = 270) -> bytes:
    from conftest import make_pdf_with_pasted_table

    doc = pymupdf.open("pdf", make_pdf_with_pasted_table(pages=1))
    for page in doc:
        page.set_rotation(rotation)
    out = doc.tobytes()
    doc.close()
    return out


@pytest.mark.skipif(not convert.support().ocr, reason="Tesseract not installed")
def test_region_read_recovers_the_rows_from_a_rotated_sheet():
    """The same recovery as the upright case, on a sheet plotted sideways.

    A region whose bbox was never mapped out of unrotated space clips to
    nothing here, and pdfocr_tobytes rejects the zero-height pixmap with
    "Invalid bandwriter header dimensions" — which reads like a corrupt file
    rather than a coordinate-space mistake. Every sheet in the JSP set is
    /Rotate 270, so this path carries real sets, not edge cases.
    """
    src = write(rotated_pasted_set(270), "pasted-rot.pdf")
    before = pdfkind.profile(src)
    regions = {p: [(r.x0, r.y0, r.x1, r.y1) for r in before.sheets[p].raster_regions]
               for p in before.region_pages}
    assert regions, "a rotated sheet must still report its pasted table"

    dest = src.replace("pasted-rot", "read-rot")
    report = convert.read_regions(src, regions, dest)

    assert report.ocr_used is True
    # No region may be silently dropped: the note records a failure per region.
    assert "failed" not in " ".join(p.note for p in report.converted_pages)
    # The region rendered to a real pixmap and reached Tesseract. What the
    # glyphs say is not asserted here: this fixture rotates a sheet that was
    # drawn upright, so its table renders sideways and OCR returns noise. A
    # real set is the other way round — drawn sideways, /Rotate turns it
    # upright — and the upright recovery is pinned by the test above. The
    # regression this guards is the region going off-sheet, which produced a
    # zero-height pixmap and no OCR call at all.
    assert report.total_chars > 0


def test_a_region_off_the_sheet_is_skipped_rather_than_raising():
    """A degenerate region costs one table, never the whole conversion."""
    src = write(pasted_set(), "pasted.pdf")
    dest = src.replace("pasted", "read")
    doc = pymupdf.open(src)
    page = doc[0]
    # Entirely below the sheet.
    off = pymupdf.Rect(0, page.rect.y1 + 500, page.rect.x1, page.rect.y1 + 900)
    assert convert._ocr_region(page, off, "eng") == 0
    doc.close()

    report = convert.read_regions(
        src, {0: [(off.x0, off.y0, off.x1, off.y1)]}, dest
    )
    assert Path(dest).exists()
    assert "failed" not in " ".join(p.note for p in report.converted_pages)


# ── writing onto a CAD-plotted page ───────────────────────────────────────
def unbalanced_page() -> bytes:
    """A sheet whose content stream leaves a transform on the stack.

    `q ... cm` with no matching `Q` is common in plotted CAD output, and it is
    invisible until something is appended: the new content inherits the
    dangling matrix. On the ITEC sheets that matrix put text written at
    (1299, 102) in 21pt down at (92, 1601) in half a point — the words were all
    still there and extraction read them in order, so the rules saw the values
    and only the geometry was wrong.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    page.draw_line((100, 100), (2400, 100))
    xref = page.get_contents()[0]
    stream = doc.xref_stream(xref) or b""
    # Push a scale-and-shift and never pop it, exactly as the real sheets do.
    doc.update_stream(xref, b"q 0.0708 0 0 0.0708 0 1520 cm\n" + stream)
    out = doc.tobytes()
    doc.close()
    return out


def test_recovered_text_lands_where_the_ink_was(tmp_path):
    """The region reader must not inherit the page's dangling transform.

    Nothing here needs OCR: the failure is in how text is written back, so it
    is reproduced with the same TextWriter call the reader makes.
    """
    src = write(unbalanced_page(), "unbalanced.pdf")
    doc = pymupdf.open(src)
    page = doc[0]

    page.wrap_contents()
    writer = pymupdf.TextWriter(page.rect)
    writer.append(pymupdf.Point(1299, 102), "PROBEWORD", fontsize=21.2)
    writer.write_text(page, render_mode=3)

    dest = src.replace("unbalanced", "written")
    doc.save(dest)
    doc.close()

    check = pymupdf.open(dest)
    hits = check[0].search_for("PROBEWORD")
    assert hits, "text was not written at all"
    got = hits[0]
    check.close()

    # Within a line of where it was asked for, at the size it was asked for.
    assert abs(got.x0 - 1299) < 5, f"x drifted to {got.x0}"
    assert abs(got.y1 - 102) < 40, f"y drifted to {got.y1}"
    assert got.height > 15, f"shrank to {got.height:.2f}pt tall"


@pytest.mark.skipif(not convert.support().ocr, reason="Tesseract not installed")
def test_region_read_writes_full_size_text_on_an_unbalanced_page():
    """End to end: the reader balances the page itself, so callers need not."""
    doc = pymupdf.open("pdf", unbalanced_page())
    page = doc[0]
    table = pymupdf.open()
    tp = table.new_page(width=900, height=300)
    tp.insert_text((40, 90), "MIXED OCCUPANCY", fontsize=40)
    tp.insert_text((40, 180), "CONSTRUCTION TYPE", fontsize=40)
    pix = tp.get_pixmap(dpi=150)
    table.close()
    page.insert_image(pymupdf.Rect(300, 300, 1800, 800), pixmap=pix)
    src = write(doc.tobytes(), "unbalanced-table.pdf")
    doc.close()

    dest = src.replace("unbalanced-table", "read-table")
    report = convert.read_regions(src, {0: [(300, 300, 1800, 800)]}, dest)
    assert report.total_chars > 0

    out = pymupdf.open(dest)
    sizes = [s["size"]
             for b in out[0].get_text("dict")["blocks"]
             for l in b.get("lines", [])
             for s in l.get("spans", [])]
    out.close()
    assert sizes, "no text recovered"
    # Not one span may come back microscopic: that is the dangling-matrix bug.
    assert min(sizes) > 2.0, f"smallest span is {min(sizes):.2f}pt"
