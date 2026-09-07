"""Getting escalated feedback in front of the owner, and turning it into work.

Four channels, because they answer different questions and none of them
subsumes the others:

* **The queue** (`/api/admin/feedback`) is the record. Everything lands there.
* **Mail** is the interrupt. Only escalations send immediately; everything else
  waits for the digest, because a notification per confirmation is a
  notification nobody reads.
* **A feature prompt** is the action item. Escalated feedback exported as
  Markdown in the same shape as `docs/FEATURE-PROMPT-*.md`, ready to paste into
  Claude Code — which is how work actually starts in this repository.
* **A GitHub issue** is the tracked version of the same thing, for feedback
  that needs to outlive one session.

Every channel is inert unless configured and says so, the way
`webapp.mailer.status()` does. A deployment with no SMTP and no GitHub token
still collects feedback and still triages it; the owner just reads the queue.

Nothing here raises into a caller. Feedback has already been accepted and the
user has already been told so — a failed notification must not become a failed
submission.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from webapp import mailer
from webapp.config import settings
from webapp.triage import (AUTO_TUNABLE, DISPOSITION_LABELS, ESCALATE,
                           NEEDS_COMPONENT)

log = logging.getLogger("fbc.notify")

GITHUB_API = "https://api.github.com"

#: Which dispositions interrupt. `auto_tunable` does not: it is a one-click
#: approval sitting in a queue, and mailing about it would train the owner to
#: ignore the mail that matters.
URGENT = (ESCALATE, NEEDS_COMPONENT)


def github_configured() -> bool:
    cfg = settings()
    return bool(cfg.github_token and cfg.github_repo)


def github_status() -> str:
    cfg = settings()
    if github_configured():
        return f"issues to {cfg.github_repo}"
    return ("not configured — set FBC_GITHUB_REPO and FBC_GITHUB_TOKEN to open "
            "issues from escalated feedback")


# ── rendering ─────────────────────────────────────────────────────────────
def _finding_lines(record: Dict[str, Any]) -> List[str]:
    finding = record.get("finding") or {}
    if finding:
        return [
            f"- **Rule** `{finding.get('rule_id', '')}`",
            f"- **Severity** {finding.get('severity', '')}",
            f"- **Sheet** {finding.get('sheet', '')} (page {finding.get('page', '')})",
            f"- **Cites** {finding.get('code', '') or '—'}",
            f"- **Title** {finding.get('title', '')}",
            f"- **Result** {finding.get('result', '')}",
        ]

    # An abstention is the opposite shape: there is no finding precisely because
    # the rule declined to produce one, and what the owner needs is the reason
    # it gave and how this build classified that reason.
    abstention = record.get("abstention") or {}
    if abstention:
        from webapp import abstentions as abstention_kinds

        kind = abstention_kinds.kind(str(abstention.get("kind") or "unknown"))
        lines = [
            f"- **Rule** `{abstention.get('rule', '')}` — *declined to run*",
            f"- **Reason it gave** {abstention.get('reason', '')}",
        ]
        if abstention.get("detail"):
            lines.append(f"- **Detail** {abstention['detail']}")
        lines += [
            f"- **Classified as** {kind.label} — {kind.help}",
            f"- **Standing guidance** {kind.guidance}",
        ]
        return lines

    sweep = record.get("sweep") or {}
    if sweep:
        markups = sweep.get("markups") or []
        sheets = sweep.get("sheets") or []
        return [
            f"- **A marked-up pass** over `{sweep.get('filename', '')}`",
            f"- **{len(markups)} annotation{'' if len(markups) == 1 else 's'}** "
            f"on {len(sheets)} sheet{'' if len(sheets) == 1 else 's'}, "
            f"{sweep.get('commented', 0)} with a written comment",
            "- **Kinds** " + (
                ", ".join(f"{v}× {k}" for k, v in sorted((sweep.get("counts") or {}).items()))
                or "—"
            ),
        ]

    return ["_No finding — this is a coverage report about something the "
            "review did not produce._"]


def _answer_lines(record: Dict[str, Any]) -> List[str]:
    from webapp import feedback_schema

    answers = record.get("answers") or {}
    if not answers:
        return ["- _No structured answers._"]
    out = []
    for aspect_key, verdict_key in sorted(answers.items()):
        verdict = feedback_schema.lookup(aspect_key, verdict_key)
        label = verdict.label if verdict else verdict_key
        mark = "✓" if verdict and verdict.polarity == feedback_schema.GOOD else "✗"
        out.append(f"- {mark} **{aspect_key}** — {label}")
    return out


def _what(record: Dict[str, Any]) -> str:
    """What this piece of feedback is about, in one token.

    Used for digest rows and issue titles, where a row that says only
    "coverage" for four different kinds of submission is a row nobody can
    triage from the list.
    """
    finding = record.get("finding") or {}
    if finding.get("rule_id"):
        return str(finding["rule_id"])
    if record.get("subject") == "abstention" and record.get("rule_id"):
        return f"{record['rule_id']} (stood down)"
    if record.get("subject") == "sweep":
        sweep = record.get("sweep") or {}
        count = len(sweep.get("markups") or [])
        return f"markup pass, {count} annotation{'' if count == 1 else 's'}"
    return str(record.get("sheet") or "coverage")


def summarise(record: Dict[str, Any]) -> str:
    """One line for a digest row or an issue title."""
    return (
        f"[{record.get('disposition', '')}] {_what(record)} — "
        f"{record.get('rationale', '')[:90]}"
    )


def _where(markup: Dict[str, Any]) -> str:
    """A stored markup's geometry, in words somebody can go and look at.

    The key is `geometry`, and it is a dict — `{x0, y0, x1, y1, points}` in PDF
    user space. This read used to be `markup["rect"] or markup["points"]`, and a
    stored markup has never carried either of those, so every escalation this
    feature has ever sent said "no geometry" whatever the reviewer drew. A
    report that cannot say where on the sheet it is about cannot be acted on,
    which is the whole point of asking somebody to draw the box.

    "no geometry" is still the honest answer for two cases and only two: a
    `note`, which is defined as a pin carrying none, and a record written before
    this was fixed. A degenerate rectangle at the origin is not rendered as a
    location, because the origin is a place on the sheet and the markup was not
    there.
    """
    geometry = markup.get("geometry") or {}
    if not isinstance(geometry, dict):
        return "no geometry"

    points = geometry.get("points") or []
    if points:
        first, last = points[0], points[-1]
        return (f"{len(points)} points from {_pt(first)} to {_pt(last)}"
                if len(points) > 1 else f"a single point at {_pt(first)}")

    try:
        x0, y0 = float(geometry.get("x0", 0)), float(geometry.get("y0", 0))
        x1, y1 = float(geometry.get("x1", 0)), float(geometry.get("y1", 0))
    except (TypeError, ValueError):
        return "no geometry"
    if x0 == x1 and y0 == y1:
        return "no geometry"
    return f"({_n(x0)}, {_n(y0)}) to ({_n(x1)}, {_n(y1)})"


def _n(value: float) -> str:
    """A coordinate, without a trailing `.0` on a whole number of points."""
    return f"{value:g}"


def _pt(point: Any) -> str:
    try:
        return f"({_n(float(point[0]))}, {_n(float(point[1]))})"
    except (TypeError, ValueError, IndexError):
        return "(?, ?)"


def feature_prompt(record: Dict[str, Any], job: Optional[Dict[str, Any]] = None) -> str:
    """Escalated feedback as a runnable prompt, in this repo's house style.

    Deliberately the same frontmatter and shape as `docs/FEATURE-PROMPT-*.md`:
    the owner's workflow is to open Claude Code and paste one of these, and a
    format that matches what is already in `docs/` costs nothing and fits.
    """
    job = job or {}
    triage = record.get("triage") or {}
    disposition = record.get("disposition", "")
    created = record.get("created_at")
    created_text = created.date().isoformat() if hasattr(created, "date") else ""

    lines: List[str] = [
        "---",
        f"title: 'Feedback {record.get('id', '')} — {DISPOSITION_LABELS.get(disposition, disposition)}'",
        "type: runbook",
        "tags:",
        "  - code-review",
        "  - florida-building-code",
        "  - training-feedback",
        f"  - disposition/{disposition}",
        "status: draft",
        f"created: {created_text}",
        "---",
        "",
        f"# Feedback {record.get('id', '')} — {DISPOSITION_LABELS.get(disposition, disposition)}",
        "",
        "`CLAUDE.md` carries the standing rules and outranks anything here that "
        "contradicts it. In particular: the review path makes zero model calls, "
        "and `tests/` is not to be edited to make a change pass.",
        "",
        "## What was reported",
        "",
        f"A reviewer working in training mode on **{job.get('filename', 'a permit set')}** "
        f"reported the following.",
        "",
        *_finding_lines(record),
        "",
        "### Structured verdicts",
        "",
        *_answer_lines(record),
        "",
    ]

    comment = (record.get("comment") or "").strip()
    if comment:
        lines += [
            "### What they wrote",
            "",
            "> " + comment.replace("\n", "\n> "),
            "",
        ]

    markup = record.get("markup") or {}
    if markup:
        lines += [
            "### Where on the sheet",
            "",
            f"- **{markup.get('kind', 'markup')}** on page {markup.get('page', '?')} "
            f"at {_where(markup)} (PDF user space, origin top-left)",
            "",
        ]

    # The pass itself, verbatim. A sweep's value is the sentences somebody wrote
    # next to specific places on specific sheets, and summarising it away would
    # leave a prompt that says a pass happened without saying what it found.
    sweep = record.get("sweep") or {}
    if sweep.get("text"):
        lines += [
            "### The marked-up pass, as handed over",
            "",
            "```",
            str(sweep["text"]),
            "```",
            "",
        ]

    lines += [
        "## How the triage routed it, and why",
        "",
        f"**{DISPOSITION_LABELS.get(disposition, disposition)}** — {record.get('rationale', '')}",
        "",
        f"Signals: `{'`, `'.join(triage.get('signals') or []) or 'none'}`",
        "",
    ]

    assist = triage.get("assist")
    if assist:
        lines += [
            "The comment assist read this as: "
            f"_{assist.get('summary', '')}_ (confidence {assist.get('confidence', '')}). "
            "Advisory only — it can raise an escalation and never lower one.",
            "",
        ]

    if disposition == NEEDS_COMPONENT:
        lines += [
            "## What to build",
            "",
            "This is engineering work, not configuration. The calibration overlay in "
            "`webapp/calibration.py` runs downstream of the corpus over a finished "
            "findings list, so it can re-level, re-order, scope and suppress — and it "
            "cannot re-read a table, re-resolve a scale, move a marker or invent a "
            "finding. That is why this arrived here rather than as a knob.",
            "",
            "Likely homes for the change:",
            "",
            "- `fbcreview/extract/` — if a value was read wrongly or not at all.",
            "- `fbcreview/rules/` — if a check does not exist or does not fire.",
            "- `fbcreview/render/markup.py` — if the marker landed in the wrong place.",
            "",
            "`fbcreview/` is behind an ownership boundary in `CLAUDE.md`: if the change "
            "genuinely belongs in there, stop and report it rather than editing it.",
            "",
            "Add a regression test that fails before the change and passes after, and "
            "state the before-and-after counts.",
            "",
        ]
    elif disposition == ESCALATE:
        lines += [
            "## What to decide",
            "",
            "This needs judgement rather than code. Nothing has been applied.",
            "",
            "If the dispute is about a **code citation**, the change belongs in "
            "`fbcreview/codes/fbc2023.py` — the corpus is data, and thresholds never "
            "live in a rule. Confirm the section text against the published code "
            "before changing anything: a wrong citation in an advisory plan review is "
            "a liability, which is exactly why the triage refuses to apply citation "
            "feedback automatically however many people agree with it.",
            "",
        ]
    elif disposition == AUTO_TUNABLE:
        changes = triage.get("changes") or []
        lines += [
            "## The proposed calibration",
            "",
            "This can be approved in the admin console without any code change. "
            "It is recorded here for the audit trail.",
            "",
            "```json",
            json.dumps(changes, indent=2, default=str),
            "```",
            "",
        ]

    lines += [
        "## Before you finish",
        "",
        "- `pytest tests/ -v` is the regression gate. Run it before committing.",
        "- One commit per phase, naming what changed and why.",
        f"- Mark feedback `{record.get('id', '')}` as actioned in the admin console.",
        "",
    ]
    return "\n".join(lines)


def _mail_body(record: Dict[str, Any], job: Optional[Dict[str, Any]]) -> str:
    job = job or {}
    finding = record.get("finding") or {}
    parts = [
        f"Disposition: {DISPOSITION_LABELS.get(record.get('disposition', ''), '')}",
        f"Why: {record.get('rationale', '')}",
        "",
        f"Review: {job.get('filename', '')} ({record.get('job_id', '')})",
        f"From: {record.get('email', '')}",
    ]
    if finding:
        parts += [
            f"Finding: {finding.get('rule_id', '')} {finding.get('severity', '')} "
            f"on {finding.get('sheet', '')} — {finding.get('title', '')}",
        ]
    abstention = record.get("abstention") or {}
    if abstention:
        parts += [
            f"Stood down: {abstention.get('rule', '')} — {abstention.get('reason', '')}",
        ]
    sweep = record.get("sweep") or {}
    if sweep:
        parts += [
            f"Markup pass: {len(sweep.get('markups') or [])} annotations on "
            f"{len(sweep.get('sheets') or [])} sheets",
        ]
    comment = (record.get("comment") or "").strip()
    if comment:
        parts += ["", "They wrote:", comment]
    parts += [
        "",
        "Open the queue to approve, reject, or export this as a prompt:",
        "  /admin  →  feedback  →  " + str(record.get("id", "")),
    ]
    return "\n".join(parts)


# ── channels ──────────────────────────────────────────────────────────────
def notify_owner(record: Dict[str, Any], job: Optional[Dict[str, Any]] = None) -> str:
    """Mail an escalation. Returns a human-readable result; never raises."""
    cfg = settings()
    recipients = sorted(cfg.owner_emails)
    if not recipients:
        return "no owner addresses configured"
    if record.get("disposition") not in URGENT:
        return "not urgent — will appear in the digest"

    subject = (
        f"[FBC feedback] {DISPOSITION_LABELS.get(record.get('disposition', ''), '')}"
        f" — {_what(record)}"
    )
    try:
        return mailer.send_review(recipients, subject, _mail_body(record, job))
    except Exception as exc:
        log.warning("owner notification failed", extra={"error": type(exc).__name__})
        return f"failed: {type(exc).__name__}"


def digest(records: List[Dict[str, Any]]) -> str:
    """One mail for everything waiting. Called by the digest route."""
    cfg = settings()
    recipients = sorted(cfg.owner_emails)
    if not recipients:
        return "no owner addresses configured"
    if not records:
        return "nothing waiting"

    by_disposition: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        by_disposition.setdefault(record.get("disposition", ""), []).append(record)

    lines = [f"{len(records)} pieces of feedback are waiting.", ""]
    for disposition, group in sorted(by_disposition.items()):
        lines.append(f"{DISPOSITION_LABELS.get(disposition, disposition)} ({len(group)})")
        for record in group[:20]:
            lines.append(f"  · {summarise(record)}")
        if len(group) > 20:
            lines.append(f"  … and {len(group) - 20} more")
        lines.append("")
    lines.append("Open /admin to work through them.")

    return mailer.send_review(recipients, "[FBC feedback] digest", "\n".join(lines))


def create_issue(record: Dict[str, Any], job: Optional[Dict[str, Any]] = None) -> str:
    """Open a GitHub issue for one piece of feedback. Returns its URL, or "".

    Uses `urllib` rather than adding an HTTP client to the runtime image: this
    is one POST, on a path almost nobody takes, and the container is deliberately
    thin.
    """
    cfg = settings()
    if not github_configured():
        return ""

    disposition = record.get("disposition", "")
    title = (
        f"[feedback] {_what(record)} — "
        f"{DISPOSITION_LABELS.get(disposition, disposition)}"
    )
    payload = json.dumps({
        "title": title[:250],
        "body": feature_prompt(record, job),
        "labels": ["training-feedback", f"disposition/{disposition}"],
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{GITHUB_API}/repos/{cfg.github_repo}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "fbc-review",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        url = str(body.get("html_url") or "")
        log.info("issue opened", extra={"feedback_id": record.get("id"), "url": url})
        return url
    except urllib.error.HTTPError as exc:
        # Never the token, and never the body — a GitHub error can echo request
        # content back.
        log.warning("could not open issue", extra={"status": exc.code})
    except Exception as exc:
        log.warning("could not open issue", extra={"error": type(exc).__name__})
    return ""
