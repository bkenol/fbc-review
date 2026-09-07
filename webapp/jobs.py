"""Job records in Firestore, and the rate limits derived from them.

Replaces the in-process dict the laptop build used. Cloud Run scales to zero
and replaces instances freely, so nothing about a job may live in memory.

Rate limits are **derived by query**, never kept as a counter. A counter that
is incremented when a review starts and decremented when it finishes leaks a
slot permanently every time an instance is killed mid-review, and the user is
then locked out with no way to clear it.
"""
from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from webapp.config import settings

log = logging.getLogger("fbc.jobs")

QUEUED, RUNNING, DONE, ERROR = "queued", "running", "done", "error"
ACTIVE = (QUEUED, RUNNING)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class RateLimited(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def check_limits(active: int, recent: int) -> None:
    """The limit decision, as a plain function of two counts.

    Deliberately not a method: the local development store and the test double
    both need exactly this logic, and borrowing an unbound method onto an
    unrelated class works only until the method touches an attribute the
    borrower does not have. Taking the counts as arguments makes the tested
    unit unambiguous.
    """
    cfg = settings()

    if active >= cfg.rate_concurrent:
        raise RateLimited(
            f"You already have {active} reviews in progress. "
            f"Wait for one to finish — the limit is {cfg.rate_concurrent} at a time."
        )
    if recent >= cfg.rate_per_hour:
        raise RateLimited(
            f"You have run {recent} reviews in the last hour, which is the limit. "
            "Try again later."
        )


class JobStore:
    def __init__(self, client: Optional[firestore.Client] = None):
        cfg = settings()
        self._db = client or firestore.Client(project=cfg.project_id or None)
        self._col = self._db.collection(cfg.collection)

    # ── writes ────────────────────────────────────────────────────────────
    def create(
        self,
        *,
        job_id: str,
        uid: str,
        email: str,
        filename: str,
        size_bytes: int,
        pages: int,
        options: Dict[str, Any],
        upload_blob: str,
        # Required, not defaulted: mark_done derives the final stage index from
        # this list, and an empty one reports a finished job at stage 0.
        stages: List[str],
        source: Optional[Dict[str, Any]] = None,
        # The declaration is part of the audit trail: the register prints what
        # the applicant asserted, and support has to be able to read it back.
        declaration: Optional[Dict[str, Any]] = None,
        # The review this one re-runs, when it re-runs one. Part of the audit
        # trail for the same reason the declaration is: two reviews of one set
        # that say different things is a fact somebody will have to explain,
        # and the explanation is that the second was given more to work with.
        rerun_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = {
            "id": job_id,
            "uid": uid,
            "email": email,
            "filename": filename,
            "bytes": size_bytes,
            "pages": pages,
            "state": QUEUED,
            "stage": 0,
            "stages": stages,
            "options": options,
            "declaration": declaration,
            "upload_blob": upload_blob,
            "rerun_of": rerun_of,
            "source": source,
            "summary": None,
            "conversion": None,
            "error": None,
            "error_code": None,
            "created_at": utcnow(),
            "started_at": None,
            "finished_at": None,
        }
        self._col.document(job_id).set(record)
        return record

    def update(self, job_id: str, **fields: Any) -> None:
        self._col.document(job_id).update(fields)

    def mark_running(self, job_id: str) -> None:
        self.update(job_id, state=RUNNING, stage=0, started_at=utcnow())

    def mark_stage(self, job_id: str, stage: int) -> None:
        self.update(job_id, stage=stage)

    def mark_done(self, job_id: str, summary: Dict[str, Any]) -> None:
        # stage is not hard-coded: a job that rebuilt scanned sheets has one
        # more stage than one that did not.
        record = self.get(job_id) or {}
        final = max(len(record.get("stages") or []) - 1, 0)
        self.update(job_id, state=DONE, stage=final, summary=summary, finished_at=utcnow())

    def mark_error(self, job_id: str, code: str, message: str) -> None:
        self.update(
            job_id, state=ERROR, error=message, error_code=code, finished_at=utcnow()
        )

    # ── reads ─────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        snap = self._col.document(job_id).get()
        return snap.to_dict() if snap.exists else None

    def list_for(self, uid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """This user's reviews, newest first.

        Scoped to the caller's own uid at the query, not filtered afterwards:
        a history that fetches everything and then hides the rest is one
        refactor away from being a data leak.
        """
        q = (
            self._col.where(filter=FieldFilter("uid", "==", uid))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [snap.to_dict() for snap in q.stream()]

    # ── rate limiting ─────────────────────────────────────────────────────
    def _count(self, query) -> int:
        result = query.count().get()
        # Aggregation results come back as [[AggregationResult]].
        for row in result:
            for item in row if isinstance(row, list) else [row]:
                return int(item.value)
        return 0

    def active_count(self, uid: str) -> int:
        q = (
            self._col.where(filter=FieldFilter("uid", "==", uid))
            .where(filter=FieldFilter("state", "in", list(ACTIVE)))
        )
        return self._count(q)

    def recent_count(self, uid: str, since: dt.datetime) -> int:
        q = (
            self._col.where(filter=FieldFilter("uid", "==", uid))
            .where(filter=FieldFilter("created_at", ">", since))
        )
        return self._count(q)

    def enforce_limits(self, uid: str) -> None:
        window_start = utcnow() - dt.timedelta(hours=1)
        check_limits(self.active_count(uid), self.recent_count(uid, window_start))

    # ── startup housekeeping ──────────────────────────────────────────────
    def fail_stale_running(self) -> int:
        """Cloud Run scales to zero and kills instances mid-review. Any job
        still marked running from before this instance existed is orphaned:
        nothing is going to finish it, so say so rather than let the client
        poll forever."""
        cfg = settings()
        cutoff = utcnow() - dt.timedelta(minutes=cfg.stale_running_minutes)
        stale: List[Any] = list(
            self._col.where(filter=FieldFilter("state", "==", RUNNING))
            .where(filter=FieldFilter("started_at", "<", cutoff))
            .limit(100)
            .stream()
        )
        for snap in stale:
            self.mark_error(
                snap.id,
                "interrupted",
                "This review was interrupted by a server restart. Run it again.",
            )
        if stale:
            log.warning("failed orphaned jobs", extra={"count": len(stale)})
        return len(stale)


@lru_cache(maxsize=1)
def get_job_store() -> JobStore:
    return JobStore()
