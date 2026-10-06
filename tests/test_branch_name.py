"""The branch-naming rule: every branch is named for what it changes.

CLAUDE.md (Working discipline) makes it permanent; `scripts/check_branch_name.py`
is its one implementation, run by CI on every pull request and by the
SessionStart hook in `.claude/settings.json`. The cases below are this
repository's own branches, so the rule is pinned to the names it was written
against: the generated ones it must reject and the topic names it must keep.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_branch_name.py"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("check_branch_name", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATED = [
    "claude/bold-gauss-05oe0f",
    "claude/adoring-heisenberg-3tvsu6",
    "claude/fbc-reviewer-aesthetic-a2k5o9",
    "claude/project-declaration-questionnaire-xiz0ob",
    "claude/raster-tables-feature-st92xo",
    "claude/sheet-identification-fix-didj1r",
    "claude/training-mode-expansion-6hrhcx",
    "claude/version-numbers-local-deploy-94jwpp",
    "cloud-dev/relaxed-lamport-55sv0v",
]

TOPICS = [
    "claude/no-billing-local-backend",
    "claude/cad-dwg-dxf-input",
    "claude/structural-consult-plan",
    "claude/wind-standard-unrecorded-asce7",
    "claude/cad-input-review-fixes",
    "claude/rebuild-console-desktop-launch",
    "claude/enforce-topic-branch-names",
]


@pytest.mark.parametrize("name", GENERATED)
def test_every_generated_branch_this_repo_has_had_is_refused(check, name):
    found = check.problems(name)
    assert any("random suffix" in p for p in found), found


@pytest.mark.parametrize("name", TOPICS)
def test_every_topic_branch_this_repo_has_had_passes(check, name):
    assert check.problems(name) == []


@pytest.mark.parametrize("name", ["main", "refs/heads/main", "dependabot/pip/requests-2.32.3",
                                  "renovate/angular-22.x"])
def test_main_and_the_bots_are_exempt(check, name):
    assert check.problems(name) == []


@pytest.mark.parametrize("name, says", [
    ("local-changes", "no prefix"),
    ("claude/fix", "one word"),
    ("claude/Fix-Thing", "kebab-case"),
    ("claude/fix_the_thing", "kebab-case"),
    ("claude/fix--thing", "empty word"),
    ("claude/a/b-c", "more than one"),
    ("Claude/fix-thing", "prefix"),
    ("claude/" + "-".join(["word"] * 14), "characters"),
    ("claude/fix-thing-qwzrtx", "random suffix"),
])
def test_each_way_a_name_can_be_wrong_is_named(check, name, says):
    assert any(says in p for p in check.problems(name)), check.problems(name)


def test_a_six_letter_word_is_not_mistaken_for_a_suffix(check):
    for word in ("review", "plates", "stairs", "egress", "asce22x"[:5]):
        assert not check.looks_random(word), word
    assert check.problems("claude/occupancy-egress") == []


def test_the_command_line_exits_by_the_verdict():
    ok = subprocess.run([sys.executable, str(SCRIPT), "claude/cad-dwg-dxf-input"],
                        capture_output=True, text=True)
    bad = subprocess.run([sys.executable, str(SCRIPT), "claude/bold-gauss-05oe0f"],
                         capture_output=True, text=True)
    assert ok.returncode == 0
    assert bad.returncode == 1
    assert "git branch -m claude/bold-gauss-05oe0f claude/<what-changes>" in bad.stderr


def test_the_session_hook_informs_and_never_fails_a_session():
    bad = subprocess.run([sys.executable, str(SCRIPT), "--session-start",
                          "claude/bold-gauss-05oe0f"], capture_output=True, text=True)
    good = subprocess.run([sys.executable, str(SCRIPT), "--session-start",
                           "claude/cad-dwg-dxf-input"], capture_output=True, text=True)
    assert bad.returncode == 0 and good.returncode == 0
    assert "Before the first push" in bad.stdout
    assert good.stdout == ""


def test_ci_runs_the_check_on_every_pull_request():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    job = workflow.split("  branch-name:\n", 1)[1].split("\n  # ──", 1)[0]
    assert "if: github.event_name == 'pull_request'" in job
    assert "scripts/check_branch_name.py" in job
    # The head ref is attacker-controlled text on a fork's PR: it goes in by
    # environment variable, never interpolated into the script.
    assert "HEAD_REF: ${{ github.head_ref }}" in job
    assert 'run: python3 scripts/check_branch_name.py "$HEAD_REF"' in job


def test_the_session_start_hook_runs_the_same_script():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for group in settings["hooks"]["SessionStart"]
                for h in group["hooks"] if h.get("type") == "command"]
    assert any("scripts/check_branch_name.py" in c and "--session-start" in c for c in commands)


def test_claude_md_states_the_rule_as_permanent_and_names_its_enforcement():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Name every branch after its topic — a permanent rule." in text
    assert "scripts/check_branch_name.py" in text
    assert "branch name" in text and ".claude/settings.json" in text
