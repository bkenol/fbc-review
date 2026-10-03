"""`run.py` on a drawing: plotted in-process, reviewed as the plot, marked up as DXF.

The drawing is built here with ezdxf (`test_worker_cad.make_dxf`); no client
drawing and no converter are needed for a DXF.
"""
from __future__ import annotations

import json
import zipfile

import pytest

pytest.importorskip("ezdxf")

import run  # noqa: E402
from test_worker_cad import make_dxf  # noqa: E402


@pytest.fixture(autouse=True)
def _no_local_env(monkeypatch, tmp_path):
    """Never pick up a developer's secrets/local.env, or their key."""
    monkeypatch.setenv("FBC_ENV_FILE", str(tmp_path / "absent.env"))
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FBC_AI_READING",
                "FBC_AI_CACHE_DIR"):
        monkeypatch.delenv(key, raising=False)


def test_a_dxf_prints_its_sheets_layers_and_each_viewports_scale(tmp_path, capsys):
    drawing = make_dxf(tmp_path / "tower.dxf")
    assert run.main(["run.py", str(drawing)]) == 0
    out = capsys.readouterr().out

    assert "sheets      1  (A-101" in out
    assert "in the drawing's layer table" in out
    assert "plotted     1 sheets, 1 numbered by their title block" in out
    assert "layout 'A101 PLAN', 1 viewport(s)" in out
    # 1/4" = 1'-0" is 1:48, which on the plotted sheet is 18 points to the foot.
    assert "1:48 = 18 pt/ft" in out
    assert "no model called" in out


def test_markup_dxf_writes_the_findings_into_the_drawing(tmp_path, capsys):
    drawing = make_dxf(tmp_path / "tower.dxf")
    out_zip = tmp_path / "out" / "tower-review.zip"
    out_zip.parent.mkdir()
    keep = tmp_path / "plot"
    assert run.main(["run.py", str(drawing), "--markup-dxf", str(out_zip),
                     "--cad-dir", str(keep), "--json", str(tmp_path / "f.json")]) == 0
    assert f"wrote {out_zip}" in capsys.readouterr().out

    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
        assert "READ ME.txt" in names
        dxf_name = next(n for n in names if n.endswith(".dxf"))
        assert dxf_name.startswith("tower")
        assert "FBC-REVIEW" in zf.read(dxf_name).decode("utf-8", "replace")

    # --cad-dir keeps the plot and its sidecar; the findings are on that plot.
    assert (keep / "rendered.pdf").is_file() and (keep / "cad.json").is_file()
    findings = json.loads((tmp_path / "f.json").read_text())
    assert findings["meta"]["cad"]["sheets"][0]["number"] == "A-101"


def test_the_plot_is_removed_without_cad_dir(tmp_path, monkeypatch):
    import tempfile

    made = []
    real = tempfile.mkdtemp

    def tracking(*a, **k):
        path = real(*a, **k)
        made.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", tracking)
    assert run.main(["run.py", str(make_dxf(tmp_path / "tower.dxf"))]) == 0
    assert made and not any(__import__("os").path.exists(p) for p in made)


def test_markup_dxf_on_a_pdf_says_it_needs_a_drawing(tmp_path, capsys):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=1224, height=792).insert_text((40, 100), "OCCUPANCY: BUSINESS")
    pdf = tmp_path / "set.pdf"
    doc.save(str(pdf))
    assert run.main(["run.py", str(pdf), "--markup-dxf", str(tmp_path / "x.zip")]) == 2
    assert "needs a drawing" in capsys.readouterr().out
    assert not (tmp_path / "x.zip").exists()


def test_readings_are_checked_against_the_drawing_not_the_plot(tmp_path, capsys):
    """The plot's bytes differ every run; readings made from this drawing must
    replay, and readings made from any other file must not."""
    import hashlib

    from fbcreview.ai.prompt import PROMPT_VERSION
    from fbcreview.ai.readings import Readings, plotted_identity, save_readings
    from fbcreview.cad import ingest

    drawing = make_dxf(tmp_path / "tower.dxf")
    # Keyed by the drawing and what plotted it — reproduced here from an
    # independent plot, which is exactly what a later run of run.py does.
    plot = ingest(str(drawing), str(tmp_path / "plot"), name="tower.dxf")
    ident = plotted_identity(hashlib.sha256(drawing.read_bytes()).hexdigest(),
                             plot.data, plot.pdf_path)

    good = tmp_path / "good.json"
    save_readings(Readings(ident, "claude-opus-5", PROMPT_VERSION, sheets={}), str(good))
    assert run.main(["run.py", str(drawing), "--readings", str(good)]) == 0
    assert "replayed from" in capsys.readouterr().out

    other = tmp_path / "other.json"
    save_readings(Readings("0" * 64, "claude-opus-5", PROMPT_VERSION, sheets={}), str(other))
    assert run.main(["run.py", str(drawing), "--readings", str(other)]) == 2
    assert "different file" in capsys.readouterr().out


def test_an_unreadable_drawing_is_refused_in_prose(tmp_path, capsys):
    broken = tmp_path / "broken.dxf"
    broken.write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    assert run.main(["run.py", str(broken)]) == 2
    assert "drawing refused" in capsys.readouterr().out
