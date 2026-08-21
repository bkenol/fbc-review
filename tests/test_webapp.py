"""Coverage for the hardening added in phases 1-3A.

tests/test_regression.py is the engine's gate and is left untouched. This file
covers the service around it: admission control, the error envelope, auth, the
allowlist, ownership and the rate limits.
"""
from __future__ import annotations

import io
import json

import pytest

from conftest import make_encrypted_pdf, make_pdf
from webapp.config import settings


def post_review(client, pdf: bytes, options: str = "{}", name: str = "set.pdf"):
    return client.post(
        "/api/review",
        files={"file": (name, io.BytesIO(pdf), "application/pdf")},
        data={"review_options": options},
    )


def envelope(response):
    """Assert the typed error shape and return the inner object."""
    body = response.json()
    assert set(body) == {"error"}, f"not the error envelope: {body}"
    assert set(body["error"]) == {"code", "message"}, body
    assert body["error"]["code"] and body["error"]["message"]
    assert "Traceback" not in json.dumps(body)
    return body["error"]


# ── health ────────────────────────────────────────────────────────────────
def test_healthz_needs_no_auth_and_touches_nothing(anon_client):
    r = anon_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "fbc-review"
    assert body["version"]


# ── authentication ────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,path", [("get", "/api/config"), ("get", "/api/jobs/abc")])
def test_api_requires_authentication(anon_client, method, path):
    r = getattr(anon_client, method)(path)
    assert r.status_code == 401
    assert envelope(r)["code"] == "unauthenticated"


def test_anonymous_review_is_rejected_before_any_work(anon_client):
    r = post_review(anon_client, make_pdf())
    assert r.status_code == 401
    assert envelope(r)["code"] == "unauthenticated"


def test_malformed_authorization_header_is_rejected(anon_client):
    r = anon_client.get("/api/config", headers={"Authorization": "Basic abc123"})
    assert r.status_code == 401
    assert envelope(r)["code"] == "unauthenticated"


def test_allowlist_rejects_verified_but_unlisted_address(monkeypatch):
    """A genuine Google account that is not on the list gets 403 with prose
    that tells the person what to do, not a generic error."""
    from webapp import auth
    from webapp.errors import ApiError

    settings.cache_clear()
    monkeypatch.setenv("FBC_ALLOWED_EMAILS", "allowed@example.com")
    settings.cache_clear()

    with pytest.raises(ApiError) as excinfo:
        auth._authorise(uid="uid-mallory", email="stranger@example.com", email_verified=True)
    assert excinfo.value.status == 403
    assert excinfo.value.code == "forbidden"
    assert "administrator" in excinfo.value.message.lower()
    settings.cache_clear()


def test_allowlist_rejects_unverified_email(monkeypatch):
    from webapp import auth
    from webapp.errors import ApiError

    settings.cache_clear()
    monkeypatch.setenv("FBC_ALLOWED_EMAILS", "allowed@example.com")
    settings.cache_clear()

    with pytest.raises(ApiError) as excinfo:
        auth._authorise(uid="uid-x", email="allowed@example.com", email_verified=False)
    assert excinfo.value.status == 403
    assert "not verified" in excinfo.value.message.lower()
    settings.cache_clear()


def test_allowlist_is_case_insensitive(monkeypatch):
    from webapp import auth

    settings.cache_clear()
    monkeypatch.setenv("FBC_ALLOWED_EMAILS", "Allowed@Example.com")
    settings.cache_clear()

    user = auth._authorise(uid="u", email="ALLOWED@example.COM", email_verified=True)
    assert user.email == "allowed@example.com"
    settings.cache_clear()


def test_dev_auth_bypass_cannot_activate_on_cloud_run(monkeypatch):
    """The escape hatch keys off K_SERVICE, which only Cloud Run sets."""
    settings.cache_clear()
    monkeypatch.setenv("FBC_DEV_UNSAFE_AUTH", "1")
    monkeypatch.setenv("K_SERVICE", "fbc-review")
    settings.cache_clear()
    assert settings().dev_unsafe_auth is False

    monkeypatch.delenv("K_SERVICE")
    settings.cache_clear()
    assert settings().dev_unsafe_auth is True
    settings.cache_clear()


# ── upload admission ──────────────────────────────────────────────────────
def test_non_pdf_is_rejected_with_a_typed_error(client):
    r = post_review(client, b"this is not a pdf at all", name="notes.txt")
    assert r.status_code == 415
    assert envelope(r)["code"] == "unsupported_media_type"


def test_html_masquerading_as_pdf_is_rejected_on_magic_bytes(client):
    r = post_review(client, b"<!doctype html><html></html>", name="evil.pdf")
    assert r.status_code == 415
    assert envelope(r)["code"] == "unsupported_media_type"


def test_empty_file_is_rejected(client):
    r = post_review(client, b"", name="empty.pdf")
    assert r.status_code in (400, 415)
    envelope(r)


def test_oversized_upload_is_rejected_while_streaming(client, monkeypatch):
    settings.cache_clear()
    monkeypatch.setenv("FBC_MAX_UPLOAD_MB", "1")
    settings.cache_clear()

    big = make_pdf(1) + b"\n%padding\n" + (b"x" * (2 * 1024 * 1024))
    r = post_review(client, big)
    assert r.status_code == 413
    assert envelope(r)["code"] == "payload_too_large"
    # Nothing was admitted: no job record, no blob.
    assert client.fake_store.docs == {}
    assert client.fake_files.blobs == {}
    settings.cache_clear()


def test_encrypted_pdf_is_rejected_with_prose_not_a_traceback(client):
    r = post_review(client, make_encrypted_pdf(), name="locked.pdf")
    assert r.status_code == 400
    err = envelope(r)
    assert err["code"] == "encrypted_pdf"
    assert "password" in err["message"].lower()


def test_corrupt_pdf_is_rejected_cleanly(client):
    r = post_review(client, b"%PDF-1.7\nnot really a pdf body\n%%EOF", name="broken.pdf")
    assert r.status_code == 400
    assert envelope(r)["code"] in ("corrupt_pdf", "encrypted_pdf")


def test_page_count_is_capped(client, monkeypatch):
    settings.cache_clear()
    monkeypatch.setenv("FBC_MAX_PAGES", "5")
    settings.cache_clear()

    r = post_review(client, make_pdf(pages=8))
    assert r.status_code == 413
    err = envelope(r)
    assert err["code"] == "too_many_pages"
    assert "8" in err["message"]
    settings.cache_clear()


def test_bad_options_json_is_rejected(client):
    r = post_review(client, make_pdf(), options="{not json")
    assert r.status_code == 400
    assert envelope(r)["code"] == "invalid_request"


def test_unavailable_edition_is_rejected_by_name(client):
    r = post_review(client, make_pdf(), options=json.dumps({"edition": "fbc2026"}))
    assert r.status_code == 400
    err = envelope(r)
    assert err["code"] == "invalid_request"
    assert "9th Edition" in err["message"]


def test_unknown_occupancy_group_is_rejected(client):
    r = post_review(client, make_pdf(), options=json.dumps({"occupancy_group": "Z-9"}))
    assert r.status_code == 400
    assert envelope(r)["code"] == "invalid_request"


def test_filename_path_traversal_is_stripped(client):
    r = post_review(client, make_pdf(), name="../../../etc/passwd.pdf")
    assert r.status_code == 202
    job_id = r.json()["id"]
    record = client.fake_store.get(job_id)
    assert record["filename"] == "passwd.pdf"
    assert ".." not in record["upload_blob"]


# ── ownership ─────────────────────────────────────────────────────────────
def test_another_session_cannot_read_a_job_by_guessing_its_id(client):
    from webapp import server
    from webapp.auth import User

    r = post_review(client, make_pdf())
    assert r.status_code == 202
    job_id = r.json()["id"]

    assert client.get(f"/api/jobs/{job_id}").status_code == 200

    server.app.dependency_overrides[server.current_user] = lambda: User(
        uid="uid-mallory", email="allowed@example.com"
    )
    stolen = client.get(f"/api/jobs/{job_id}")
    # Reported as absent, not forbidden, so ids cannot be probed for existence.
    assert stolen.status_code == 404
    assert envelope(stolen)["code"] == "not_found"


def test_unknown_job_is_a_typed_404(client):
    r = client.get("/api/jobs/deadbeefcafe")
    assert r.status_code == 404
    assert envelope(r)["code"] == "not_found"


# ── rate limits ───────────────────────────────────────────────────────────
def test_concurrent_review_limit(client, monkeypatch):
    settings.cache_clear()
    monkeypatch.setenv("FBC_RATE_CONCURRENT", "2")
    monkeypatch.setenv("FBC_RATE_PER_HOUR", "50")
    settings.cache_clear()

    store = client.fake_store
    for n in range(2):
        store.create(job_id=f"busy{n}", uid="uid-alice", email="allowed@example.com",
                     filename="x.pdf", size_bytes=1, pages=1, options={}, upload_blob="b",
                     stages=[])

    r = post_review(client, make_pdf())
    assert r.status_code == 429
    err = envelope(r)
    assert err["code"] == "rate_limited"
    assert "at a time" in err["message"]
    settings.cache_clear()


def test_hourly_review_limit(client, monkeypatch):
    settings.cache_clear()
    monkeypatch.setenv("FBC_RATE_PER_HOUR", "3")
    monkeypatch.setenv("FBC_RATE_CONCURRENT", "99")
    settings.cache_clear()

    store = client.fake_store
    for n in range(3):
        rec = store.create(job_id=f"old{n}", uid="uid-alice", email="allowed@example.com",
                           filename="x.pdf", size_bytes=1, pages=1, options={}, upload_blob="b",
                           stages=[])
        rec["state"] = "done"
        store.docs[f"old{n}"]["state"] = "done"

    r = post_review(client, make_pdf())
    assert r.status_code == 429
    assert "last hour" in envelope(r)["message"]
    settings.cache_clear()


def test_limits_are_per_user(client, monkeypatch):
    settings.cache_clear()
    monkeypatch.setenv("FBC_RATE_CONCURRENT", "1")
    settings.cache_clear()

    client.fake_store.create(job_id="theirs", uid="uid-bob", email="bob@example.com",
                             filename="x.pdf", size_bytes=1, pages=1, options={}, upload_blob="b",
                             stages=[])
    r = post_review(client, make_pdf())
    assert r.status_code == 202
    settings.cache_clear()


# ── accepted job shape ────────────────────────────────────────────────────
def test_accepted_review_creates_a_queued_job_and_stores_the_upload(client):
    opts = json.dumps({"occupancy_group": "B", "sprinklered": False, "min_severity": "HIGH"})
    r = post_review(client, make_pdf(pages=3), options=opts, name="Permit Set.pdf")
    assert r.status_code == 202

    job_id = r.json()["id"]
    record = client.fake_store.get(job_id)
    assert record["uid"] == "uid-alice"
    assert record["pages"] == 3
    assert record["options"]["occupancy_group"] == "B"
    assert record["options"]["sprinklered"] is False
    assert f"uploads/{job_id}/Permit Set.pdf" in client.fake_files.blobs


def test_job_response_is_fully_typed(client):
    r = post_review(client, make_pdf())
    job_id = r.json()["id"]

    body = client.get(f"/api/jobs/{job_id}").json()
    for field in ("id", "filename", "state", "stage", "stage_label", "stages",
                  "options", "bytes", "created_at", "elapsed_seconds"):
        assert field in body, f"missing {field}"
    assert body["stages"] and isinstance(body["stages"], list)
    assert body["stage_label"] in body["stages"]


def test_signed_urls_appear_only_when_the_job_is_done(client):
    r = post_review(client, make_pdf())
    job_id = r.json()["id"]

    assert client.get(f"/api/jobs/{job_id}").json()["downloads"] is None

    client.fake_store.mark_done(job_id, {
        "sheets": 1, "pages": 1, "cad_layers": 0, "annotations": 0, "marked": 0,
        "counts": {"HIGH": 1}, "open": 1, "verified": 0, "abstentions": [],
        "rules_run": 12, "scale_pages": 0, "pdf_bytes": 1234,
        "pdf_name": "Set — CODE REVIEW.pdf", "findings_count": 1,
    })

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["state"] == "done"
    dl = body["downloads"]
    assert dl["markup_pdf"].startswith("https://")
    assert "X-Goog-Signature" in dl["markup_pdf"]
    assert "findings.json" in dl["findings_json"]
    assert dl["expires_at"]


# ── config ────────────────────────────────────────────────────────────────
def test_config_is_typed_and_drives_the_form(client):
    body = client.get("/api/config").json()

    assert {g["id"] for g in body["occupancy_groups"]} >= {"A-2", "A-3", "B", "M", "E"}
    assert all(set(g) == {"id", "label"} for g in body["occupancy_groups"])

    editions = {e["id"]: e for e in body["editions"]}
    assert editions["fbc2023"]["available"] is True
    assert editions["fbc2026"]["available"] is False  # listed and disabled, not hidden

    assert body["severities"] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    assert len(body["stages"]) == 5
    assert len(body["rules"]) == 12
    assert body["mail"]["configured"] is False
    assert body["defaults"]["occupancy_group"] == "A-3"


# ── the contract itself ───────────────────────────────────────────────────
def test_openapi_has_no_untyped_response_bodies(client):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]

    for name in ("ErrorResponse", "ConfigResponse", "Job", "Summary", "Finding",
                 "ReviewOptions", "Downloads", "Health", "ReviewAccepted"):
        assert name in schemas, f"{name} missing from the generated schema"

    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            ok = op["responses"].get("200") or op["responses"].get("202")
            assert ok, f"{method} {path} declares no success response"
            content = ok.get("content", {})
            if "application/json" in content:
                assert content["application/json"]["schema"], f"{method} {path} untyped"


def test_every_failure_uses_the_same_envelope(client):
    spec = client.get("/openapi.json").json()
    ref = "#/components/schemas/ErrorResponse"
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            for status, decl in op["responses"].items():
                if status.startswith(("4", "5")) and "content" in decl:
                    schema = decl["content"].get("application/json", {}).get("schema", {})
                    assert schema.get("$ref") == ref, f"{method} {path} {status}: {schema}"


def test_no_permissive_cors_middleware_is_installed(client):
    """Production is same-origin behind the Hosting rewrite and dev goes
    through ng serve's proxy. A wildcard CORS header here would mean the proxy
    is misconfigured."""
    r = client.get("/healthz", headers={"Origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
