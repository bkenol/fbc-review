"""The versioning scheme, and the two invariants that keep it honest.

`webapp/version.py` states the rule; `.github/workflows/deploy.yml` reimplements
it in shell because a workflow cannot import Python before checking out and
installing anything. These tests are what stop the two drifting apart, and what
stops the committed `VERSION` file from saying something the scheme cannot
parse.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from webapp import version

ROOT = Path(__file__).resolve().parent.parent


# ── the committed file ────────────────────────────────────────────────────
def test_version_file_exists_and_parses():
    """A missing or malformed VERSION degrades to UNKNOWN at runtime rather than
    crashing the service, so nothing else would notice it. This does."""
    assert (ROOT / "VERSION").exists(), "VERSION is the source of truth and must be committed"
    assert version.base() != version.UNKNOWN, (
        "VERSION is missing or does not hold MAJOR.MINOR.PATCH with an optional "
        "-alpha or -beta channel"
    )
    release, channel = version.parse(version.base())
    assert re.fullmatch(r"\d+\.\d+\.\d+", release)
    assert channel in {"alpha", "beta", "prod"}


def test_version_file_is_one_line_and_carries_no_build_number():
    """The build number is stamped on per build. A committed one is a number
    that has stopped incrementing."""
    lines = [l.strip() for l in (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()]
    meaningful = [l for l in lines if l and not l.startswith("#")]
    assert len(meaningful) == 1, f"VERSION should hold exactly one version, got {meaningful}"
    assert "+" not in meaningful[0], "build metadata is stamped on, never committed"


@pytest.mark.parametrize(
    "text,release,channel",
    [
        ("1.0.0-alpha", "1.0.0", "alpha"),
        ("1.0.0-beta", "1.0.0", "beta"),
        ("1.0.0", "1.0.0", "prod"),
        ("12.3.45-alpha", "12.3.45", "alpha"),
        ("  1.0.0-beta  ", "1.0.0", "beta"),
    ],
)
def test_parse_splits_release_from_channel(text, release, channel):
    assert version.parse(text) == (release, channel)


@pytest.mark.parametrize(
    "text",
    ["", "1.0", "1.0.0.0", "1.0.0-rc", "1.0.0-alpha.1", "1.0.0+build.1", "v1.0.0", "banana"],
)
def test_parse_rejects_anything_else(text):
    with pytest.raises(version.InvalidVersion):
        version.parse(text)


# ── the stamping rule ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "base,build,commit,expected",
    [
        # A channel takes the build number as a further dotted identifier, which
        # is what makes builds sort.
        ("1.0.0-alpha", "412", "3f1c9ab", "1.0.0-alpha.412+3f1c9ab"),
        ("1.0.0-beta", "412", "3f1c9ab", "1.0.0-beta.412+3f1c9ab"),
        # A release has no pre-release part to extend, and `1.0.0.412` would not
        # be SemVer, so the build number goes into the metadata.
        ("1.0.0", "412", "3f1c9ab", "1.0.0+build.412.3f1c9ab"),
        # Outside CI there is no build number; the slot carries the tree.
        ("1.0.0-alpha", None, "local.3f1c9ab", "1.0.0-alpha+local.3f1c9ab"),
        ("1.0.0-alpha", None, "local.3f1c9ab.dirty", "1.0.0-alpha+local.3f1c9ab.dirty"),
        ("1.0.0-alpha", None, None, "1.0.0-alpha"),
        ("1.0.0", None, None, "1.0.0"),
    ],
)
def test_stamp(base, build, commit, expected):
    assert version.stamp(base, build=build, commit=commit) == expected


def test_builds_sort_the_way_the_release_train_runs():
    """SemVer 2.0.0 precedence, and the reason the build number is a numeric
    pre-release identifier rather than part of a string: `alpha.10` must outrank
    `alpha.9`, which a plain string comparison gets backwards."""
    ordered = [
        version.stamp("1.0.0-alpha", build="9"),
        version.stamp("1.0.0-alpha", build="10"),
        version.stamp("1.0.0-beta", build="1"),
        version.stamp("1.0.0"),
    ]
    assert ordered == ["1.0.0-alpha.9", "1.0.0-alpha.10", "1.0.0-beta.1", "1.0.0"]
    # The trap this scheme avoids, stated so nobody "simplifies" it away.
    assert "1.0.0-alpha.10" < "1.0.0-alpha.9"
    assert _precedence(ordered) == sorted(_precedence(ordered))


def _precedence(versions):
    """Sort keys under SemVer 2.0.0 precedence, for the assertion above.

    Three rules, which are the three the scheme leans on: build metadata after
    `+` is ignored; a release outranks any pre-release of the same triple; and a
    numeric pre-release identifier compares as a number, not as text.
    """
    keys = []
    for text in versions:
        without_metadata = text.partition("+")[0]
        core, _, pre = without_metadata.partition("-")
        triple = tuple(int(part) for part in core.split("."))
        if not pre:
            keys.append((triple, 1, ()))
            continue
        identifiers = tuple(
            (0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split(".")
        )
        keys.append((triple, 0, identifiers))
    return keys


# ── what each consumer publishes ──────────────────────────────────────────
def test_release_strips_the_channel():
    assert version.release() == version.parse(version.base())[0]
    assert "-" not in version.release()


def test_openapi_publishes_the_release_and_nothing_per_build(client):
    """The committed schema is regenerated in CI and compared byte-for-byte, so
    `info.version` must not move with a build number, a commit or a machine."""
    from webapp.server import app

    assert app.version == version.release()
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == version.release()


def test_healthz_reports_the_full_build(anon_client):
    body = anon_client.get("/healthz").json()
    assert body["version"] == version.resolve()
    # Whatever the environment, it must at least name the release it came from.
    assert body["version"].startswith(version.release())


def test_the_api_alias_answers_identically(anon_client):
    """Firebase Hosting rewrites /api/** to Cloud Run and everything else to
    index.html, so /api/healthz is the only path the browser can reach."""
    direct = anon_client.get("/healthz")
    alias = anon_client.get("/api/healthz")
    assert alias.status_code == 200
    assert alias.json() == direct.json()


def test_the_alias_stays_off_the_published_schema(client):
    """One alias, not a second endpoint: publishing it would put a duplicate
    method on the generated client."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/healthz" in paths
    assert "/api/healthz" not in paths


def test_fbc_version_wins_outright(monkeypatch):
    """How CI hands the running service the number it stamped."""
    monkeypatch.setenv("FBC_VERSION", "9.9.9-beta.7+deadbee")
    version.resolve.cache_clear()
    try:
        assert version.resolve() == "9.9.9-beta.7+deadbee"
    finally:
        version.resolve.cache_clear()


def test_module_entry_point_is_what_the_scripts_call():
    """scripts/share.ps1 stamps the container by running this, so it has to work
    with nothing but the standard library on the path."""
    for args, expected in (
        ([], version.resolve()),
        (["--release"], version.release()),
        (["--channel"], version.channel()),
        (["--base"], version.base()),
    ):
        out = subprocess.run(
            [sys.executable, "-m", "webapp.version", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == expected


# ── the workflow reimplements this rule in shell ──────────────────────────
def test_the_workflow_stamps_the_same_way():
    """`.github/workflows/deploy.yml` cannot import Python before it has checked
    out and installed anything, so it applies the rule in shell. This runs that
    shell against this module and fails if they have drifted."""
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "run_number" in workflow, "the build number must still come from the workflow run"

    script = _extract_stamp_script(workflow)
    for base, build, sha in [
        ("1.0.0-alpha", "412", "3f1c9abcdef"),
        ("1.0.0-beta", "7", "0000001abcdef"),
        ("2.10.3", "1", "abcdef1234567"),
    ]:
        out = _run_stamp(script, base, build, sha)
        expected = version.stamp(base, build=build, commit=sha[:7])
        assert out["version"] == expected, f"{base}: shell says {out['version']}, Python says {expected}"
        assert out["release"] == version.parse(base)[0]
        assert out["channel"] == version.parse(base)[1]
        # A Docker tag may not contain '+'.
        assert "+" not in out["image_tag"]
        assert out["image_tag"] == expected.replace("+", "_")


def test_the_workflow_rejects_a_malformed_version_file():
    script = _extract_stamp_script((ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8"))
    for bad in ["1.0.0-rc", "1.0", "banana", "1.0.0+build.1"]:
        completed = _run_stamp(script, bad, "1", "abcdef1234567", check=False)
        assert completed.returncode != 0, f"{bad!r} should have failed the workflow's check"


def _extract_stamp_script(workflow: str) -> str:
    """Lift the `run:` block of the version job's stamp step out of the YAML.

    Deliberately literal rather than a YAML parse: what is tested has to be the
    same characters the runner executes.
    """
    marker = "      - id: stamp\n"
    assert marker in workflow, "the version job no longer has a step called `stamp`"
    after = workflow.split(marker, 1)[1]
    body = after.split("        run: |\n", 1)[1]

    lines = []
    for line in body.splitlines():
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line[10:])
    script = "\n".join(lines)
    assert "GITHUB_OUTPUT" in script, "extracted the wrong block; the stamp step writes outputs"
    return script


def _run_stamp(script: str, base: str, build: str, sha: str, *, check: bool = True):
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "VERSION").write_text(base + "\n", encoding="utf-8")
        outputs = Path(tmp, "outputs")
        outputs.touch()
        summary = Path(tmp, "summary")
        summary.touch()
        completed = subprocess.run(
            ["bash", "-c", script],
            cwd=tmp,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "BUILD": build,
                "SHA": sha,
                "GITHUB_OUTPUT": str(outputs),
                "GITHUB_STEP_SUMMARY": str(summary),
            },
        )
        if not check:
            return completed
        assert completed.returncode == 0, completed.stderr
        return dict(
            line.split("=", 1)
            for line in outputs.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
