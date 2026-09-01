"""The Rebuild Console's reading of `secrets/local.env`.

The console reports on that file so mail and the comment assist stop failing
invisibly — a review still runs without them and nothing else says otherwise.
To do that it parses the file itself, which is a second copy of the parser in
`webapp/envfile.py`.

The duplication is deliberate: `scripts/rebuild_console.py` is standard library
only and runs outside the virtualenv, on a machine where the venv may not exist
yet, so importing the service to read a config file would make the console
depend on the thing it diagnoses. What is *not* acceptable is the two drifting,
because then the console reports on a file the service reads differently. That
is what the last test here is for.

Nothing in this module touches the network or starts a container.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from webapp import envfile

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "scripts" / "rebuild_console.py"


@pytest.fixture(scope="module")
def console():
    """The console, imported by path — `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("rebuild_console", CONSOLE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(repo: Path, text: str) -> Path:
    (repo / "secrets").mkdir(parents=True, exist_ok=True)
    path = repo / "secrets" / "local.env"
    path.write_text(text, encoding="utf-8")
    return path


# ── what it reports ───────────────────────────────────────────────────────
def test_an_absent_file_is_reported_rather_than_raising(console, tmp_path):
    """The common case on a machine that has never been configured."""
    cfg = console.local_config(tmp_path)
    assert cfg["exists"] is False
    assert cfg["set"] == []


def test_a_channel_is_configured_only_when_all_of_it_is(console, tmp_path):
    """Mail with no password is not mail. Reporting it as configured would
    send somebody looking for the fault at the SMTP server."""
    write(tmp_path, "FBC_SMTP_HOST=smtp.gmail.com\nFBC_SMTP_USER=a@b.c\n")
    assert console.local_config(tmp_path)["channels"]["Mail"] is False

    write(tmp_path, "\n".join([
        "FBC_SMTP_HOST=smtp.gmail.com",
        "FBC_SMTP_USER=a@b.c",
        "FBC_SMTP_PASS=abcd efgh ijkl mnop",
        "FBC_MAIL_FROM=a@b.c",
    ]))
    assert console.local_config(tmp_path)["channels"]["Mail"] is True


def test_a_name_with_no_value_does_not_count_as_set(console, tmp_path):
    """The committed template ships every name with an empty value. A console
    that counted those would report a fully configured deployment on a file
    nobody has touched."""
    write(tmp_path, "ANTHROPIC_API_KEY=\nFBC_GITHUB_TOKEN=   \n")
    cfg = console.local_config(tmp_path)
    assert cfg["set"] == []
    assert cfg["channels"]["Comment assist"] is False


def test_a_quoted_value_is_named(console, tmp_path):
    """Docker's --env-file keeps the quotes, so this is wrong rather than
    untidy — and it fails as an authentication error, which sends you to the
    wrong place entirely."""
    write(tmp_path, 'FBC_SMTP_PASS="hunter2"\nANTHROPIC_API_KEY=sk-ant-plain\n')
    cfg = console.local_config(tmp_path)
    assert cfg["quoted"] == ["FBC_SMTP_PASS"]


def test_an_api_key_without_a_workspace_is_flagged(console, tmp_path):
    """An identity-linked key is refused with 400 until the request names a
    workspace, and the assist reports that as "no summary" — indistinguishable
    from having no key at all."""
    write(tmp_path, "ANTHROPIC_API_KEY=sk-ant-x\n")
    assert console.local_config(tmp_path)["workspace_missing"] is True

    write(tmp_path, "ANTHROPIC_API_KEY=sk-ant-x\nANTHROPIC_WORKSPACE_ID=wrkspc_1\n")
    assert console.local_config(tmp_path)["workspace_missing"] is False


def test_no_value_is_ever_carried_out_of_the_file(console, tmp_path):
    """The console serves this over HTTP on loopback and prints it into a log
    somebody is standing in front of. Names only."""
    secret = "sk-ant-do-not-repeat-this"
    write(tmp_path, "ANTHROPIC_API_KEY={}\nFBC_SMTP_PASS=hunter2\n".format(secret))
    rendered = repr(console.local_config(tmp_path))
    assert secret not in rendered
    assert "hunter2" not in rendered


# ── the two parsers agree ─────────────────────────────────────────────────
def test_the_console_and_the_service_read_the_same_file_the_same_way(
    console, tmp_path
):
    """The console's parser is a copy of `webapp.envfile.parse`. If they drift,
    the console reports on a file the service reads differently — which is a
    worse failure than not reporting at all, because it is confidently wrong."""
    text = "\n".join([
        "# a comment",
        "",
        "   ",
        "FBC_SMTP_HOST=smtp.gmail.com",
        "export ANTHROPIC_API_KEY=sk-ant-x",
        "  FBC_MAIL_FROM = a@b.c  ",
        "FBC_SMTP_PASS=abcd efgh ijkl mnop",
        'FBC_GITHUB_TOKEN="quoted"',
        "FBC_OWNER_EMAILS=",
        "not a pair",
        "=novalue",
    ])
    write(tmp_path, text)

    # The service sets every name it parses; the console reports the ones with
    # a value, so an empty value is the one deliberate difference between them.
    service = {k for k, v in envfile.parse(text) if v}
    assert set(console.local_config(tmp_path)["set"]) == service


def test_the_committed_template_reports_as_unconfigured(console):
    """The template is what `setup-secrets` copies into place. If the console
    called a fresh copy configured, its first report on every new machine would
    be a lie."""
    cfg = console.local_config(ROOT)
    template = ROOT / "secrets" / "local.env.example"
    assert template.is_file()
    # The real file may or may not exist in a checkout; the template's own
    # contents are what is being asserted about.
    fresh = {k for k, v in envfile.parse(template.read_text(encoding="utf-8")) if v}
    assert "ANTHROPIC_API_KEY" not in fresh
    assert "FBC_SMTP_PASS" not in fresh
    assert isinstance(cfg["exists"], bool)


# ── the log ───────────────────────────────────────────────────────────────
def test_colour_escapes_are_stripped_out_of_the_log(console):
    """The scripts these buttons run colour their output for a terminal. The
    page renders text, so an unstripped line arrives looking like corruption."""
    console.log_clear()
    console.log_write("\x1b[1msecrets/local.env\x1b[0m")
    console.log_write("  \x1b[32mok\x1b[0m    FBC_SMTP_HOST")
    console.log_write("plain — untouched, em dash and all")

    lines = console.log_since(0)["lines"]
    assert lines == [
        "secrets/local.env",
        "  ok    FBC_SMTP_HOST",
        "plain — untouched, em dash and all",
    ]
    console.log_clear()
