"""The result review loop: read, decide, check and correct — at most three passes.

`CLAUDE.md`, rule 7, as amended by the owner on 2026-09-28. After the rules
have run, a reviewer model looks at the finished result beside the set's text
and what the user asked for, and may:

* **edit the findings directly** — revise a severity, status, title, result,
  remedy or citation; add a finding the rules missed; withdraw one the sheets
  or the request show is wrong (`schema.FindingEdit`);
* **send sheets back to be read again** for named catalog facts, after which
  the same pure-Python rules run again and its edits are re-applied on top;
* leave **notes** for the audit record.

The passes have different jobs (`MODES`):

1. **check** — find what is wrong or missing; ask for re-reads; edit only what
   the sheet plainly contradicts, and note the rest for pass 2;
2. **edit** — the active pass: act on every pass-1 note and correct everything
   else it can support from the sheets, directly;
3. **verify** — review the edits made so far and correct any that went wrong.

Two things hold for every edit, because they are what makes an AI-edited
review something a person can act on (`apply_edits`):

* **Evidence.** Adding or withdrawing a finding, or changing its severity or
  status, needs a quote that is printed on the sheet — found with the same
  locator the grounding gate uses. An edit without one is rejected and recorded.
* **Provenance.** Every applied edit is labelled on the finding — its result
  text says it was revised or raised by the AI review and why, and
  `findings.json` carries the structured record (`revisions`). A withdrawn
  finding becomes an abstention saying so, so "withdrawn" never reads as
  "checked and passed".

The loop stops when the reviewer is satisfied, a pass changes nothing, any AI
call fails, or after three passes — held here whatever is configured. Whatever
stops it, the last good state is the review. Everything it did is a
`ReviewTrace`, stored with the job (`ai_review.json`) and replayed on a re-run
with no call: validation is deterministic, so the same trace gives the same
findings. This module makes no network call itself.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pymupdf

from ..confidence import Abstention
from ..layout import page_layout
from ..payload import _keys, finding_identity
from ..read.catalog import BY_KEY, FIELDS
from .grounding import locate
from .readings import Readings
from .schema import FindingEdit, ResultReview, SheetReading

#: The most passes any configuration can ask for. A pass is one AI check.
MAX_PASSES = 3
#: The most sheets one pass may send back to be read again.
MAX_REREAD_SHEETS = 8
#: The most edits one pass may make.
MAX_EDITS = 40
#: How much of each sheet's text, and of the whole set's, the reviewer is shown.
SHEET_TEXT_CHARS = 6000
PACKET_TEXT_CHARS = 90000
MAX_HINT_CHARS = 400
#: The rule id and fid prefix of a finding the AI review raised itself.
AI_RULE = "AI.REVIEW"
AI_FID = "AI-"

LADDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
PASSED = ("VERIFIED", "MEASURED")

#: How a loop ended. Every one leaves the last good state standing.
MET = "meets_request"            # the reviewer is satisfied with the result
LIMIT = "max_passes"             # still correcting when the pass limit was reached
NO_CHANGE = "no_change"          # a pass after the first changed nothing
FAILED = "review_failed"         # the reviewer errored or refused
REREAD_FAILED = "reread_failed"  # a re-read errored

CHECK, EDIT, VERIFY = "check", "edit", "verify"

MODES = {
    CHECK: (
        "PASS {n} OF {m} — CHECK. Find everything that is wrong or missing. Ask for re-reads "
        "where a fact the rules need is printed on a sheet but was missed. Edit findings in "
        "this pass only where a sheet plainly contradicts them; put everything else you find "
        "in notes, one problem per note — the next pass acts on every one."),
    EDIT: (
        "PASS {n} OF {m} — EDIT. This is the pass where the result is corrected, so be "
        "active. Act on every note from the earlier pass, and on anything else wrong: revise "
        "a severity, status, title, result, remedy or citation that is wrong, overstated, "
        "understated or unclear; add each finding the sheets show that the rules missed; "
        "withdraw each finding the sheets or the user's request show is wrong. Make every "
        "correction you can support with text printed on the sheets, now — do not leave for "
        "a later pass what you can fix in this one. Ask for re-reads only for facts you "
        "cannot see in the sheets' text."),
    VERIFY: (
        "PASS {n} OF {m} — VERIFY. The edits made so far are listed under earlier_passes and "
        "marked on the findings. Correct any edit that went too far or not far enough, and "
        "fix anything still wrong. This is the last pass."),
}


def mode_for(number: int, limit: int) -> str:
    if number >= 3:
        return VERIFY
    if number == 2 or limit == 1:
        return EDIT
    return CHECK


# ── the trace ────────────────────────────────────────────────────────────────
@dataclass
class PassRecord:
    """One check, the edits it made, and the re-read it led to."""
    number: int                                     # 1-based
    mode: str = CHECK
    review: Optional[ResultReview] = None
    error: str = ""
    #: Edits from this pass that were not applied, and why: [{"edit": i, "why": ...}].
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    applied: int = 0
    focus: Dict[int, str] = field(default_factory=dict)       # 0-based page -> text appended
    reread: Optional[Readings] = None
    usage: Dict[str, int] = field(default_factory=dict)
    #: Grounded AI values in the store after the re-read's rules run, when there was one.
    accepted_after: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "number": self.number, "mode": self.mode,
            "review": self.review.model_dump() if self.review else None,
            "error": self.error, "rejected": list(self.rejected), "applied": self.applied,
            "focus": {str(p): t for p, t in sorted(self.focus.items())},
            "reread": self.reread.to_json() if self.reread else None,
            "usage": dict(self.usage),
            "accepted_after": self.accepted_after,
        }

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "PassRecord":
        return cls(
            number=int(d.get("number", 0)), mode=d.get("mode", CHECK),
            review=ResultReview.model_validate(d["review"]) if d.get("review") else None,
            error=str(d.get("error") or ""),
            rejected=list(d.get("rejected") or []), applied=int(d.get("applied") or 0),
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
    passes: int = 0
    rule_runs: int = 1
    outcome: str = ""
    #: The final state's labels: one per AI-touched finding, keyed by `finding_identity`.
    revisions: List[Dict[str, Any]] = field(default_factory=list)
    withdrawn: List[Dict[str, Any]] = field(default_factory=list)

    def usage(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.records:
            for part in (r.usage, r.reread.usage if r.reread else {}):
                for k, v in part.items():
                    out[k] = out.get(k, 0) + int(v)
        return out

    def revision_map(self) -> Dict[Tuple, Dict[str, Any]]:
        """`finding_identity` -> the label `findings.json` carries as `ai_revision`."""
        return {tuple(r["finding"]): {k: v for k, v in r.items() if k != "finding"}
                for r in self.revisions}

    def summary(self) -> Dict[str, Any]:
        """JSON-safe and content-free — counts, never notes, reasons or sheet text."""
        return {
            "model": self.model, "prompt_version": self.prompt_version,
            "passes": self.passes, "max_passes": self.max_passes,
            "rule_runs": self.rule_runs,
            "reviews": sum(1 for r in self.records if r.review is not None),
            "outcome": self.outcome,
            "sheets_reread": sum(len(r.focus) for r in self.records),
            "edits_applied": sum(r.applied for r in self.records),
            "edits_rejected": sum(len(r.rejected) for r in self.records),
            "findings_revised": sum(1 for r in self.revisions if r.get("op") == "revise"),
            "findings_added": sum(1 for r in self.revisions if r.get("op") == "add"),
            "findings_withdrawn": len(self.withdrawn),
            "notes": sum(len(r.review.notes) for r in self.records if r.review),
            "usage": self.usage(),
        }

    def to_json(self) -> Dict[str, Any]:
        return {"file_sha256": self.file_sha256, "model": self.model,
                "prompt_version": self.prompt_version, "max_passes": self.max_passes,
                "reader": self.reader, "passes": self.passes, "rule_runs": self.rule_runs,
                "outcome": self.outcome, "revisions": self.revisions,
                "withdrawn": self.withdrawn,
                "records": [r.to_json() for r in self.records]}

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "ReviewTrace":
        return cls(file_sha256=d.get("file_sha256", ""), model=d.get("model", ""),
                   prompt_version=d.get("prompt_version", ""),
                   max_passes=int(d.get("max_passes", MAX_PASSES)),
                   reader=d.get("reader", ""),
                   records=[PassRecord.from_json(r) for r in d.get("records") or []],
                   passes=int(d.get("passes", 0)), rule_runs=int(d.get("rule_runs", 1)),
                   outcome=d.get("outcome", ""),
                   revisions=list(d.get("revisions") or []),
                   withdrawn=list(d.get("withdrawn") or []))


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


# ── applying edits ───────────────────────────────────────────────────────────
def _clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


class Sheets:
    """The set's pages, laid out on demand, for checking an edit's quote."""

    def __init__(self, pdf_path: Optional[str]) -> None:
        self.pdf_path = pdf_path
        self._layouts: Dict[int, Any] = {}
        self.page_count = 0
        if pdf_path:
            doc = pymupdf.open(pdf_path)
            self.page_count = doc.page_count
            doc.close()

    def find(self, page: int, quote: str) -> Optional[Tuple[float, float, float, float]]:
        """The box the quote is printed in on `page` (0-based), or None."""
        if not self.pdf_path or not quote.strip() or not 0 <= page < self.page_count:
            return None
        if page not in self._layouts:
            doc = pymupdf.open(self.pdf_path)
            try:
                self._layouts[page] = page_layout(doc[page])
            finally:
                doc.close()
        lay = self._layouts[page]
        hit = locate(quote, lay.words, lay.width, lay.height)
        return tuple(hit.box) if hit is not None else None


@dataclass
class State:
    """The result as it stands after the edits: what the reviewer sees and returns."""
    result: Any
    keys: List[str]
    #: Per finding, the label of what the AI review did to it, or None.
    labels: List[Optional[Dict[str, Any]]]
    withdrawn: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Tuple[int, str]] = field(default_factory=list)   # (edit index, why)


def _status_severity(e: FindingEdit, status: str, severity: str) -> Tuple[str, str, str]:
    """(status, severity, why-not) after an edit; why-not is "" when consistent."""
    s, v = e.status or "", e.severity or ""
    new_s, new_v = s or status, v or severity
    if s == "PASS":
        if v in LADDER:
            return status, severity, "a passing check cannot carry a problem severity"
        if new_v not in PASSED:
            new_v = "VERIFIED"
    elif s == "OPEN":
        if v in PASSED:
            return status, severity, "an open finding needs a problem severity"
        if new_v not in LADDER:
            return status, severity, "an open finding needs a severity: give one"
    elif v in LADDER and status not in ("OPEN", "CONFLICT"):
        new_s = "OPEN"
    elif v in PASSED and status in ("OPEN", "CONFLICT"):
        new_s = "PASS"
    return new_s, new_v, ""


def apply_edits(base, edits: List[Tuple[int, FindingEdit]], sheets: Sheets,
                sheet_codes: List[str]) -> State:
    """The rules' result with the accepted edits applied in order, each labelled.

    `edits` are (pass number, edit). An edit that fails validation is skipped
    and listed in `State.rejected` by its index in `edits`; the rest still apply.
    """
    findings = list(base.findings)
    keys = list(_keys(findings))
    labels: List[Optional[Dict[str, Any]]] = [None] * len(findings)
    abstentions = list(base.abstentions)
    withdrawn: List[Dict[str, Any]] = []
    rejected: List[Tuple[int, str]] = []
    added = 0

    def reject(i: int, why: str) -> None:
        rejected.append((i, why))

    for i, (number, e) in enumerate(edits):
        reason = _clip(e.reason.strip(), 400)
        if not reason:
            reject(i, "an edit needs a reason")
            continue
        quote = _clip(e.quote.strip(), 300)
        if e.op == "add":
            page = e.page - 1
            if not (e.title.strip() and e.result.strip()):
                reject(i, "a new finding needs a title and a result")
                continue
            status, severity, why = _status_severity(e, "", "")
            if why or not status:
                reject(i, why or "a new finding needs a severity or a status")
                continue
            box = sheets.find(page, quote)
            if box is None:
                reject(i, "the quote was not found on that page")
                continue
            added += 1
            fid = f"{AI_FID}{added:02d}"
            findings.append(_new_finding(fid, e, page, box, status, severity, sheet_codes))
            keys.append(fid)
            labels.append({"op": "add", "pass": number, "reasons": [reason],
                           "changed": [], "was": {}, "quote": quote, "page": page + 1})
            continue

        if e.key not in keys:
            reject(i, f"no finding has the key {e.key!r}")
            continue
        at = keys.index(e.key)
        f = findings[at]
        page = e.page - 1 if e.page else f.page
        if e.op == "withdraw":
            if sheets.find(page, quote) is None:
                reject(i, "withdrawing a finding needs a quote printed on the sheet")
                continue
            withdrawn.append({"finding": list(finding_identity(f)), "key": e.key,
                              "fid": f.fid, "title": f.title, "pass": number,
                              "reason": reason, "quote": quote, "page": page + 1})
            abstentions.append(Abstention(
                f.rule_id, "withdrawn by the AI result review", page=f.page,
                detail=f"{f.fid} — {f.title}: {reason}"))
            del findings[at], keys[at], labels[at]
            continue

        # revise
        status, severity, why = _status_severity(e, f.status, f.severity)
        if why:
            reject(i, why)
            continue
        moves = (status, severity) != (f.status, f.severity)
        box = sheets.find(page, quote) if quote else None
        if moves and box is None:
            reject(i, "changing a severity or status needs a quote printed on the sheet")
            continue
        if quote and box is None:
            reject(i, "the quote was not found on that page")
            continue
        changes = {"status": status, "severity": severity}
        for name, value, limit in (("title", e.title, 200), ("result", e.result, 1500),
                                   ("action", e.remedy, 800), ("code", e.code, 160)):
            if value.strip():
                changes[name] = _clip(value.strip(), limit)
        changed = [k for k, v in changes.items() if getattr(f, k) != v]
        if not changed:
            reject(i, "the edit changes nothing")
            continue
        label = labels[at] or {"op": "revise", "pass": number, "reasons": [], "changed": [],
                               "was": {}, "quote": "", "page": None}
        for k in changed:
            if k not in label["changed"]:
                label["changed"].append(k)
            if k in ("status", "severity") and k not in label["was"] and label["op"] == "revise":
                label["was"][k] = getattr(f, k)
        label["reasons"].append(reason)
        label["pass"] = number
        if quote:
            label["quote"], label["page"] = quote, page + 1
        findings[at] = replace(f, **changes)
        labels[at] = label

    # Provenance on the finding itself, so the marked-up PDF says it too.
    for at, label in enumerate(labels):
        if label is None:
            continue
        verb = "Raised" if label["op"] == "add" else "Revised"
        tag = f" [{verb} by AI review, pass {label['pass']}: {' '.join(label['reasons'])}]"
        findings[at] = replace(findings[at], result=findings[at].result + tag)

    result = replace(base, findings=findings, abstentions=abstentions)
    return State(result, keys, labels, withdrawn, rejected)


def _new_finding(fid: str, e: FindingEdit, page: int, box, status: str, severity: str,
                 sheet_codes: List[str]):
    from ..rules import Finding
    sheet = sheet_codes[page] if 0 <= page < len(sheet_codes) else f"p{page + 1}"
    return Finding(
        fid=fid, rule_id=AI_RULE, status=status, severity=severity,
        discipline="AI review", page=page, sheet=sheet, anchor=_clip(e.quote.strip(), 300),
        title=_clip(e.title.strip(), 200),
        checked="Raised by the AI result review from the sheet text quoted, not by a rule "
                "in the hand-verified corpus.",
        result=_clip(e.result.strip(), 1500), code=_clip(e.code.strip(), 160),
        action=_clip(e.remedy.strip(), 800), box=box)


def labels_for_trace(state: State) -> List[Dict[str, Any]]:
    out = []
    for f, label in zip(state.result.findings, state.labels):
        if label is not None:
            out.append({"finding": list(finding_identity(f)), "op": label["op"],
                        "pass": label["pass"], "reason": " ".join(label["reasons"]),
                        "changed": list(label["changed"]), "was": dict(label["was"]),
                        "quote": label["quote"], "page": label["page"]})
    return out


# ── what the reviewer is shown ───────────────────────────────────────────────
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


def packet(pdf_path: str, facts, state: State, options=None, declaration=None,
           number: int = 1, max_passes: int = MAX_PASSES,
           history: Optional[List[PassRecord]] = None) -> List[Dict[str, Any]]:
    """The reviewer's user message: this pass's job, the request, the result, the sheets."""
    request: Dict[str, Any] = {}
    if options is not None:
        request["review_options"] = {k: v for k, v in options.to_dict().items()
                                     if k != "email_to"}
    if declaration is not None and not declaration.is_empty():
        request["declaration"] = {k: v for k, v in declaration.to_dict().items()
                                  if v not in (None, "", [])}
    sheets = [{"page": s.index + 1, "sheet": s.code, "title": s.title,
               "discipline": s.discipline} for s in facts.sheets]
    findings = []
    for f, key, label in zip(state.result.findings, state.keys, state.labels):
        row = {"key": key, "id": f.fid, "rule": f.rule_id, "status": f.status,
               "severity": f.severity, "sheet": f.sheet, "page": f.page + 1, "title": f.title,
               "result": _clip(f.result, 600), "remedy": _clip(f.action, 300), "code": f.code,
               "basis": f.basis}
        if label is not None:
            row["ai_review"] = {"op": label["op"], "pass": label["pass"],
                                "changed": label["changed"], "was": label["was"]}
        findings.append(row)
    abstentions = [{"rule": a.rule_id, "reason": a.reason, "detail": a.detail or ""}
                   for a in state.result.abstentions]
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
    earlier = []
    for h in history or []:
        if h.review is None:
            continue
        earlier.append({
            "pass": h.number, "mode": h.mode, "notes": h.review.notes,
            "edits": [{"op": e.op, "key": e.key, "reason": e.reason}
                      for e in h.review.edits[:MAX_EDITS]],
            "edits_not_applied": h.rejected,
            "reread": [{"page": p + 1} for p in sorted(h.focus)],
            "reread_fields": sorted({k for rq in h.review.rereads for k in rq.fields}),
            "ai_values_used_after": h.accepted_after,
        })
    body = {
        "pass": number, "max_passes": max_passes,
        "what_the_user_asked_for": request,
        "sheets": sheets,
        "findings": findings,
        "abstentions": abstentions,
        "facts_the_rules_used": resolved,
        "ai_values_rejected_by_the_sheet_check": rejected,
        "earlier_passes": earlier,
    }
    texts = _sheet_texts(pdf_path)
    sheet_text = "\n\n".join(
        f"=== page {i + 1} · {facts.sheets[i].code if i < len(facts.sheets) else ''} ===\n"
        f"{t or '(no live text on this sheet — its content may be an image)'}"
        for i, t in enumerate(texts))
    job = MODES[mode_for(number, max_passes)].format(n=number, m=max_passes)
    return [
        {"type": "text", "text": job},
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
    """What a state says, to tell whether a pass changed anything."""
    return (tuple(sorted((f.fid, f.rule_id, f.status, f.severity, f.sheet, f.page, f.title,
                          f.result, f.action, f.code) for f in result.findings)),
            tuple(sorted((a.rule_id, a.reason, a.detail or "") for a in result.abstentions)))


# ── the loop ─────────────────────────────────────────────────────────────────
RunPass = Callable[[Readings], Tuple[Any, Any]]
Check = Callable[[Any, State, int, List[PassRecord]], Tuple[ResultReview, Dict[str, int]]]
Reread = Callable[[Dict[int, str]], Readings]


def review_loop(first: Tuple[Any, Any], readings: Readings, run_pass: RunPass,
                check: Optional[Check], reread: Optional[Reread], trace: ReviewTrace,
                replay: Optional[ReviewTrace] = None, pdf_path: Optional[str] = None
                ) -> Tuple[Any, Any, Readings]:
    """Check, correct, re-read and re-run — at most three passes.

    `first` is the (facts, result) the rules already produced from `readings`.
    `check` and `reread` are the two calls that reach the model; with `replay`
    they are never called and the recorded answers are used instead. `pdf_path`
    is the file the quotes in edits are checked against; without it, an edit
    that needs a quote is rejected. Fills `trace` and returns the final
    (facts, result, readings), with the edits applied and labelled.
    """
    facts, base = first
    limit = max(1, min(trace.max_passes, MAX_PASSES))
    sheets = Sheets(pdf_path)
    codes = [s.code for s in facts.sheets]
    accepted: List[Tuple[int, FindingEdit]] = []
    state = apply_edits(base, accepted, sheets, codes)
    tried: Set[Tuple[int, str]] = set()
    trace.passes, trace.rule_runs = 0, 1

    for number in range(1, limit + 1):
        recorded = None
        if replay is not None:
            if number > len(replay.records):
                trace.outcome = replay.outcome
                break
            recorded = replay.records[number - 1]

        rec = PassRecord(number, mode=mode_for(number, limit))
        trace.records.append(rec)
        trace.passes = number
        if recorded is not None:
            rec.review, rec.error, rec.usage = recorded.review, recorded.error, dict(recorded.usage)
        else:
            try:
                rec.review, rec.usage = check(facts, state, number, trace.records[:-1])
            except Exception as exc:                     # noqa: BLE001 — the last state stands
                rec.error = type(exc).__name__
        if rec.review is None:
            trace.outcome = FAILED
            break

        before = fingerprint(state.result)
        proposed = [(number, e) for e in rec.review.edits[:MAX_EDITS]]
        trial = apply_edits(base, accepted + proposed, sheets, codes)
        bad = {i for i, _ in trial.rejected}
        rec.rejected = [{"edit": i - len(accepted), "why": why}
                        for i, why in trial.rejected if i >= len(accepted)]
        kept = [e for i, e in enumerate(proposed, start=len(accepted)) if i not in bad]
        rec.applied = len(kept)
        accepted += kept
        state = apply_edits(base, accepted, sheets, codes)

        if rec.review.meets_request:
            trace.outcome = MET
            break
        if number == limit:
            trace.outcome = LIMIT
            break

        rec.focus = plan_rereads(rec.review, len(facts.sheets), tried)
        if rec.focus:
            for page in rec.focus:
                for rq in rec.review.rereads:
                    if rq.page - 1 == page:
                        tried.update((page, k) for k in rq.fields)
            if recorded is not None:
                rec.reread = recorded.reread
            else:
                try:
                    rec.reread = reread(rec.focus)
                except Exception as exc:                 # noqa: BLE001 — the last state stands
                    rec.error = type(exc).__name__
            if rec.reread is None:
                trace.outcome = REREAD_FAILED
                break
            readings = merge_readings(readings, rec.reread)
            facts, base = run_pass(readings)
            trace.rule_runs += 1
            rec.accepted_after = (readings.grounded or {}).get("accepted")
            state = apply_edits(base, accepted, sheets, codes)

        # The first pass hands its notes on to the edit pass even when it changed
        # nothing itself; after that, a pass that changes nothing ends the loop.
        if number >= 2 and fingerprint(state.result) == before:
            trace.outcome = NO_CHANGE
            break

    trace.revisions = labels_for_trace(state)
    trace.withdrawn = list(state.withdrawn)
    return facts, state.result, readings


def catalog_keys() -> List[str]:
    return [f.key for f in FIELDS]
