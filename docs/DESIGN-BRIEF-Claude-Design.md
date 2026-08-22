# Copy block for Claude Design

Paste everything below the rule into a new Claude Design chat with the **Meridian
Design System** project attached.

Scoped to the FBC Code Reviewer web interface — the tool in this repository. If
you meant a different interface, keep the system rules and the conflict
resolution in section 4 and swap sections 2–3.

---

Design the web interface for the **FBC Code Reviewer**, using the Meridian
Design System. Every token, component and copy rule in that system applies
without exception. Where this brief and the system disagree, the system wins.

## 1. What the product is

A pre-submittal plan review. An architect uploads a multi-sheet permit set as a
PDF, chooses the review parameters, and gets back the same set with a review
margin added to every sheet, plus a findings register at the back.

The review is deterministic. It reads the drawing's own vector geometry and text,
checks every stated code value against the section it cites, redoes the
arithmetic, and measures the egress paths off the drawing at the scale recorded
inside the file. It makes no model calls and it does not guess: a rule that
cannot get the input it needs abstains and says so.

That last property is the product. **"Not checked" must never look like "checked
and passed."** The interface has to carry that distinction visually, not just in
prose.

It belongs to Meridian, a drafting practice. The people using it read drawings
for a living. Design for a reviewer at a desk with the set open beside them, not
for a prospect being persuaded.

## 2. The screens

**One page, four states.** No dashboard, no nav, no sidebar. The header is the
absolute overlay the system specifies.

**a. Empty.** A drop zone over a real `<input type="file">`, and the review
parameters below it. Parameters are: project name, code edition (2023 8th
Edition; the 2026 9th is listed and disabled, never hidden), occupancy group
(A-2, A-3, B, M, E), sprinklered yes/no, severity floor, two switches for
whether to mark what passes and whether to measure egress geometry, and a switch
for rebuilding scanned sheets. Each switch carries one line saying what it
actually changes — these are not decorative.

**b. Running.** Five named stages: reading the PDF, extracting schedules and
code data, running rules, rendering the markup, delivering. Six when scanned
sheets are being rebuilt. A real 24-sheet set takes about fifteen seconds, so
this state is brief and must not be theatrical. Show elapsed seconds as a mono
figure. It also needs an honest "lost contact with the server" condition —
the review keeps running whether or not the browser can see it, and the copy
must say that rather than implying failure.

**c. Result.** Severity tally, provenance line, the findings table, the
abstention register, and two downloads: the marked-up PDF and the findings JSON.

**d. Refused.** A set that is entirely scanned images is turned away before it
is reviewed, because reviewing it would produce zero findings and zero findings
reads as a clean set. The refusal states what was measured and offers the
rebuild.

Also needed: signed out, and signed in but not on the allowlist. The second is
its own state with its own message, not a generic error.

## 3. Populate it with this, not with placeholder text

Real output from the Sculpted Hot Pilates permit set, 24 sheets, 17.8 MB:

Provenance line — set this in mono, it is the credibility of the whole screen:

> 24 sheets · 28 pages out · 200 CAD layers preserved · 34 live annotations ·
> 13 markers placed · 12 rules · scale resolved on 13/24 pages · 17.4 MB · 15.3 s

Tally: 1 critical, 2 high, 4 medium, 0 low, 1 measured, 5 verified.

Findings, with their real IDs and sheets:

| ID | Sheet | Severity | Finding |
| --- | --- | --- | --- |
| C-01 | M-1 | CRITICAL | Outdoor-air capacity 531 CFM below this sheet's own requirement |
| H-02 | G-0 | HIGH | Common path requirement understated on G-0 |
| H-03 | A-2 | HIGH | Door 104 is a 2'-8" leaf — under the 32 in. clear width the set requires |
| M-04 | G-0 | MEDIUM | Building area disagrees between sheets |
| V-02 | G-0 | VERIFIED | Dead-end limit 20 LF — correct for Group A |
| MEAS-1 | G-1 | MEASURED | Longest egress run measured off the drawing — 70.7 ft |

The critical finding's body, which the result screen leads with:

> Required 881 CFM. RTU-2 scheduled outside air = 350 CFM. Shortfall 531 CFM —
> 40 percent of the requirement is delivered. The calculation is correct; the
> equipment selected cannot meet it, and no note reconciles the two figures.

An abstention, which must read as a limit and not a pass:

> `EGRESS.EXIT_COUNT` — exit count row not found in a code data block

Note the numbers already carry the system's tone: `531 CFM`, `2'-8"`, `70.7 ft`,
`13/24`. Set every one of them in mono. Do not reformat imperial dimensions into
metric or into space-separated thousands — on a Florida permit set `2'-8"` is the
correct notation and changing it would be wrong.

## 4. The one real conflict, and how to resolve it

The engine has six severities: CRITICAL, HIGH, MEDIUM, LOW, MEASURED, VERIFIED.
The obvious treatment is six colours. **Do not do that.** The system allows two
accents at 3% red and 2% blue, no third accent and no gradient, and a
six-colour spectrum would blow the distribution and make the screen look like a
bug tracker rather than a drawing.

Resolve it the way a drafter would, with notation rather than hue:

- **CRITICAL and HIGH** carry `--red-1`. These are the two that stop a permit.
  Red is already the system's dimension-and-scope colour, so it is doing the
  same job here: this is the measured thing that is wrong.
- **MEASURED** carries `--blue-2`, the annotation colour. It is a reading taken
  off the drawing, which is exactly what a survey annotation is.
- **MEDIUM, LOW and VERIFIED** sit in the ink ramp. Differentiate them by rule
  weight, tick presence and mono label — not by colour.
- Severity labels are mono uppercase throughout, which the system already
  requires for labels.

The severity tally should read as a **dimension run**, not as a row of stat
cards: a horizontal rule with tick terminators, figures in mono above it, labels
below. Reuse `DimensionRule`.

For the abstention register, use `Annotation` — underlined mono in survey blue
with a red cross tick. A rule that declined to run is a callout on the drawing,
not an error message. This is the single most important visual decision on the
screen and it should be unmistakable at a glance that these items were *not
checked*, distinct from the ones that passed.

## 5. Copy

The system's content fundamentals apply as written: sentence case for headings
and body, uppercase only for mono labels and button text, never title case, no
emoji, no exclamation marks, none of "solutions", "seamless", "cutting-edge",
"leverage". Buttons are verbs in mono caps.

Buttons here: `RUN THE REVIEW`, `DOWNLOAD MARKED-UP SET`, `FINDINGS JSON`,
`REVIEW ANOTHER SET`, `RECONNECT`.

Headline shape is the system's — a concrete claim with a measure in it. Two that
fit this product:

> Every stated value checked against the section it cites.

> Twelve rules, zero model calls, fifteen seconds.

The disclaimer is required on every state and is not negotiable prose:

> Advisory only. A licensed design professional remains responsible for code
> compliance — this is not a plan approval and does not replace review by the
> authority having jurisdiction.

## 6. Constraints

- Radius 0 everywhere. 1px hairlines in `--paper-edge`. No shadow, no elevation.
- No icon set. Drafting notation only — dimension terminators, cross ticks,
  underlined mono annotations. If a conventional icon is genuinely unavoidable,
  Lucide in `--ink-3`, and flag it as a substitution.
- Hover is a colour swap, not a fade. Nothing scales, shrinks or bounces.
- No frosted glass beyond the one sanctioned button-outline blur.
- Focus is a 1px `--blue-1` outline at 2px offset, square.
- The file input must stay a real focusable `<input type="file">` behind the drop
  zone, and job status must stay in an `aria-live` region. Keyboard and screen
  reader users run this tool too.
- Light and dark both follow the OS. No theme toggle.
- This screen is dense with figures. Left-align everything; no centred body text.

Deliver the four main states plus the two auth states as a single stepped page,
consistent with how the Meridian site scrolls.
