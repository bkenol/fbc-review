"""Every button variant declares its hover colours beside its resting ones.

The standing rule for `web/src/styles.css`: the engineer red is the *fallback* —
what a button hovers to when nothing else said — and a button type that sets its
own colours sets its hover in the same declaration block, as the three
`--hover-*` variables.

It exists because the alternative was measured and does not hold. Hover colour is
applied in one rule, `button:hover:not(:disabled)`, which is specificity (0,2,1).
A variant's natural `.state-btn:hover` is (0,2,0), so the type selector won and
green ACCEPTED, blue ACTIONED, the pressed wording toggle and a hovered queue row
all turned the same engineer red. Out-specifying it with `:not(:disabled)` works
and is unreadable — a construct present for the cascade rather than the meaning,
which the next variant added would forget.

A test rather than a comment because a convention nobody checks is a convention
that decays, and this one fails invisibly: the variant looks right until somebody
hovers it. Tested here rather than in vitest because the Angular pipeline
resolves the stylesheet's font `url()`s when it is imported, and this only needs
to read the text.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parent.parent / "web" / "src" / "styles.css"

#: `selector { body }`, with comments stripped so a commented-out declaration
#: cannot satisfy the contract.
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: Variants that override a resting background or colour, and therefore have to
#: carry their own hover. Left off, the variant inherits the primary red and
#: silently stops meaning what it looks like.
VARIANTS = [
    "button.ghost, .btn.ghost",
    "button.inline, a.inline",
    ".vocab-btn",
    ".vocab-btn.on",
    ".qstat",
    ".state-btn",
    ".state-btn.on",
    ".state-clear",
    ".queue-head",
    ".rail-step",
    ".tool",
    ".tool.on",
    ".rail-chip",
    ".rail-chip.on",
    ".side-row",
    ".side-row.on",
    ".verdict",
    ".verdict[data-polarity='good']",
    ".verdict.on",
    ".verdict[data-polarity='good'].on",
    ".ink",
    ".ink.on",
    ".pastrow",
    ".stage-arrow",
    ".side-tabs button",
    ".side-tabs button.on",
]

#: Variants that change one channel of an already-declared block — they set only
#: the `--hover-*` that differs, because the rest cascade from the same element.
PARTIAL = [".qstat.on"]

HOVER_VARS = ("--hover-bg", "--hover-ink", "--hover-edge")


def rules():
    text = _COMMENT.sub("", CSS_PATH.read_text(encoding="utf-8"))
    out = []
    for selector, body in _RULE.findall(text):
        selector = " ".join(selector.split())
        if not selector or selector.startswith("@"):
            continue
        out.append((selector, body))
    return out


def body_of(selector: str) -> str:
    found = [b for s, b in rules() if s == selector]
    assert found, f"no rule in styles.css for `{selector}`"
    return found[0]


def test_hover_colour_is_applied_in_exactly_one_place():
    """One consumer, reading the variables. A second would be a second opinion."""
    consumers = [s for s, _b in rules() if s.startswith("button:hover:not(:disabled)")]
    assert len(consumers) == 1, consumers

    body = body_of(consumers[0])
    assert "background: var(--hover-bg)" in body
    assert "color: var(--hover-ink)" in body
    assert "border-color: var(--hover-edge)" in body


@pytest.mark.parametrize("anchor", ["a.inline:hover", "a.pastrow:hover"])
def test_the_anchors_that_share_a_button_style_are_covered_too(anchor):
    """These share a button's resting block but are not buttons, so
    `button:hover` never reaches them. `.pastrow` is both — a `<button>` in the
    workspace, an `<a>` in the training console — and dropping the anchor left
    one of the two with no hover at all, which the browser audit caught and
    reading the rule did not."""
    consumer = [s for s, _b in rules() if s.startswith("button:hover:not(:disabled)")][0]
    assert anchor in consumer


def test_the_engineer_red_is_the_default_not_something_to_fight_off():
    base = body_of("button, .btn")
    assert "--hover-bg: var(--primary-hover)" in base
    assert "--hover-edge: var(--primary-hover)" in base
    assert "--hover-ink:" in base


@pytest.mark.parametrize("selector", VARIANTS)
@pytest.mark.parametrize("variable", HOVER_VARS)
def test_a_variant_declares_its_hover_beside_its_colours(selector, variable):
    body = body_of(selector)
    assert f"{variable}:" in body, (
        f"`{selector}` sets its own resting colours but no {variable}, so it "
        f"inherits the primary red on hover"
    )


APP = Path(__file__).resolve().parent.parent / "web" / "src" / "app"

#: The last compound in a selector — what the rule actually paints. Splitting on
#: the combinators rather than rejecting every space, because `.side-tabs
#: button:hover` paints a button and slipped through a whole-selector test while
#: `.qstat:hover .qstat-l` paints a child and is not this contract's business.
_COMBINATOR = re.compile(r"\s*[>+~]\s*|\s+")


def target(selector: str) -> str:
    return _COMBINATOR.split(selector.strip())[-1]


def button_classes() -> set:
    """Every class that appears on a `<button>` in a template.

    Read from the templates because the element type is what decides whether the
    base `button` rule reaches a variant at all, and a stylesheet cannot say.
    `.pastrow` is both — a `<button>` on the tool page and an `<a>` in the
    training console — so the class is in, and the anchor-only `a.pastrow:hover`
    is skipped where the offender scan runs.
    """
    out = set()
    for path in APP.rglob("*.html"):
        for tag in re.findall(r"<button\b[^>]*>", path.read_text(encoding="utf-8"), re.S):
            for attr in re.findall(r'class="([^"]*)"', tag):
                out.update(t for t in attr.split() if t and "{" not in t)
    return out


def test_the_templates_still_carry_the_buttons_this_contract_is_about():
    """If this finds nothing, every assertion below passes vacuously."""
    found = button_classes()
    assert {"state-btn", "qstat", "queue-head", "rail-step"} <= found, sorted(found)


def test_no_variant_re_specifies_a_hover_colour_to_win_the_cascade():
    """`:not(:disabled)` on a button variant's own hover rule is the tell: it is
    there to out-specify the base rule, which the variables make unnecessary."""
    buttons = button_classes()
    offenders = []
    for selector, body in rules():
        # The consumer rule's own selector list — `.btn:hover`, `a.inline:hover`
        # — is the contract, not a breach of it.
        if selector.startswith("button:hover:not(:disabled)"):
            continue
        for one in (s.strip() for s in selector.split(",")):
            painted = target(one)
            if ":hover" not in painted:
                continue
            # An anchor styled like a button: the base `button:hover` rule never
            # reaches it, so there is no cascade to lose and nothing to declare.
            if painted.startswith("a."):
                continue
            classes = set(re.findall(r"\.([A-Za-z0-9_-]+)", painted))
            if not (classes & buttons) and not painted.startswith("button"):
                continue
            if re.search(r"(?:^|;|\s)(background|color|border-color)\s*:", body):
                offenders.append(one)

    assert offenders == [], (
        "these set a hover colour on a button instead of declaring --hover-* "
        "beside their resting colours: " + ", ".join(offenders)
    )


@pytest.mark.parametrize("selector", PARTIAL)
def test_a_variant_that_changes_one_channel_declares_that_one(selector):
    """`.qstat.on` frames the tile in ink on all four sides, accent included, so
    it restates `--hover-edge` and leaves the background and ink to `.qstat`."""
    body = body_of(selector)
    assert any(f"{v}:" in body for v in HOVER_VARS), (
        f"`{selector}` sets a resting colour and declares no hover at all"
    )


def test_every_variant_named_here_still_exists():
    """A renamed variant must not quietly drop out of the contract by leaving a
    parametrised case with nothing to check."""
    selectors = {s for s, _b in rules()}
    assert set(VARIANTS) | set(PARTIAL) <= selectors
