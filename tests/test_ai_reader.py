"""The AI reader's request, its failure modes, and its switch — with no network.

The request shape is checked against the real, pinned SDK through a mock HTTP
transport, so a parameter the SDK would reject fails here rather than in
production. Everything else uses a fake client.
"""
from __future__ import annotations

import json
import threading
import time

import pymupdf
import pytest

from fbcreview.ai import reader as R
from fbcreview.ai.readings import ReadingsCache
from fbcreview.ai.schema import FieldReading, SheetReading


def _pdf(tmp_path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page(width=1224, height=792)
        p.insert_text((40, 100), "OCCUPANT LOAD: 70", fontsize=9)
        p.insert_text((1100, 760), f"G-{i}", fontsize=15)
    path = tmp_path / "set.pdf"
    doc.save(str(path))
    return str(path)


class _Response:
    def __init__(self, parsed=None, stop="end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop
        self.usage = type("U", (), {"input_tokens": 100, "output_tokens": 10,
                                    "cache_read_input_tokens": 80})()


class _Client:
    def __init__(self, behaviour):
        self.calls = 0
        self._lock = threading.Lock()
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                with outer._lock:
                    outer.calls += 1
                    n = outer.calls
                return behaviour(n, kwargs)

        self.beta = type("B", (), {"messages": _Messages()})()


def _ok(n, kwargs):
    return _Response(SheetReading(sheet_number=f"G-{n}", fields=[
        FieldReading(field="occupant_load", value="70", quote="OCCUPANT LOAD: 70")]))


# ══ the switch ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize("env,on", [
    ({}, False),
    ({"FBC_AI_READING": "on"}, False),                          # no credential
    ({"ANTHROPIC_API_KEY": "sk-ant-x"}, False),                 # key alone is not consent
    ({"FBC_AI_READING": "on", "ANTHROPIC_API_KEY": "sk-ant-x"}, True),
    ({"FBC_AI_READING": "off", "ANTHROPIC_API_KEY": "sk-ant-x"}, False),
])
def test_ai_reading_needs_the_switch_and_a_credential(env, on):
    assert (R.ReaderConfig.from_env(env) is not None) is on


def test_the_configured_model_and_limits_are_read():
    c = R.ReaderConfig.from_env({"FBC_AI_READING": "on", "ANTHROPIC_API_KEY": "k",
                                 "FBC_AI_MODEL": "claude-opus-5", "FBC_AI_CONCURRENCY": "3",
                                 "FBC_AI_MAX_SHEETS": "notanumber"})
    assert c.model == "claude-opus-5" and c.concurrency == 3 and c.max_sheets == 60


@pytest.mark.parametrize("given,used", [("HIGH", "high"), ("xhigh", "xhigh"),
                                        ("medum", "medium"), ("", "medium")])
def test_an_effort_the_api_would_reject_falls_back_rather_than_failing_every_sheet(given, used):
    c = R.ReaderConfig.from_env({"FBC_AI_READING": "on", "ANTHROPIC_API_KEY": "k",
                                 "FBC_AI_EFFORT": given})
    assert c.effort == used


# ══ the request, through the real SDK ══════════════════════════════════════
def test_the_request_is_one_the_sdk_accepts_and_sends_as_designed():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": json.dumps({"sheet_number": "G-1", "fields": [
                {"field": "occupant_load", "value": "70", "quote": "OCCUPANT LOAD 70",
                 "role": ""}]})}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = anthropic.Anthropic(
        api_key="sk-ant-test", base_url="http://api.test",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    reading, usage = R._read_sheet(client, R.ReaderConfig(), "system",
                                   [{"type": "text", "text": "sheet"}])
    assert reading.fields[0].value == "70"
    body = seen["body"]
    assert body["model"] == "claude-opus-5"
    assert body["fallbacks"] == "default"
    assert R.FALLBACK_BETA in seen["headers"]["anthropic-beta"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_the_system_prompt_is_the_same_for_every_sheet():
    """It is the cached prefix; anything per-request in it would miss the cache."""
    from fbcreview.ai.prompt import system_prompt
    assert system_prompt() == system_prompt()
    assert "occupant_load" in system_prompt() and "egress.common_path" in system_prompt()


# ══ reading a set ══════════════════════════════════════════════════════════
def test_every_sheet_is_read(tmp_path):
    client = _Client(_ok)
    out = R.read_document(_pdf(tmp_path, 3), R.ReaderConfig(concurrency=2), client=client)
    assert sorted(out.sheets) == [0, 1, 2] and not out.errors
    assert out.usage["input_tokens"] == 300 and client.calls == 3


def test_a_refused_sheet_is_skipped_not_fatal(tmp_path):
    def behaviour(n, kwargs):
        return _Response(None, stop="refusal") if n == 1 else _ok(n, kwargs)
    out = R.read_document(_pdf(tmp_path, 2), R.ReaderConfig(concurrency=1), client=_Client(behaviour))
    assert len(out.sheets) == 1 and len(out.errors) == 1
    assert next(iter(out.errors.values())).startswith("refused")


def test_an_api_error_is_recorded_without_its_message(tmp_path):
    def behaviour(n, kwargs):
        raise ConnectionError("OCCUPANT LOAD: 70 — sheet text must never reach a log")
    out = R.read_document(_pdf(tmp_path, 1), R.ReaderConfig(), client=_Client(behaviour))
    assert out.errors == {0: "failed: ConnectionError"}


def test_a_stuck_sheet_cannot_hold_the_review(tmp_path):
    def behaviour(n, kwargs):
        time.sleep(2.0)
        return _ok(n, kwargs)
    t0 = time.monotonic()
    out = R.read_document(_pdf(tmp_path, 1), R.ReaderConfig(deadline_s=0.3),
                          client=_Client(behaviour))
    assert time.monotonic() - t0 < 1.5
    assert "deadline" in out.errors.get(0, "")


def test_sheets_over_the_limit_are_not_sent(tmp_path):
    client = _Client(_ok)
    out = R.read_document(_pdf(tmp_path, 3), R.ReaderConfig(max_sheets=2), client=client)
    assert client.calls == 2 and "over the sheet limit" in out.errors[2]


def test_a_cached_reading_is_replayed_without_a_call(tmp_path):
    path = _pdf(tmp_path, 2)
    cache = ReadingsCache(str(tmp_path / "cache"))
    first = _Client(_ok)
    R.read_document(path, R.ReaderConfig(), client=first, cache=cache)
    again = _Client(_ok)
    out = R.read_document(path, R.ReaderConfig(), client=again, cache=cache)
    assert first.calls == 2 and again.calls == 0 and len(out.sheets) == 2


def test_a_pass_with_a_transient_failure_is_not_cached_for_the_next_upload(tmp_path):
    path = _pdf(tmp_path, 2)
    cache = ReadingsCache(str(tmp_path / "cache"))

    def flaky(n, kwargs):
        if n == 1:
            raise ConnectionError("overloaded")
        return _ok(n, kwargs)
    R.read_document(path, R.ReaderConfig(concurrency=1), client=_Client(flaky), cache=cache)
    again = _Client(_ok)
    out = R.read_document(path, R.ReaderConfig(), client=again, cache=cache)
    assert again.calls == 2 and not out.errors


def test_a_refusal_is_the_same_answer_next_time_and_is_cached(tmp_path):
    path = _pdf(tmp_path, 2)
    cache = ReadingsCache(str(tmp_path / "cache"))

    def refuses_one(n, kwargs):
        return _Response(None, stop="refusal") if n == 1 else _ok(n, kwargs)
    R.read_document(path, R.ReaderConfig(concurrency=1), client=_Client(refuses_one), cache=cache)
    again = _Client(_ok)
    out = R.read_document(path, R.ReaderConfig(), client=again, cache=cache)
    assert again.calls == 0 and len(out.errors) == 1
