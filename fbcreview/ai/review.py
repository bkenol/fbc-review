"""The result review loop: read, decide, check — at most three passes.

`CLAUDE.md`, "AI reads; rules decide", rule 7. After the rules have run, a
reviewer model looks at the finished result beside the set's text and what the
user asked for, and answers one question: would reading any sheet again make
this review more complete or more faithful? It can only answer in the shape of
`schema.ResultReview` — sheets to read again, for named catalog facts, and
notes for the audit record. It has no way to add, remove, edit or re-rank a
finding.

A pass, when the reviewer asks for one:

1. the named sheets are read again by the same reader, with the reviewer's
   pointer appended to the request (`reader.read_document(focus=...)`);
2. what comes back is merged into the set's readings and grounded against the
   sheets like every other reading — a value the sheet does not print is
   rejected here exactly as it would be on the first pass;
3. the facts are rebuilt and the same pure-Python rules run again.

The loop stops as soon as the reviewer is satisfied, a re-read changes nothing,
the reviewer asks for nothing it may ask for, the reviewer or a re-read fails,
or the pass limit is reached. Whatever stops it, the last pass's result is the
review — an AI failure makes a smaller review, never a failed one.

Everything the loop did is a `ReviewTrace`, stored with the job
(`ai_review.json`) and replayed on a re-run, so a re-run makes no call and
reaches the same findings. This module makes no network call itself; the
caller passes the two functions that do (`fbcreview.ai.reviewer`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pymupdf

from ..read.catalog import BY_KEY, FIELDS
from .readings import Readings
from .schema import ResultReview, SheetReading

#: The most passes any configuration can ask for. A pass is one run of the rules.
MAX_PASSES = 3
#: The most sheets one pass may send back to be read again.
MAX_REREAD_SHEETS = 8
#: How much of each sheet's text, and of the whole set's, the reviewer is shown.
SHEET_TEXT_CHARS = 6000
PACKET_TEXT_CHARS = 90000
MAX_HINT_CHARS = 400

#: How a loop ended. Every one leaves the last pass's result standing.
MET = "meets_request"            # the reviewer found nothing a re-read would improve
LIMIT = "max_passes"             # still asking when the pass limit was reached
NO_CHANGE = "no_change"          # a re-read changed nothing, so another pass would repeat itself
NOTHING = "nothing_to_reread"    # not satisfied, but asked for nothing a re-read can do
FAILED = "review_failed"         # the reviewer errored or refused
REREAD_FAILED = "reread_failed"  # the re-read errored


@dataclass
class PassRecord:
    """One check of one pass, and the re-read it led to."""
    number: int                                     # the pass this check looked at, 1-based
    review: Optional[ResultReview] = None
    error: str = ""
    focus: Dict[int, str] = field(default_factory=dict)       # 0-based page -> text appended
    reread: Optional[Readings] = None
    usage: Dict[str, int] = field(default_factory=dict)
    #: Grounded AI values in the store after the re-read's pass, when there was one.
    accepted_after: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "review": self.review.model_dump() if self.review else None,
            "error": self.error,
            "focus": {str(p): t for p, t in sorted(self.focus.items())},
            "reread": self.reread.to_json() if self.reread else None,
            "usage": dict(self.usage),
            "accepted_after": self.accepted_after,
        }

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "PassRecord":
        return cls(
            number=int(d.get("number", 0)),
            review=ResultReview.model_validate(d["review"]) if d.get("review") else None,
            error=str(d.get("error") or ""),
            focus={int(p): str(t) for p, t in (d.get("focus") or {}).items()},
            reread=Readings.from_json(d["reread"]) if d.get("reread") else None,
            usage={k: int(v) for k, v in (d.get("usage") or {}).items()},
            accepted_after=d.get("accepted_after"),
        )


@dataclass
class ReviewTrace:
    """Everything one review loop did — stored with the job, replayed on a re-run."""
    file_sha256: str
    model: str
    prompt_version: str
    max_passes: int
    #: The first-pass readings this loop started from, as `model|prompt version`.
    #: A replay is only valid on top of the same readings.
    reader: str = ""
    records: List[PassRecord] = field(default_factory=list)
    passes: int = 1
    outcome: str = ""

    def usage(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.records:
            for part in (r.usage, r.reread.usage if r.reread else {}):
                for k, v in part.items():
                    out[k] = out.get(k, 0) + int(v)
        return out

    def summary(self) -> Dict[str, Any]:
        """JSON-safe and content-free — counts, never notes or sheet text."""
        return {
            "model": self.model, "prompt_version": self.prompt_version,
            "passes": self.passes, "max_passes": self.max_passes,
            "reviews": sum(1 for r in self.records if r.review is not None),
            "outcome": self.outcome,
            "sheets_reread": sum(len(r.focus) for r in self.records),
            "notes": sum(len(r.review.notes) for r in self.records if r.review),
            "usage": self.usage(),
        }

    def to_json(self) -> Dict[str, Any]:
        return {"file_sha256": self.file_sha256, "model": self.model,
                "prompt_version": self.prompt_version, "max_passes": self.max_passes,
                "reader": self.reader,
                "passes": self.passes, "outcome": self.outcome,
                "records": [r.to_json() for r in self.records]}

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "ReviewTrace":
        return cls(file_sha256=d.get("file_sha256", ""), model=d.get("model", ""),
                   prompt_version=d.get("prompt_version", ""),
                   max_passes=int(d.get("max_passes", MAX_PASSES)),
                   reader=d.get("reader", ""),
                   records=[PassRecord.from_json(r) for r in d.get("records") or []],
                   passes=int(d.get("passes", 1)), outcome=d.get("outcome", ""))


def reader_identity(readings: Readings) -> str:
    return f"{readings.model}|{readings.prompt_version}"


def replayable(trace: ReviewTrace, readings: Readings, model: str, prompt_version: str,
               max_passes: int) -> bool:
    """Whether a stored trace was made from these readings, by this reviewer."""
    return (trace.file_sha256, trace.reader, trace.model, trace.prompt_version,
            trace.max_passes) == (readings.file_sha256, reader_identity(readings), model,
                                  prompt_version, max_passes)


def load_trace(path: str) -> ReviewTrace:
    with open(path, encoding="utf-8") as fh:
        return ReviewTrace.from_json(json.load(fh))


def save_trace(trace: ReviewTrace, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(trace.to_json(), fh, indent=1)


# ── what the reviewer is shown ───────────────────────────────────────────────
def _clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _sheet_texts(pdf_path: str, budget: int = PACKET_TEXT_CHARS) -> List[str]:
    """Each sheet's text, whitespace collapsed, capped per sheet and in total."""
    out: List[str] = []
    left = budget
    doc = pymupdf.open(pdf_path)
    try:
        for p in range(doc.page_count):
            lines = [re.sub(r"\s+", " ", ln).strip()
                     for ln in doc[p].get_text("text").splitlines()]
            text = "\n".join(ln for ln in lines if ln)
            text = _clip(text, min(SHEET_TEXT_CHARS, max(left, 0)))
            left -= len(text)
            out.append(text)
    finally:
        doc.close()
    return out


def packet(pdf_path: str, facts, result, options=None, declaration=None,
           number: int = 1, max_passes: int = MAX_PASSES,
           history: Optional[List[PassRecord]] = None) -> List[Dict[str, Any]]:
    """The reviewer's user message: the request, the result, the facts, the sheets."""
    request: Dict[str, Any] = {}
    if options is not None:
        request["review_options"] = {k: v for k, v in options.to_dict().items()
                                     if k != "email_to"}
    if declaration is not None and not declaration.is_empty():
        request["declaration"] = {k: v for k, v in declaration.to_dict().items()
                                  if v not in (None, "", [])}
    sheets = [{"page": s.index + 1, "sheet": s.code, "title": s.title,
               "discipline": s.discipline} for s in facts.sheets]
    findings = [{"id": f.fid, "rule": f.rule_id, "status": f.status, "severity": f.severity,
                 "sheet": f.sheet, "page": f.page + 1, "title": f.title,
                 "result": _clip(f.result, 400), "code": f.code, "basis": f.basis}
                for f in result.findings]
    abstentions = [{"rule": a.rule_id, "reason": a.reason, "detail": a.detail or ""}
                   for a in result.abstentions]
    resolved = []
    for key, r in sorted(facts.store.summary().items()):
        best = (r.get("claims") or [{}])[0]
        resolved.append({"fact": key, "value": r.get("value"), "confidence": r.get("confidence"),
                         "sheets": r.get("sheets"), "read_by": r.get("methods"),
                         "as_printed": _clip(str(best.get("raw", "")), 160),
                         "disagreeing_values": [g[0].get("value") for g in r.get("rivals") or []
                                                if g]})
    rejected = [{"page": r.get("page", 0) + 1, "fact": r.get("field"), "value": r.get("value"),
                 "quote": _clip(str(r.get("quote", "")), 160), "why": r.get("reason")}
                for r in facts.store.rejected[:60]]
    earlier = [{"pass": h.number,
                "reread": [{"page": p + 1} for p in sorted(h.focus)],
                "fields": sorted({k for rq in (h.review.rereads if h.review else [])
                                  for k in rq.fields}),
                "ai_values_used_after": h.accepted_after}
               for h in history or [] if h.focus]
    body = {
        "pass": number, "max_passes": max_passes,
        "what_the_user_asked_for": request,
        "sheets": sheets,
        "findings": findings,
        "abstentions": abstentions,
        "facts_the_rules_used": resolved,
        "ai_values_rejected_by_the_sheet_check": rejected,
        "earlier_rereads_in_this_review": earlier,
    }
    texts = _sheet_texts(pdf_path)
    sheet_text = "\n\n".join(
        f"=== page {i + 1} · {facts.sheets[i].code if i < len(facts.sheets) else ''} ===\n"
        f"{t or '(no live text on this sheet — its content may be an image)'}"
        for i, t in enumerate(texts))
    return [
        {"type": "text", "text": "The review packet (JSON):\n" + json.dumps(body, default=str)},
        {"type": "text", "text": "Each sheet's text layer, capped:\n" + sheet_text},
    ]


# ── what the reviewer may ask for ────────────────────────────────────────────
def plan_rereads(review: ResultReview, page_count: int,
                 tried: Set[Tuple[int, str]]) -> Dict[int, str]:
    """The re-reads a review may have: named pages, catalog facts, not tried before.

    Everything else it asked for is dropped: a page the set does not have, a
    key the catalog does not define, a page and fact an earlier pass already
    read again. Returns 0-based page -> the text appended to that sheet's request.
    """
    wanted: Dict[int, Tuple[List[str], List[str]]] = {}
    for rq in review.rereads:
        page = rq.page - 1
        if not 0 <= page < page_count:
            continue
        keys = [k for k in dict.fromkeys(rq.fields) if k in BY_KEY and (page, k) not in tried]
        if not keys:
            continue
        ks, hints = wanted.setdefault(page, ([], []))
        ks.extend(k for k in keys if k not in ks)
        if rq.hint.strip():
            hints.append(_clip(rq.hint.strip(), MAX_HINT_CHARS))
    out: Dict[int, str] = {}
    for page in sorted(wanted)[:MAX_REREAD_SHEETS]:
        keys, hints = wanted[page]
        lines = ["A check of the finished review asked for this sheet to be read again "
                 "for these facts, which the review appears to have missed or misread:"]
        lines += [f"- {k}: {BY_KEY[k].description}" for k in keys]
        if hints:
            lines.append("Pointer from the check (it may be wrong): " + " ".join(hints))
        lines.append("Every rule above still holds. Report a value only if it is printed on "
                     "this sheet, with its verbatim quote. If it is not there, omit it — an "
                     "empty answer is correct.")
        out[page] = "\n".join(lines)
    return out


def merge_readings(base: Readings, extra: Readings) -> Readings:
    """The set's readings with a re-read's added — duplicates once, the first pass kept."""
    merged = Readings(base.file_sha256, base.model, base.prompt_version,
                      sheets={p: SheetReading(sheet_number=r.sheet_number, fields=list(r.fields))
                              for p, r in base.sheets.items()},
                      errors=dict(base.errors), usage=dict(base.usage))
    for page, reading in extra.sheets.items():
        into = merged.sheets.setdefault(page, SheetReading(sheet_number=reading.sheet_number))
        seen = {(f.field, f.value, f.quote, f.role) for f in into.fields}
        for f in reading.fields:
            if (f.field, f.value, f.quote, f.role) not in seen:
                into.fields.append(f)
                seen.add((f.field, f.value, f.quote, f.role))
        merged.errors.pop(page, None)
    for k, v in extra.usage.items():
        merged.usage[k] = merged.usage.get(k, 0) + int(v)
    return merged


def fingerprint(result) -> Tuple:
    """What a pass decided, to tell whether a re-read changed anything."""
    return (tuple(sorted((f.fid, f.rule_id, f.status, f.severity, f.sheet, f.page, f.result)
                         for f in result.findings)),
            tuple(sorted((a.rule_id, a.reason, a.detail or "") for a in result.abstentions)))


# ── the loop ─────────────────────────────────────────────────────────────────
RunPass = Callable[[Readings], Tuple[Any, Any]]
Check = Callable[[Any, Any, int, List[PassRecord]], Tuple[ResultReview, Dict[str, int]]]
Reread = Callable[[Dict[int, str]], Readings]


def review_loop(first: Tuple[Any, Any], readings: Readings, run_pass: RunPass,
                check: Optional[Check], reread: Optional[Reread], trace: ReviewTrace,
                replay: Optional[ReviewTrace] = None) -> Tuple[Any, Any, Readings]:
    """Check the first pass's result, and re-read and re-run while the check asks.

    `first` is the (facts, result) the rules already produced from `readings`.
    `check` and `reread` are the two calls that reach the model; with `replay`
    they are never called, and the recorded answers are used in their place.
    Fills `trace` and returns the last pass's (facts, result, readings).
    """
    facts, result = first
    limit = max(1, min(trace.max_passes, MAX_PASSES))
    tried: Set[Tuple[int, str]] = set()
    trace.passes = 1
    for number in range(1, limit + 1):
        recorded = None
        if replay is not None:
            if number > len(replay.records):
                trace.outcome = replay.outcome
                break
            recorded = replay.records[number - 1]

        rec = PassRecord(number)
        trace.records.append(rec)
        if recorded is not None:
            rec.review, rec.error, rec.usage = recorded.review, recorded.error, dict(recorded.usage)
        else:
            try:
                rec.review, rec.usage = check(facts, result, number, trace.records[:-1])
            except Exception as exc:                     # noqa: BLE001 — the last pass stands
                rec.error = type(exc).__name__
        if rec.review is None:
            trace.outcome = FAILED
            break
        if rec.review.meets_request:
            trace.outcome = MET
            break
        if number == limit:
            trace.outcome = LIMIT
            break

        rec.focus = plan_rereads(rec.review, len(facts.sheets), tried)
        if not rec.focus:
            trace.outcome = NOTHING
            break
        for page in rec.focus:
            for rq in rec.review.rereads:
                if rq.page - 1 == page:
                    tried.update((page, k) for k in rq.fields)
        if recorded is not None:
            rec.reread = recorded.reread
        else:
            try:
                rec.reread = reread(rec.focus)
            except Exception as exc:                     # noqa: BLE001 — the last pass stands
                rec.error = type(exc).__name__
        if rec.reread is None:
            trace.outcome = REREAD_FAILED
            break

        readings = merge_readings(readings, rec.reread)
        before = fingerprint(result)
        facts, result = run_pass(readings)
        trace.passes += 1
        rec.accepted_after = (readings.grounded or {}).get("accepted")
        if fingerprint(result) == before:
            trace.outcome = NO_CHANGE
            break
    return facts, result, readings


def catalog_keys() -> List[str]:
    return [f.key for f in FIELDS]
