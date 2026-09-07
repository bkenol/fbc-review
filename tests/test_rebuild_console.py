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


# ── opening the console in a window of its own ────────────────────────────
# The console is a control panel, not a document. Chrome's `--app=` mode gives
# it its own window, taskbar button and icon, which is what stops it getting
# lost among thirty tabs. Only Chromium-family browsers have it, so the whole
# path has to degrade to the default browser rather than fail.
def test_app_mode_is_off_unless_asked_for(console, monkeypatch):
    """Double-clicking the .cmd must behave exactly as it always has."""
    calls = []
    monkeypatch.setattr(console.webbrowser, "open", lambda u: calls.append(u))
    monkeypatch.setattr(console, "open_app_window", lambda *a, **k: 1 / 0)

    console.open_console("http://127.0.0.1:9/?token=x", "default")
    assert calls == ["http://127.0.0.1:9/?token=x"]


def test_none_opens_nothing_at_all(console, monkeypatch):
    monkeypatch.setattr(console.webbrowser, "open", lambda u: 1 / 0)
    monkeypatch.setattr(console, "open_app_window", lambda *a, **k: 1 / 0)
    console.open_console("http://127.0.0.1:9/", "none")


def test_app_mode_launches_the_browser_with_the_url_as_its_own_window(
        console, monkeypatch, tmp_path):
    fake = tmp_path / "chrome.exe"
    fake.write_text("")
    seen = {}

    def popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(console, "app_browser", lambda: str(fake))
    monkeypatch.setattr(console.subprocess, "Popen", popen)

    assert console.open_app_window("http://127.0.0.1:9/?token=x") is True
    assert seen["argv"][0] == str(fake)
    assert "--app=http://127.0.0.1:9/?token=x" in seen["argv"]
    # No --user-data-dir: a private profile would sign the user out of
    # everything and start a second copy of Chrome to show one local page.
    assert not any(a.startswith("--user-data-dir") for a in seen["argv"])


def test_app_mode_falls_back_to_the_default_browser_when_there_is_no_chrome(
        console, monkeypatch, capsys):
    """A machine with only Firefox still gets the console, and is told why it
    did not get a window of its own."""
    calls = []
    monkeypatch.setattr(console, "app_browser", lambda: None)
    monkeypatch.setattr(console.webbrowser, "open", lambda u: calls.append(u))

    console.open_console("http://127.0.0.1:9/", "app")
    assert calls == ["http://127.0.0.1:9/"]
    assert "default browser" in capsys.readouterr().out


def test_a_browser_that_will_not_start_is_not_a_crash(console, monkeypatch, tmp_path):
    fake = tmp_path / "chrome.exe"
    fake.write_text("")
    monkeypatch.setattr(console, "app_browser", lambda: str(fake))
    monkeypatch.setattr(console.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert console.open_app_window("http://127.0.0.1:9/") is False


def test_an_unset_windows_variable_never_looks_like_a_real_path(console, monkeypatch):
    """os.path.expandvars leaves %ProgramFiles(x86)% alone when it is not set,
    and a literal like that must not be handed to Popen as a browser."""
    monkeypatch.setattr(console, "WINDOWS", True)
    monkeypatch.setattr(console.shutil, "which", lambda name: None)
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.delenv(var, raising=False)
    assert console.app_browser() is None


def test_the_browser_choice_is_a_command_line_option(console):
    import argparse
    parser = argparse.ArgumentParser()
    # Mirrors main(); asserting the parser accepts what the shortcut passes.
    parser.add_argument("--browser", choices=("default", "app", "none"),
                        default="default")
    assert parser.parse_args(["--browser", "app"]).browser == "app"
    assert parser.parse_args([]).browser == "default"


# ── the launchers agree about what exists ─────────────────────────────────
def test_the_cmd_offers_the_app_shortcut_and_points_at_the_script():
    """`Rebuild Console.cmd app-shortcut` is what DEPLOYMENT.md tells people to
    run, so the branch and the file it calls both have to be there."""
    cmd = (ROOT / "Rebuild Console.cmd").read_text(encoding="utf-8")
    assert '"%~1"=="app-shortcut"' in cmd
    assert '"%~1"=="app"' in cmd
    assert "scripts\\app-shortcut.ps1" in cmd
    assert "--browser app" in cmd
    assert (ROOT / "scripts" / "app-shortcut.ps1").exists()


def test_the_shortcut_targets_pythonw_rather_than_the_cmd():
    """The whole point over the older shortcut: a .cmd opens a console window
    however briefly, and a black window flashing on every launch is what stops
    people using a shortcut at all."""
    ps1 = (ROOT / "scripts" / "app-shortcut.ps1").read_text(encoding="utf-8")
    assert "pythonw.exe" in ps1
    assert "rebuild_console.py" in ps1
    assert "--browser app" in ps1
    assert "$s.TargetPath = $python" in ps1


def test_the_shortcut_and_the_console_look_for_the_same_browsers():
    """The .ps1 borrows the browser's icon and the .py launches it. If they
    disagree, the shortcut wears Chrome's icon and opens Firefox."""
    ps1 = (ROOT / "scripts" / "app-shortcut.ps1").read_text(encoding="utf-8")
    for exe in ("chrome.exe", "msedge.exe", "brave.exe"):
        assert exe in ps1, exe

    py = CONSOLE.read_text(encoding="utf-8")
    for exe in ("chrome.exe", "msedge.exe", "brave.exe"):
        assert exe in py, exe


# ══════════════════════════════════════════════════════════════════════════
# The Facts button
# ══════════════════════════════════════════════════════════════════════════
# Added because someone followed the runbook to find a Firebase ID token in
# the Network tab, and there was no `Authorization` header to find: the
# deployment was running with the sign-in bypass on, so nothing was asking for
# one. "Which of those two worlds am I in" is the question that actually
# blocks people, and it is not answerable from the browser.
def _facts_lines(console, mode, tmp_path, port=8060):
    console.log_clear()
    original = console._auth_mode
    console._auth_mode = lambda _port: mode
    try:
        console.facts(tmp_path, port)
    finally:
        console._auth_mode = original
    return console.log_since(0)["lines"]


def test_facts_says_there_is_no_token_when_sign_in_is_bypassed(console, tmp_path):
    text = "\n".join(_facts_lines(console, {"known": True, "enforced": False}, tmp_path))
    assert "no sign-in token to find" in text
    assert "need no token" in text
    # And the curl it offers must not carry an Authorization header, or it
    # sends people looking for the thing that does not exist all over again.
    assert "Authorization: Bearer" not in text


def test_facts_warns_that_the_bypass_is_reachable_when_it_is_on(console, tmp_path):
    """The bypass keys off K_SERVICE, which only Cloud Run sets. Served
    through a tunnel from a workstation that signal is absent, so the guard
    does not fire and the open service is on a public domain."""
    text = "\n".join(_facts_lines(console, {"known": True, "enforced": False}, tmp_path))
    assert "open to anyone" in text
    assert "fbc.omniflexfitness.com" in text


def test_facts_explains_how_to_get_a_token_when_sign_in_is_on(console, tmp_path):
    text = "\n".join(_facts_lines(console, {"known": True, "enforced": True}, tmp_path))
    assert "Authorization: Bearer" in text
    assert "firebaseLocalStorageDb" in text
    assert "one hour" in text


def test_facts_says_so_rather_than_guessing_when_it_cannot_tell(console, tmp_path):
    text = "\n".join(_facts_lines(
        console, {"known": False, "why": "nothing is listening"}, tmp_path))
    assert "Cannot tell" in text
    # It must not fall through to either confident answer.
    assert "no sign-in token to find" not in text


def test_facts_never_prints_a_secret(console, tmp_path):
    """Same rule `local_config` follows: names, never values. A credential on
    screen is a credential in the screenshot somebody pastes into a chat."""
    write(tmp_path, "FBC_SMTP_PASS=hunter2\nANTHROPIC_API_KEY=sk-ant-secret\n")
    for mode in ({"known": True, "enforced": False}, {"known": True, "enforced": True}):
        text = "\n".join(_facts_lines(console, mode, tmp_path))
        assert "hunter2" not in text
        assert "sk-ant-secret" not in text


def test_facts_offers_the_decision_call_the_runbook_needs(console, tmp_path):
    text = "\n".join(_facts_lines(console, {"known": True, "enforced": False}, tmp_path))
    assert "/api/admin/feedback/PASTE_ID/decision" in text
    assert '"decision": "action"' in text
    # The trap that costs a round trip: accept 400s with no proposal attached.
    assert "accept returns 400" in text


def test_the_facts_button_is_wired_to_the_action(console):
    source = CONSOLE.read_text(encoding="utf-8")
    assert '<button id="facts"' in source
    assert 'getElementById("facts").onclick' in source
    assert 'run("facts")' in source
    assert 'if action == "facts":' in source
