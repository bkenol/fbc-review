"""`run.py`: the deterministic default, and AI readings kept, replayed and refused."""
from __future__ import annotations

import json

import pymupdf
import pytest

import run
from fbcreview.ai.prompt import PROMPT_VERSION
from fbcreview.ai.readings import Readings, file_sha256, save_readings
from fbcreview.ai.schema import FieldReading, SheetReading

NOTE = "THIS TENANT SPACE IS DESIGNED FOR AN OCCUPANT LOAD OF 48 PERSONS."


@pytest.fixture(autouse=True)
def _no_local_env(monkeypatch, tmp_path):
    """Never pick up a developer's secrets/local.env, or their key."""
    monkeypatch.setenv("FBC_ENV_FILE", str(tmp_path / "absent.env"))
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FBC_AI_READING", "FBC_AI_CACHE_DIR"):
        monkeypatch.delenv(key, raising=False)


def _set(tmp_path, name="set.pdf", extra=""):
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    page.insert_text((40, 100), "OCCUPANCY: BUSINESS", fontsize=9)
    page.insert_text((40, 115), NOTE + extra, fontsize=9)
    page.insert_text((1100, 760), "G-1", fontsize=15)
    path = tmp_path / name
    doc.save(str(path))
    return str(path)


def _reading(sha):
    return Readings(sha, "claude-opus-5", PROMPT_VERSION, sheets={0: SheetReading(
        sheet_number="G-1", fields=[FieldReading(field="occupant_load", value="48",
                                                 quote="OCCUPANT LOAD OF 48 PERSONS")])})


def test_without_a_flag_nothing_is_read_by_a_model(tmp_path, capsys):
    assert run.main(["run.py", _set(tmp_path)]) == 0
    assert "no model called" in capsys.readouterr().out


def test_saved_readings_replay_without_a_call(tmp_path, capsys, monkeypatch):
    path = _set(tmp_path)
    saved = str(tmp_path / "readings.json")
    save_readings(_reading(file_sha256(path)), saved)

    def no_call(*_a, **_k):
        raise AssertionError("a replay called the API")
    monkeypatch.setattr("fbcreview.ai.reader.read_document", no_call)

    out_json = str(tmp_path / "out.json")
    assert run.main(["run.py", path, "--readings", saved, "--json", out_json]) == 0
    out = capsys.readouterr().out
    assert "replayed from" in out and "1 values found on the sheet" in out
    meta = json.load(open(out_json))["meta"]
    assert meta["ai_reading"]["accepted"] == 1


def test_readings_from_another_file_are_refused(tmp_path, capsys):
    path = _set(tmp_path)
    other = _set(tmp_path, "other.pdf", extra=" REVISED")
    saved = str(tmp_path / "readings.json")
    save_readings(_reading(file_sha256(other)), saved)
    assert run.main(["run.py", path, "--readings", saved]) == 2
    assert "different file" in capsys.readouterr().out


def test_ai_needs_a_key_and_says_so(tmp_path, capsys):
    assert run.main(["run.py", _set(tmp_path), "--ai"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_ai_readings_can_be_kept_for_replay(tmp_path, capsys, monkeypatch):
    path = _set(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    calls = []

    def fake(src, config, cache=None, **_kw):
        calls.append(config.model)
        return _reading(file_sha256(src))
    monkeypatch.setattr("fbcreview.ai.reader.read_document", fake)

    saved = str(tmp_path / "kept.json")
    assert run.main(["run.py", path, "--ai", "--save-readings", saved]) == 0
    assert calls == ["claude-opus-5"]
    assert json.load(open(saved))["sheets"]["0"]["fields"][0]["value"] == "48"
    assert "read by claude-opus-5 at medium effort" in capsys.readouterr().out
