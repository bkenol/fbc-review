"""The AI reader: one Claude request per sheet, returning located values.

This is the only module in the engine that calls a model, and it is reached only
by the caller that chose to — `webapp/worker.py` when the deployment has AI
reading on, or `run.py --ai`. What it returns is data (`Readings`); nothing it
returns is used until `fbcreview.ai.grounding` has checked it against the sheet.

The request, per sheet (`fbcreview.ai.prompt`): a cached system prompt carrying
the job and the field catalog, then the sheet — an overview image, a crop of each
pasted raster region, and the positioned text layer. The response is structured
output validated against `SheetReading`, which has no field a severity or a
verdict could travel in.

Failure is never the review's failure. A sheet that times out, errors or is
refused is recorded in `Readings.errors` and simply not read; the deterministic
reader has already read it. The whole pass has a deadline, so one stuck sheet
cannot hold a job.

Configuration — all environment, all off unless set:

    FBC_AI_READING=on          and an API key (ANTHROPIC_API_KEY), or it is off
    FBC_AI_MODEL               default claude-opus-5
    FBC_AI_EFFORT              default medium
    FBC_AI_CONCURRENCY         default 6
    FBC_AI_MAX_SHEETS          default 60
    FBC_AI_TIMEOUT_S           per request, default 240
    FBC_AI_DEADLINE_S          whole set, default 900
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import pymupdf

from ..layout import page_layout
from .prompt import PROMPT_VERSION, sheet_content, system_prompt
from .readings import Readings, ReadingsCache, file_sha256
from .schema import SheetReading

log = logging.getLogger("fbc.ai")

DEFAULT_MODEL = "claude-opus-5"
#: Server-side refusal fallback: on a policy decline the API re-runs the request
#: on the model Anthropic recommends for that refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: The levels `output_config.effort` accepts. Anything else would be a 400 on
#: every sheet — a silent loss of the whole AI pass — so it falls back instead.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "medium"


@dataclass(frozen=True)
class ReaderConfig:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    concurrency: int = 6
    max_sheets: int = 60
    timeout_s: float = 240.0
    deadline_s: float = 900.0
    max_tokens: int = 16000

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> Optional["ReaderConfig"]:
        """The configured reader, or None when AI reading is off.

        Off unless both the switch and a credential are present: a deployment
        does not start sending client drawings to an API because a key happens
        to be in its environment for something else.
        """
        e = os.environ if env is None else env
        if (e.get("FBC_AI_READING") or "").strip().lower() not in ("on", "1", "true", "yes"):
            return None
        if not (e.get("ANTHROPIC_API_KEY") or e.get("ANTHROPIC_AUTH_TOKEN")):
            return None

        def num(key, default, cast):
            try:
                return cast(e.get(key)) if e.get(key) else default
            except ValueError:
                return default

        effort = (e.get("FBC_AI_EFFORT") or DEFAULT_EFFORT).strip().lower()
        if effort not in EFFORTS:
            log.warning("FBC_AI_EFFORT is not an effort level; using the default",
                        extra={"default": DEFAULT_EFFORT})
            effort = DEFAULT_EFFORT
        return cls(model=(e.get("FBC_AI_MODEL") or DEFAULT_MODEL).strip(),
                   effort=effort,
                   concurrency=max(1, num("FBC_AI_CONCURRENCY", 6, int)),
                   max_sheets=max(1, num("FBC_AI_MAX_SHEETS", 60, int)),
                   timeout_s=num("FBC_AI_TIMEOUT_S", 240.0, float),
                   deadline_s=num("FBC_AI_DEADLINE_S", 900.0, float))


def make_client(config: ReaderConfig):
    """An Anthropic client. Imported here so nothing else pays for the import."""
    import anthropic
    headers = {}
    workspace = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    return anthropic.Anthropic(timeout=config.timeout_s, max_retries=2,
                               default_headers=headers or None)


def _read_sheet(client, config: ReaderConfig, system: str, content
                ) -> Tuple[SheetReading, Dict[str, int]]:
    response = client.beta.messages.parse(
        model=config.model,
        max_tokens=config.max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config={"effort": config.effort},
        output_format=SheetReading,
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if getattr(response, "stop_reason", None) == "refusal":
        raise RefusedError("the model declined to read this sheet")
    parsed = getattr(response, "parsed_output", None)
    if parsed is None:
        raise ValueError(f"no structured output (stop reason {response.stop_reason})")
    usage = getattr(response, "usage", None)
    return parsed, {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


class RefusedError(RuntimeError):
    pass


def read_document(pdf_path: str, config: ReaderConfig, client=None,
                  sheet_codes: Optional[Dict[int, str]] = None,
                  sheet_titles: Optional[Dict[int, str]] = None,
                  cache: Optional[ReadingsCache] = None,
                  on_sheet: Optional[Callable[[int, int], None]] = None,
                  identity: Optional[str] = None) -> Readings:
    """Read every sheet of a set. Never raises for a sheet; see `Readings.errors`.

    `identity` is what the readings are keyed by — the file's SHA-256 unless the
    caller read a file it rebuilt from another (`readings.source_identity`).
    """
    sha = identity or file_sha256(pdf_path)
    if cache is not None:
        hit = cache.get(sha, config.model, PROMPT_VERSION)
        if hit is not None:
            log.info("ai readings replayed from cache", extra={"sheets": len(hit.sheets)})
            return hit

    readings = Readings(sha, config.model, PROMPT_VERSION)
    client = client or make_client(config)
    system = system_prompt()

    doc = pymupdf.open(pdf_path)
    try:
        if sheet_codes is None:
            from ..extract.document import sheet_index
            sheets = sheet_index(doc, {p: doc[p].get_text() for p in range(doc.page_count)})
            sheet_codes = {s.index: s.code for s in sheets}
            sheet_titles = sheet_titles or {s.index: s.title for s in sheets}
        codes = sheet_codes or {}
        titles = sheet_titles or {}
        total = doc.page_count
        pages = list(range(min(total, config.max_sheets)))
        for p in range(len(pages), total):
            readings.errors[p] = "not read: over the sheet limit for AI reading"
        # Build each request before the pool starts: PyMuPDF documents are not
        # safe to share across threads, and rendering is quick next to the call.
        requests = {p: sheet_content(doc[p], page_layout(doc[p]), codes.get(p, ""),
                                     titles.get(p, ""), total) for p in pages}
    finally:
        doc.close()

    started = time.monotonic()
    done = 0
    # Not a `with` block: leaving one waits for every running request, so a
    # stuck sheet would hold the review until its HTTP timeout even after the
    # deadline. Past the deadline the pool is abandoned instead — its thread
    # finishes, or times out, on its own, and the review carries on.
    pool = ThreadPoolExecutor(max_workers=config.concurrency)
    try:
        futures = {pool.submit(_read_sheet, client, config, system, requests[p]): p
                   for p in pages}
        pending = set(futures)
        while pending:
            remaining = config.deadline_s - (time.monotonic() - started)
            if remaining <= 0:
                for f in pending:
                    f.cancel()
                    readings.errors[futures[f]] = "not read: the AI reading deadline passed"
                break
            finished, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            for f in finished:
                page = futures[f]
                try:
                    sheet, usage = f.result()
                    readings.sheets[page] = sheet
                    for k, v in usage.items():
                        readings.usage[k] = readings.usage.get(k, 0) + int(v)
                except RefusedError as exc:
                    readings.errors[page] = f"refused: {exc}"
                except Exception as exc:                 # noqa: BLE001 — a sheet, not the review
                    readings.errors[page] = f"failed: {type(exc).__name__}"
                    log.warning("ai reading failed for a sheet",
                                extra={"page": page + 1, "error": type(exc).__name__})
                done += 1
                if on_sheet is not None:
                    on_sheet(done, len(pages))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if cache is not None:
        cache.put(readings)
    return readings
