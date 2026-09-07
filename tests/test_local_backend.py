"""The filesystem backend, running with real authentication.

Cloud Storage is the one part of this stack that requires an open billing
account. Until this split, choosing the filesystem stand-ins also turned
authentication off, because one flag meant both — so a deployment that could not
have a bucket could not have sign-in either. These tests hold the two apart.
"""
from __future__ import annotations

import io
import json

import pytest

from conftest import make_pdf
from webapp import storage_urls
from webapp.config import BACKEND_GCP, BACKEND_LOCAL, settings


# ══ which backend gets chosen ═════════════════════════════════════════════
@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, BACKEND_GCP),
        # Back-compat: the dev flag used to select both, and still implies the
        # filesystem when nothing else is said.
        ({"FBC_DEV_UNSAFE_AUTH": "1"}, BACKEND_LOCAL),
        ({"FBC_BACKEND": "local"}, BACKEND_LOCAL),
        # The point of the split: filesystem stores, authentication left on.
        ({"FBC_BACKEND": "local", "FBC_DEV_UNSAFE_AUTH": "0"}, BACKEND_LOCAL),
        # And the reverse — no bucket is not implied by no sign-in.
        ({"FBC_DEV_UNSAFE_AUTH": "1", "FBC_BACKEND": "gcp"}, BACKEND_GCP),
        ({"FBC_BACKEND": "nonsense"}, BACKEND_GCP),
    ],
)
def test_the_backend_is_chosen_independently_of_the_auth_bypass(
    monkeypatch, env, expected
):
    for key in ("FBC_DEV_UNSAFE_AUTH", "FBC_BACKEND", "K_SERVICE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings.cache_clear()
    assert settings().backend == expected
    settings.cache_clear()


def test_the_filesystem_backend_cannot_be_selected_on_cloud_run(monkeypatch):
    """Cloud Run replaces instances freely and its disk is ephemeral.

    The local backend there would lose every job and artefact without saying
    anything, so the refusal is the same shape as the one on the auth bypass —
    and failing over to the real stores is the recoverable direction.
    """
    monkeypatch.setenv("FBC_BACKEND", "local")
    monkeypatch.setenv("K_SERVICE", "fbc-review")
    settings.cache_clear()
    assert settings().backend == BACKEND_GCP
    assert settings().local_backend is False
    settings.cache_clear()


def test_local_storage_still_demands_a_verified_identity(monkeypatch):
    """The whole point of the split, stated as an assertion."""
    monkeypatch.setenv("FBC_BACKEND", "local")
    monkeypatch.delenv("FBC_DEV_UNSAFE_AUTH", raising=False)
    settings.cache_clear()
    assert settings().local_backend is True
    assert settings().dev_unsafe_auth is False
    settings.cache_clear()


# ══ signed artefact urls ══════════════════════════════════════════════════
@pytest.fixture
def signing(monkeypatch):
    monkeypatch.setenv("FBC_ARTEFACT_SECRET", "a-test-signing-key")
    settings.cache_clear()
    yield
    settings.cache_clear()


def parts(url: str) -> dict:
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    return {k: v[0] for k, v in query.items()}


def test_a_signed_url_round_trips(signing):
    url = storage_urls.build("outputs/job-1/markup.pdf", download_as="Set.pdf")
    assert url.startswith("/api/artefacts/outputs/job-1/markup.pdf?")
    q = parts(url)
    assert storage_urls.verify("outputs/job-1/markup.pdf", q["expires"], q["sig"], q["name"])


def test_a_signature_does_not_carry_to_another_blob(signing):
    """Otherwise one download link would open every artefact on the service."""
    q = parts(storage_urls.build("outputs/mine/findings.json"))
    assert not storage_urls.verify("outputs/theirs/findings.json", q["expires"], q["sig"])


def test_a_tampered_or_absent_signature_is_refused(signing):
    q = parts(storage_urls.build("outputs/job-1/findings.json"))
    assert not storage_urls.verify("outputs/job-1/findings.json", q["expires"], "beef")
    assert not storage_urls.verify("outputs/job-1/findings.json", q["expires"], "")


def test_an_expired_link_is_refused_even_though_it_is_correctly_signed(signing):
    blob = "outputs/job-1/markup.pdf"
    stale = storage_urls.sign(blob, 1)
    assert not storage_urls.verify(blob, "1", stale)
    # A malformed expiry is refused rather than raising.
    assert not storage_urls.verify(blob, "not-a-number", stale)


def test_the_signing_string_cannot_be_made_ambiguous(signing):
    """A separator alone would let two different pairs render identically."""
    assert storage_urls.sign("a/b", 100, "c") != storage_urls.sign("a", 100, "b/c")


def test_an_unset_secret_generates_one_rather_than_using_a_default(monkeypatch):
    """A signing key committed to a repository is a published private key."""
    monkeypatch.delenv("FBC_ARTEFACT_SECRET", raising=False)
    settings.cache_clear()
    storage_urls._generated_secret.cache_clear()
    first = storage_urls._generated_secret()
    assert len(first) >= 32
    assert storage_urls._generated_secret() is first, "must be stable within a process"
    settings.cache_clear()


# ══ the route ═════════════════════════════════════════════════════════════
@pytest.fixture
def local_client(monkeypatch, tmp_path, user):
    """A client on the filesystem backend with authentication left on.

    Deliberately does not override `job_store` or `file_store`: the selectors
    are what is under test, so they have to run for real.
    """
    from fastapi.testclient import TestClient

    from webapp import server

    settings.cache_clear()
    monkeypatch.setenv("FBC_BACKEND", "local")
    monkeypatch.setenv("FBC_BUCKET", str(tmp_path))
    monkeypatch.setenv("FBC_ARTEFACT_SECRET", "a-test-signing-key")
    monkeypatch.delenv("FBC_DEV_UNSAFE_AUTH", raising=False)
    settings.cache_clear()

    for cached in (server._dev_jobs, server._dev_files, server._dev_feedback):
        cached.cache_clear()

    server.app.dependency_overrides[server.current_user] = lambda: user
    with TestClient(server.app) as c:
        yield c

    server.app.dependency_overrides.clear()
    for cached in (server._dev_jobs, server._dev_files, server._dev_feedback):
        cached.cache_clear()
    settings.cache_clear()


def test_the_service_reports_that_sign_in_is_still_required(local_client):
    body = local_client.get("/healthz").json()
    assert body["auth_required"] is True


def test_an_artefact_is_served_to_a_correctly_signed_link(local_client, tmp_path):
    from webapp import server

    blob = "outputs/job-1/findings.json"
    payload = json.dumps({"findings": []}).encode()
    (tmp_path / "blobs" / "outputs" / "job-1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "blobs" / blob).write_bytes(payload)

    url = server._dev_files().signed_url(blob)
    response = local_client.get(url)
    assert response.status_code == 200, response.text
    assert response.content == payload
    assert response.headers["content-type"].startswith("application/json")


def test_an_unsigned_request_for_an_artefact_is_refused(local_client, tmp_path):
    blob = "outputs/job-1/findings.json"
    (tmp_path / "blobs" / "outputs" / "job-1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "blobs" / blob).write_bytes(b"{}")

    # Signed-in and still refused: the signature is the authorisation here, and
    # a session alone does not entitle the holder to every blob on the service.
    assert local_client.get(f"/api/artefacts/{blob}").status_code == 403
    assert local_client.get(f"/api/artefacts/{blob}?expires=9999999999&sig=x").status_code == 403


def test_a_signed_link_cannot_walk_out_of_the_blob_root(local_client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"not yours")

    escape = "../secret.txt"
    q = {"expires": 9999999999}
    q["sig"] = storage_urls.sign(escape, q["expires"])
    response = local_client.get(f"/api/artefacts/{escape}", params=q)
    assert response.status_code in (403, 404)
    assert b"not yours" not in response.content


def test_the_old_unauthenticated_dev_route_is_gone(local_client):
    """It served any blob under the root to anyone who asked."""
    assert local_client.get("/_dev/blob/outputs/job-1/findings.json").status_code == 404


def test_a_finished_review_hands_back_signed_links(local_client, tmp_path):
    from webapp import server, storage

    job_id = "job-signed"
    files = server._dev_files()
    for name, body in ((storage.MARKUP, b"%PDF-1.7\n"), (storage.FINDINGS, b"{}")):
        path = tmp_path / "blobs" / storage.output_path(job_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    server._dev_jobs().create(
        job_id=job_id, uid="uid-alice", email="allowed@example.com",
        filename="set.pdf", size_bytes=9, pages=2, options={},
        upload_blob=storage.upload_path(job_id, "set.pdf"), stages=["a", "b"],
    )
    server._dev_jobs().mark_done(job_id, {
        "sheets": 2, "pages": 2, "cad_layers": 0, "annotations": 0, "marked": 0,
        "counts": {}, "open": 0, "verified": 0, "conflicts": 0, "abstentions": [],
        "rules_run": 0, "scale_pages": 0, "pdf_bytes": 9,
        "pdf_name": "Set — CODE REVIEW.pdf", "findings_count": 0,
    })

    downloads = local_client.get(f"/api/jobs/{job_id}").json()["downloads"]
    assert downloads["markup_pdf"].startswith("/api/artefacts/")
    assert "sig=" in downloads["markup_pdf"]

    # And the link actually works, end to end, as a plain navigation would.
    fetched = local_client.get(downloads["markup_pdf"])
    assert fetched.status_code == 200
    assert fetched.content == b"%PDF-1.7\n"


def test_the_artefact_route_is_absent_on_the_gcp_backend(client):
    """There the browser fetches from Cloud Storage and never asks us."""
    response = client.get("/api/artefacts/outputs/job-1/findings.json")
    assert response.status_code == 404


# ══ the two stores stay the same shape ════════════════════════════════════
def test_both_job_stores_accept_the_same_create_arguments():
    """`webapp.devbackend.LocalJobStore` mirrors `webapp.jobs.JobStore`, and the
    mirroring is by hand — there is no shared base class, deliberately, because
    the Firestore one has no business importing a filesystem stand-in.

    That makes drift silent and expensive. A field added to the Firestore store
    and not to this one is a `TypeError` raised by the *filesystem* deployment
    only, which is the one nobody runs the suite against — this is exactly how
    `rerun_of` shipped broken to a local instance while every unit test passed
    against a double that had been updated.
    """
    import inspect

    from webapp.devbackend import LocalJobStore
    from webapp.jobs import JobStore

    def names(cls):
        return set(inspect.signature(cls.create).parameters) - {"self"}

    assert names(LocalJobStore) == names(JobStore)
