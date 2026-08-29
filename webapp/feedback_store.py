"""Feedback, markups and calibration profiles in Firestore.

Shaped after `webapp.jobs`: one thin class over collections, no caching, and
every query scoped to the caller at the query rather than filtered afterwards.

## Three collections, and why not four

`feedback` holds one document per submission, carrying the triage verdict
inline.  There is deliberately **no separate proposals collection**: a proposal
is the calibration diff a triage produced, it belongs to exactly one submission,
and splitting it out would create two records that can disagree about what was
proposed.  The admin queue is a query — feedback whose disposition is
`auto_tunable` and whose state is still `new`.

`markups` holds the drawn annotations.  Separate from feedback because their
lifecycles differ: a person draws six boxes on a sheet while reading, then
writes one piece of feedback about two of them.  Markups without feedback are
notes to self and are kept; feedback without a markup is the ordinary case.

`calibration` holds profile versions.  Versions are immutable, `active` points
at the live one, and `candidate-{uid}` is one user's training sandbox.

## Profile versions are never edited

A finished review records the profile version it ran under.  If that version
could be edited afterwards, the audit trail would point at something that no
longer describes the review — so promotion writes a new `version-{n}` document
and repoints `active`, and nothing ever mutates a version in place.
"""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from functools import lru_cache
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from webapp.calibration import ACTIVE_ID, CANDIDATE, GLOBAL, CalibrationProfile
from webapp.config import settings

log = logging.getLogger("fbc.feedback")

# ── feedback lifecycle ────────────────────────────────────────────────────
#: Submitted, triaged, waiting for the owner.
NEW = "new"
#: The owner approved the proposal and it was promoted into a profile version.
ACCEPTED = "accepted"
#: The owner disagreed. Kept, not deleted — a rejected report is evidence too.
REJECTED = "rejected"
#: Read and turned into work outside this system (an issue, a prompt, a task).
ACTIONED = "actioned"

STATES = (NEW, ACCEPTED, REJECTED, ACTIONED)
OPEN_STATES = (NEW,)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def version_key(version: int) -> str:
    return f"version-{version:04d}"


def candidate_key(uid: str) -> str:
    return f"candidate-{uid}"


class FeedbackStore:
    def __init__(self, client: Optional[firestore.Client] = None):
        # Constructed lazily. `webapp.jobs.JobStore` can afford to open its
        # client eagerly because every request needs it; this one is only
        # reached by the training routes, and a deployment with
        # FBC_TRAINING_MODE unset must not need Firestore credentials for a
        # feature it does not run. Resolving the dependency has to stay free.
        self._client = client
        self._collections: Optional[Dict[str, Any]] = None

    def _collection(self, which: str):
        if self._collections is None:
            cfg = settings()
            db = self._client or firestore.Client(project=cfg.project_id or None)
            self._client = db
            self._collections = {
                "feedback": db.collection(cfg.feedback_collection),
                "markups": db.collection(cfg.markup_collection),
                "profiles": db.collection(cfg.calibration_collection),
            }
        return self._collections[which]

    @property
    def _feedback(self):
        return self._collection("feedback")

    @property
    def _markups(self):
        return self._collection("markups")

    @property
    def _profiles(self):
        return self._collection("profiles")

    # ── feedback ──────────────────────────────────────────────────────────
    def add_feedback(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(record)
        record.setdefault("id", new_id())
        record.setdefault("state", NEW)
        record.setdefault("created_at", utcnow())
        record.setdefault("decided_at", None)
        record.setdefault("decided_by", "")
        record.setdefault("decision_note", "")
        record.setdefault("issue_url", "")
        record.setdefault("notified", False)
        self._feedback.document(record["id"]).set(record)
        return record

    def get_feedback(self, feedback_id: str) -> Optional[Dict[str, Any]]:
        snap = self._feedback.document(feedback_id).get()
        return snap.to_dict() if snap.exists else None

    def update_feedback(self, feedback_id: str, **fields: Any) -> None:
        self._feedback.document(feedback_id).update(fields)

    def list_feedback_for_job(self, job_id: str, uid: str,
                             limit: int = 200) -> List[Dict[str, Any]]:
        """One person's own feedback on one review."""
        q = (
            self._feedback.where(filter=FieldFilter("job_id", "==", job_id))
            .where(filter=FieldFilter("uid", "==", uid))
            .limit(limit)
        )
        return _by_created(snap.to_dict() for snap in q.stream())

    def list_feedback(
        self,
        *,
        state: Optional[str] = None,
        disposition: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """The owner's queue. Not scoped to a uid — this is the admin view, and
        the route that calls it is the only one gated on ownership."""
        q = self._feedback
        if state:
            q = q.where(filter=FieldFilter("state", "==", state))
        if disposition:
            q = q.where(filter=FieldFilter("disposition", "==", disposition))
        q = q.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
        return [snap.to_dict() for snap in q.stream()]

    def count_open(self) -> int:
        q = self._feedback.where(filter=FieldFilter("state", "==", NEW))
        try:
            for row in q.count().get():
                for item in row if isinstance(row, list) else [row]:
                    return int(item.value)
        except Exception:
            log.warning("could not count open feedback")
        return 0

    # ── markups ───────────────────────────────────────────────────────────
    def add_markup(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(record)
        record.setdefault("id", new_id())
        record.setdefault("created_at", utcnow())
        self._markups.document(record["id"]).set(record)
        return record

    def get_markup(self, markup_id: str) -> Optional[Dict[str, Any]]:
        snap = self._markups.document(markup_id).get()
        return snap.to_dict() if snap.exists else None

    def update_markup(self, markup_id: str, **fields: Any) -> None:
        self._markups.document(markup_id).update(fields)

    def delete_markup(self, markup_id: str) -> None:
        self._markups.document(markup_id).delete()

    def list_markups(self, job_id: str, uid: str, limit: int = 500) -> List[Dict[str, Any]]:
        q = (
            self._markups.where(filter=FieldFilter("job_id", "==", job_id))
            .where(filter=FieldFilter("uid", "==", uid))
            .limit(limit)
        )
        return _by_created(snap.to_dict() for snap in q.stream())

    # ── calibration profiles ──────────────────────────────────────────────
    def active_profile(self) -> CalibrationProfile:
        snap = self._profiles.document(ACTIVE_ID).get()
        if not snap.exists:
            return CalibrationProfile()
        return CalibrationProfile.from_dict(snap.to_dict())

    def candidate_profile(self, uid: str) -> CalibrationProfile:
        """One user's training sandbox.

        Seeded from the active profile the first time it is asked for, so a
        training run starts from what production actually does rather than from
        nothing.
        """
        snap = self._profiles.document(candidate_key(uid)).get()
        if snap.exists:
            return CalibrationProfile.from_dict(snap.to_dict())
        base = self.active_profile()
        seeded = CalibrationProfile.from_dict(base.to_dict())
        seeded.profile_id = candidate_key(uid)
        seeded.scope = CANDIDATE
        seeded.owner_uid = uid
        seeded.label = "Training candidate"
        seeded.derived_from = base.version
        return seeded

    def save_profile(self, profile: CalibrationProfile) -> CalibrationProfile:
        self._profiles.document(profile.profile_id).set(profile.to_dict())
        return profile

    def promote(self, profile: CalibrationProfile, *, by: str,
                label: str = "", note: str = "") -> CalibrationProfile:
        """Make `profile` what production reviews against.

        Writes an immutable `version-{n}` document first, then repoints
        `active`. In that order: a crash between the two leaves a version
        nobody points at, which is recoverable, where the reverse would leave
        `active` naming a version that was never written.
        """
        promoted = CalibrationProfile.from_dict(profile.to_dict())
        promoted.profile_id = ACTIVE_ID
        promoted.scope = GLOBAL
        promoted.owner_uid = ""
        promoted.created_by = by
        promoted.created_at = utcnow()
        if label:
            promoted.label = label
        if note:
            promoted.note = note

        archived = CalibrationProfile.from_dict(promoted.to_dict())
        archived.profile_id = version_key(promoted.version)
        self._profiles.document(archived.profile_id).set(archived.to_dict())
        self._profiles.document(ACTIVE_ID).set(promoted.to_dict())
        log.info(
            "calibration promoted",
            extra={"version": promoted.version, "rules": len(promoted.touched()), "by": by},
        )
        return promoted

    def profile_versions(self, limit: int = 25) -> List[Dict[str, Any]]:
        q = (
            self._profiles.where(filter=FieldFilter("scope", "==", GLOBAL))
            .order_by("version", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        seen: Dict[int, Dict[str, Any]] = {}
        for snap in q.stream():
            data = snap.to_dict() or {}
            # `active` and its `version-{n}` twin are the same version; show one.
            if data.get("profile_id") != ACTIVE_ID:
                seen.setdefault(int(data.get("version") or 0), data)
        return [seen[v] for v in sorted(seen, reverse=True)]

    def profile_version(self, version: int) -> Optional[CalibrationProfile]:
        snap = self._profiles.document(version_key(version)).get()
        if not snap.exists:
            return None
        return CalibrationProfile.from_dict(snap.to_dict())


def _by_created(records) -> List[Dict[str, Any]]:
    """Oldest first, tolerating a missing timestamp.

    Sorted here rather than by Firestore: these queries already filter on two
    equality fields, and adding an order_by would need a third composite index
    for a list that is never more than a few hundred rows.
    """
    out = [r for r in records if r]
    out.sort(key=lambda r: r.get("created_at") or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc))
    return out


@lru_cache(maxsize=1)
def get_feedback_store() -> FeedbackStore:
    return FeedbackStore()
