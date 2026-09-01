"""`secrets/local.env`, and the rules that keep it from being a footgun.

The file exists so mail and the comment assist can be configured once rather
than exported per shell. What is tested here is the part that would bite: that
a checked-out file cannot override a real deployment's environment, and that
this parser and Docker's `--env-file` agree about what a value is — the same
file is read by both, and a parser that was cleverer than Docker's would mean a
password that authenticated locally and failed in the container.
"""
from __future__ import annotations

import os

import pytest

from webapp import config, envfile


@pytest.fixture
def env_file(tmp_path):
    """A path to write an env file at, with the environment restored after."""
    before = dict(os.environ)
    path = tmp_path / "local.env"
    yield path
    os.environ.clear()
    os.environ.update(before)


def test_reads_what_the_file_declares(env_file):
    env_file.write_text("FBC_TEST_ONE=alpha\nFBC_TEST_TWO=beta\n", encoding="utf-8")
    os.environ.pop("FBC_TEST_ONE", None)
    os.environ.pop("FBC_TEST_TWO", None)

    applied = envfile.load(env_file)

    assert set(applied) == {"FBC_TEST_ONE", "FBC_TEST_TWO"}
    assert os.environ["FBC_TEST_ONE"] == "alpha"


def test_a_variable_already_set_wins(env_file):
    """Cloud Run sets the real thing. A file in a checkout must not beat it."""
    env_file.write_text("FBC_TEST_ONE=from-the-file\n", encoding="utf-8")
    os.environ["FBC_TEST_ONE"] = "from-the-environment"

    applied = envfile.load(env_file)

    assert applied == []
    assert os.environ["FBC_TEST_ONE"] == "from-the-environment"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert envfile.load(tmp_path / "nothing-here.env") == []


def test_an_unreadable_file_does_not_stop_the_service(tmp_path):
    """The deployment a raised exception here would take down is the one whose
    operator is halfway through configuring it."""
    assert envfile.load(tmp_path) == []  # a directory, not a file


def test_comments_blanks_and_export_are_handled():
    text = "\n".join([
        "# a comment",
        "",
        "   ",
        "export FBC_TEST_ONE=alpha",
        "  FBC_TEST_TWO = beta  ",
        "not a pair",
        "=novalue",
    ])
    assert envfile.parse(text) == [
        ("FBC_TEST_ONE", "alpha"),
        ("FBC_TEST_TWO", "beta"),
    ]


def test_a_value_is_literal_exactly_as_docker_reads_it():
    """Docker's --env-file does no quote stripping and no expansion, and this
    file is handed straight to it by scripts/share.sh. Matching that is the
    whole point: an app password with spaces in it is written bare, and a
    quoted one is wrong in both paths rather than only in one."""
    parsed = dict(envfile.parse("\n".join([
        "FBC_SMTP_PASS=abcd efgh ijkl mnop",
        "FBC_MAIL_FROM=$USER@example.com",
        "FBC_TEST_QUOTED=\"quoted\"",
        "FBC_TEST_EQUALS=a=b=c",
    ])))

    assert parsed["FBC_SMTP_PASS"] == "abcd efgh ijkl mnop"
    assert parsed["FBC_MAIL_FROM"] == "$USER@example.com"
    assert parsed["FBC_TEST_QUOTED"] == '"quoted"'
    assert parsed["FBC_TEST_EQUALS"] == "a=b=c"


def test_a_quoted_value_is_warned_about(env_file, caplog):
    """It is still set — this is a warning, not a correction. Silently
    stripping would put this parser and Docker's out of step."""
    env_file.write_text('FBC_TEST_QUOTED="hunter2"\n', encoding="utf-8")
    os.environ.pop("FBC_TEST_QUOTED", None)

    with caplog.at_level("WARNING"):
        envfile.load(env_file)

    assert os.environ["FBC_TEST_QUOTED"] == '"hunter2"'
    assert any("quoted" in record.message for record in caplog.records)
    # The name, never the value: this file holds an API key and a password.
    assert all("hunter2" not in record.getMessage() for record in caplog.records)


def test_settings_reads_the_file_before_any_value_is_used(env_file, monkeypatch):
    """Every value the service reads comes through `settings()`, so folding the
    file in there means no import order can read a variable first."""
    env_file.write_text("FBC_MAX_UPLOAD_MB=7\n", encoding="utf-8")
    monkeypatch.delenv("FBC_MAX_UPLOAD_MB", raising=False)
    monkeypatch.setenv("FBC_ENV_FILE", str(env_file))
    config.settings.cache_clear()

    try:
        assert config.settings().max_upload_mb == 7
    finally:
        config.settings.cache_clear()


def test_the_committed_template_declares_what_it_promises():
    """The template is the documentation of record for these names. If a
    variable is renamed and the template is not, this fails rather than the
    operator finding out from a feature that quietly does nothing."""
    from pathlib import Path

    template = Path(__file__).resolve().parent.parent / "secrets" / "local.env.example"
    names = {key for key, _ in envfile.parse(template.read_text(encoding="utf-8"))}

    assert {
        "FBC_SMTP_HOST",
        "FBC_SMTP_PORT",
        "FBC_SMTP_USER",
        "FBC_SMTP_PASS",
        "FBC_MAIL_FROM",
        "FBC_OWNER_EMAILS",
        "ANTHROPIC_API_KEY",
    } <= names


def test_the_template_carries_no_values():
    """It is committed. A value in it is a secret in the repository."""
    from pathlib import Path

    template = Path(__file__).resolve().parent.parent / "secrets" / "local.env.example"
    secrets = {"FBC_SMTP_USER", "FBC_SMTP_PASS", "FBC_MAIL_FROM", "ANTHROPIC_API_KEY",
               "FBC_GITHUB_TOKEN", "FBC_OWNER_EMAILS"}
    for key, value in envfile.parse(template.read_text(encoding="utf-8")):
        if key in secrets:
            assert value == "", f"{key} has a value in the committed template"
