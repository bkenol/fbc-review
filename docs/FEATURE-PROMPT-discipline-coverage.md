---
title: Claude Code Prompt — Discipline Coverage Model
type: runbook
tags:
  - code-review
  - florida-building-code
  - coverage
  - source/cowork
status: draft
created: 2026-08-25
source-session: Meridian Drafting
---

# Claude Code prompt — make every code discipline a first-class citizen

Open Claude Code in `C:\Antigravity\fbc-review` and paste everything below the line.
`CLAUDE.md` outranks anything here that conflicts with it.

---

## The defect

A reviewer looked at the ITEC Alico Park output and said it read as *a fire safety review*.
He is right, and the reason is structural rather than cosmetic.

Those seven findings did span six areas — code currency, occupancy, height and area,
structural wind, egress, and flood datum. But **nothing in the output told the reader which
areas were never examined at all**, and the areas that were examined skew heavily toward
life safety because that is where the rules happen to exist. There is no accessibility
rule. No plumbing fixture rule. No mechanical ventilation rule. No energy rule. No interior
finish rule. No existing-building alteration rule.

The register showed 7 findings and 12 abstentions. It did not show that **six entire code
disciplines were never attempted**, because the engine has no concept of a discipline that
it could fail to attempt. An abstention says "a rule tried and could not get its inputs."
Silence says nothing, and silence is exactly what a reader interprets as "checked, fine."

That is the same failure as the abstention problem one level up. `CLAUDE.md` already says
*not checked must never be indistinguishable from checked and passed*. The engine honours
that for rules. It does not honour it for disciplines.

## What to build

### 1. A discipline taxonomy — `fbcreview/codes/disciplines.py`

The seventeen areas a Florida commercial plan review actually covers. This is data, keyed
to the code volume and chapters each one governs:

```python
DISCIPLINES = [
  D("admin",        "Administrative & code currency", "FBC-B Ch.1",  ["101","107.3.4.1"]),
  D("occupancy",    "Occupancy classification",       "FBC-B Ch.3",  ["302","303","304","311","508"]),
  D("heightarea",   "Height, area & construction type","FBC-B Ch.5", ["504","506","507"]),
  D("fireresist",   "Fire-resistance & separation",   "FBC-B Ch.6,7",["601","602","705","707","708"]),
  D("finish",       "Interior finish",                "FBC-B Ch.8",  ["803"]),
  D("fireprot",     "Fire protection systems",        "FBC-B Ch.9",  ["903","906","907"]),
  D("egress",       "Means of egress",                "FBC-B Ch.10", ["1004","1005","1006","1010","1013","1017","1020"]),
  D("accessibility","Accessibility",                  "FBC-A",       ["206","208","213","304","305","404","604","606","703","904"]),
  D("environment",  "Interior environment",           "FBC-B Ch.12", ["1203","1204","1207","1208"]),
  D("energy",       "Energy conservation",            "FBC-EC",      ["C402","C405"]),
  D("envelope",     "Exterior walls & openings",      "FBC-B Ch.14,17",["1403","1405","1709","F.S. 553.842"]),
  D("structural",   "Structural",                     "FBC-B Ch.16", ["1604","1609","1613"]),
  D("plumbing",     "Plumbing",                       "FBC-P",       ["403","405","410","1002"]),
  D("mechanical",   "Mechanical",                     "FBC-M",       ["403","501","505"]),
  D("electrical",   "Electrical",                     "NFPA 70",     ["210","220","700"]),
  D("existing",     "Existing buildings",             "FBC-EB",      ["Ch.6","Ch.7","Ch.8","Ch.9"]),
  D("site",         "Site, civil & flood",            "FBC-B Ch.16 / ASCE 24", ["1612"]),
]
```

Every `Finding`, `Abstention` and rule gains a `discipline` key from this list. `Finding`
already carries a free-text `discipline` string — replace it with a controlled key and
render the human label from the taxonomy, so the register can group and count reliably.

### 2. Four coverage states, not two

Per discipline, per review:

| State | Meaning |
| --- | --- |
| `CHECKED` | at least one rule ran and produced a finding or a pass |
| `ABSTAINED` | rules exist and every one of them declined for a stated reason |
| `NOT_IMPLEMENTED` | **no rule exists yet for this discipline** |
| `OUT_OF_SCOPE` | the discipline's sheets are not in the submitted set, or the declaration excludes it |

`NOT_IMPLEMENTED` is the state the engine is missing and the whole point of this work. It
must appear in the output as prominently as a finding does. A reader has to be able to see
"accessibility: no rules implemented" without reading the source.

### 3. All-disciplines-attempted is the default

`ReviewOptions` gains `disciplines: Optional[List[str]] = None`, meaning **attempt every
discipline in the taxonomy**. A caller may narrow it; the default never does. Narrowing is
recorded on the review and printed, so a scoped review cannot be mistaken for a full one.

Deriving `OUT_OF_SCOPE` honestly: read the **sheet index**, not just the sheets present. The
JetSet Pilates set is an architectural package whose own index lists P.001-P.102, M.001-M.401
and E-001-E-401. Those disciplines are not out of scope for the project — they are **absent
from this PDF**, which is a different and more useful statement. Distinguish
`OUT_OF_SCOPE` (project genuinely has none) from `NOT_SUBMITTED` (the index names sheets
this file does not contain).

### 4. Output — a coverage matrix in the register

Add a register page, before the findings, listing all seventeen disciplines with: state,
rules run, findings by severity, abstentions, and the governing code volume. Colour it with
the existing severity palette and give `NOT_IMPLEMENTED` its own swatch and legend entry.

Also add the count to the front-matter summary strip. Today it reads
`1 CRITICAL · 3 HIGH · 5 MEDIUM · 0 LOW · 0 MEASURED · 9 VERIFIED`. It should also read
`11 of 17 disciplines checked · 6 not implemented`.

### 5. Fill the largest gaps

Ranked by what a Florida plans examiner returns most often on a tenant fit-out, and by how
mechanical the check is. Every one of these was found by hand on the JetSet Pilates set and
none of them has a rule today:

1. **`plumbing`** — `PLUMB.FIXTURE_COUNT`. Compute required fixtures from Table 403.1 at
   the occupant load the building code establishes, and compare with the provided schedule.
   Include `PLUMB.FIXTURE_BASIS`: flag when the fixture calculation uses an occupant load
   that differs from the one in the Table 1004.5 block. On JSP the sheet used **24** for
   fixtures while its own occupant table totals **36**, and leaned on an exception
   conditioned on 25 or fewer.
2. **`accessibility`** — `ACCESS.KEYNOTE_VALUES`. The scalars are fixed numbers and the
   check is pure lookup: clear floor space 30x48, turning circle 60, water closet clearance
   60x56, grab bars 42/36/12/24, lavatory rim 34, knee 27, mirror 40, service counter 36.
   On JSP a keynote read `30" X 40"` where the code says 30x48.
3. **`finish`** — `FINISH.TABLE_803_13`. Class A/B/C by occupancy and sprinkler status, plus
   a stale-citation check: `Table 803.11` is the pre-2021 number and appears on JSP.
4. **`existing`** — `EB.ALTERATION_LEVEL`. Level 1/2/3 against the described scope.
5. **`admin`** — `ADMIN.CITATION_AUTHORITY`. Flag references to model codes where the
   Florida code governs. JSP cites `IPC 2021`, `IEBC 2023`, `2023 International Energy
   Conservation Code`, `NFPA 101 2023` and the `2010 ADA Standards`, while its own adopted
   list names the Florida equivalents — and gives NFPA 101 as **2021** two inches away.
6. **`fireprot`** — `FIRE.ALARM_REQUIRED` (907.2.2 thresholds) and
   `FIRE.EGRESS_WIDTH_FACTOR`: 0.15 in/occupant requires a sprinkler system **and** an
   emergency voice/alarm communication system per 907.5.2.2, not sprinklers alone. JSP gets
   this right; most sets do not.
7. **`environment`** — `ENV.CEILING_HEIGHT` (1003.2 / 1208.2).

## Regression and acceptance

- `pytest tests/ -v` green. Sculpted Hot Pilates must keep producing what it produces today.
- Re-run ITEC. The finding count may not change much; **the coverage matrix must show six or
  more disciplines as `NOT_IMPLEMENTED`** before the new rules land, and fewer after. That
  matrix is the deliverable here, not a higher finding count.
- Add the JetSet Pilates set as a third fixture. It is the best accessibility, plumbing and
  existing-building test available: a 1,813 SF Group B fit-out, sprinklered, no alarm,
  adjacent A-2 tenant, two accessible restrooms, 14 sheets, every one `/Rotate 270`.
- Assert the seven rules above fire on it, and that `PLUMB.FIXTURE_BASIS` catches the 24
  versus 36 discrepancy.

## A renderer bug this exposed — fix it first

`render/markup.py::_box` places annotations with `add_rect_annot(b)` where `b` is in
**displayed** coordinates. Annotations are placed in the page's own **un-rotated** space.
Every sheet in the two earlier test sets was `/Rotate 0`, where the two spaces coincide, so
this was invisible. All fourteen JetSet sheets are `/Rotate 270`, and every marker landed
about a thousand points from its target — boxes over the vicinity map instead of the tables.

The fix is one line, and `_Surface` and `_nat()` already exist to do it:

```python
bm = b * pg.derotation_matrix if (pg.rotation % 360) else b
a = pg.add_rect_annot(bm)
```

The same applies to the text-annotation origin and to the chip drawing. Regression-test it
by asserting that a marker's rect on a rotated page falls inside the page's displayed rect
and within the expected band.

## Do not

- Do not report a discipline as checked because a rule in a neighbouring discipline touched
  the same sheet.
- Do not let `NOT_IMPLEMENTED` be silently folded into `ABSTAINED`. They mean different
  things: one is "the tool tried and could not", the other is "the tool has never been
  taught this".
- Do not narrow the default discipline set to make the matrix look better.
- Do not edit `tests/test_regression.py` to make a phase pass.

## Report back with

The coverage matrix for all three test sets before and after, which disciplines moved from
`NOT_IMPLEMENTED` to `CHECKED`, and the list still outstanding — that last list is the
roadmap and it should be visible in the product, not only in the commit message.
