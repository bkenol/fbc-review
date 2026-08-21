---
title: FBC Code Reviewer — ITEC Alico Park Findings
type: note
tags:
  - code-review
  - florida-building-code
  - pdf-pipeline
  - lee-county
  - source/cowork
status: draft
created: 2026-08-21
source-session: Meridian Drafting
---

# FBC Code Reviewer — ITEC Alico Park Findings

Second test case for the reviewer, chosen because it is deliberately harder than
[[FBC Code Reviewer — Sculpted Hot Pilates Findings]]: new construction, a different
county, a shell building rather than a tenant fit-out, and a set plotted from AutoCAD LT
with no preserved layers and raster code tables.

Deliverable: `ITEC ALICO PARK - CODE REVIEW - MARKUP.pdf` — 40 pages (35 marked drawing
sheets + 5 register pages), 16.6 MB, 122 live annotations.

## Project identification

- **ALICO ITEC PARK, Lots 20-22 — 12250 ITEC Park Drive, Ft. Myers, Lee County FL 33913**
- STRAP `07-46-26-L1-10000.0200`, Folio ID `10603042`
- Multi-use **shell** building. Type II-B non-combustible non-rated, 1 story, 26 ft 4 in.,
  15,376 SF, 8 tenant units, 86-space lot on 2.21 AC
- Architect and structural: Phoenix Associates of Florida. M/E/P: Spelman Engineering
- **100% Permit Set 11/12/21**, nine revisions through 04.12.2023, five of them AHJ
  correction rounds
- Cited throughout to **Florida Building Code 7th Edition (2020)**; FFPC 7th Edition /
  NFPA 101 2018; NEC 2017

Cross-validation: the current leasing brochure for 12252 ITEC Park Drive gives 15,376 SF
total, 3,272 SF largest demisable unit, 2.21 AC, 86 parking spaces — all matching the
drawings exactly. Its tenant list is what produces finding H-01.

## Counts

`1 CRITICAL · 3 HIGH · 5 MEDIUM · 2 LOW · 9 VERIFIED`

## Findings that matter

### C-01 — CRITICAL — designed and cited to a superseded code edition

Every code reference in the set is the **7th Edition (2020)**; S-001 designs to **ASCE 7-16**.
The **8th Edition (2023)** took effect **31 December 2023** and is the code in force now;
the 9th Edition (2026) takes effect on or about 31 December 2026. The 8th Edition adopts
**ASCE 7-22**, which replaces the wind maps this structure was designed from.

The set was legitimately 7th Edition when drawn. This finding is about what happens if it
is submitted, re-submitted or revived today — it is a re-analysis, not a cover-sheet edit.

- Action: confirm whether a live permit exists from the original submittal. If it lapsed,
  update every code reference to the 8th Edition and re-run wind to ASCE 7-22.

### H-01 — the occupancy analysis contradicts itself, and the building as leased is not all Group B

Two adjacent rows on G-002 disagree:

- `MIXED OCCUPANCY? = NO`
- `OCCUPANCY SEPARATION RATING PROVIDED = MULTIPLE - SEPARATED PER TABLE 508.4`

`FBC-B Table 508.4` only applies to a mixed-occupancy building. The whole 15,376 SF is
classified `BUSINESS / OFFICE, PROFESSIONAL SERVICES`. The leasing brochure lists fitness,
retail and healthcare tenants for this shell — none of which is Group B.

`FBC-B Table 1004.5` occupant load factors that matter here:

| Use | Factor |
|---|---|
| Business | 150 gross |
| Exercise room | 50 gross |
| Mercantile, grade floor | 30 gross |

Citations: `FBC-B 302.1`, `508.3`, `508.4`, `Table 1004.5`.

### H-02 — the OCCUPANT FACTOR column is a unit-number sequence, not a code factor

Both occupant-load tables on A-101 carry an `OCCUPANT FACTOR` column reading:

```
100, 101, 102, 103, 104, 105, 106, 104
```

That is the unit number, not a code value. `Table 1004.5` gives **150 gross** for business
in both the 7th and 8th Editions.

- Areas: 1,855 / 1,441 / 2,142 / 2,262 / 1,545 / 1,418 / 1,441 / 3,272 SF = 15,376 SF
- Recomputed at 150 gross: **107 occupants**, against the **152** stated
- 152 is conservative against 107, so nothing downstream is unsafe — but the column counts
  upward and no plans examiner will accept it
- The same wrong column is repeated verbatim in the FFPC / NFPA 101 table beneath it, so
  both analyses inherit the error (`FFPC / NFPA 101 Table 7.3.1.2`)

### H-03 — wind design to a superseded standard, and Exposure B asserted without justification

S-001 states `Vult = 155 mph, Risk Category II, Exposure B, enclosed, GCpi +/-0.18`.

- The 8th Edition adopts ASCE 7-22, whose Lee County maps differ from 7-16 — the design
  wind speed has to be re-read for this parcel, not carried across
- **Exposure B** requires upwind terrain with closely spaced obstructions for 1,500 ft or
  20 times the building height (`ASCE 7 26.7.3`). G-003 shows the site fronting Alico Road
  with a vacant lot west, a storm pond and open right-of-way east, and Lots 20-23 largely
  undeveloped
- **Exposure C** is the more defensible assumption and raises velocity pressure materially

Citations: `FBC-B 1609`, `ASCE 7-22 Fig. 26.5-1`, `ASCE 7 26.7.3`, `FBC-B 1609.3`.

## The MEDIUMs, in one line each

- **M-01 (A-201)** — `TYPICAL IMPACT RATED STOREFRONT SYSTEM` with no Florida Product
  Approval or Miami-Dade NOA number and no scheduled design pressures. The entire lateral
  design rests on `GCpi +/-0.18`; unprotected glazing makes the building partially enclosed
  and `GCpi` becomes `+/-0.55` — roughly three times the internal pressure.
  `FBC-B 1609.1.2`, `1709.5`, `F.S. 553.842`, `ASTM E1996 / E1886`.
- **M-02 (A-102)** — no `FBC-P Table 403.1` fixture calculation appears on any sheet. At the
  stated load of 152 split 76/76: 6 water closets, 4 lavatories, 2 drinking fountains,
  1 service sink. Four completed and four roughed-in single-user rooms clear WC and lav at
  full build-out; only one drinking fountain is shown.
- **M-03 (G-003)** — four accessible spaces is correct for an 86-space lot
  (`FBC-A Table 208.2`), but none is labelled VAN ACCESSIBLE, no 132 in. access aisle is
  called out, and no stall dimensions appear. Florida requires 12 ft accessible car stalls,
  wider than the federal minimum. `208.2.4`, `502.2`, `502.3`, `502.6`, `F.S. 553.5041`.
- **M-04 (G-003)** — FDC shown as future / deferred without the sprinkler layout it serves.
- **M-05 (G-002)** — `FINISHED FLOOR ELEV. 24.85' NGVD`, repeated as `24.85 NAVD` on G-003.
  Lee County and current FEMA mapping both work in **NAVD 88**; the two datums differ by
  about 1.2 ft in this part of the county.

## What the deterministic engine did — and why 0 findings is the correct answer

The engine returned **0 findings and 12 reasoned abstentions** on this set. That is the
"never guess" rule working under stress, not a failure. Every one of the 20 findings above
is hand-authored.

| | Sculpted Hot Pilates | ITEC Alico Park |
|---|---|---|
| Preserved CAD layers (OCGs) | 200 | **0** |
| Code analysis tables | live vector text | **raster images** |
| Scale resolved | 13/24 pages, 12 HIGH | 18/35, medium confidence only |
| Page rotation | none | **7 pages `/Rotate 270`** |
| Plotted from | full AutoCAD | **AutoCAD LT 2022** |

With no OCGs there is no layer filtering, and with raster code blocks there is no cited
section number to key on — which is the single design decision the whole rule corpus rests
on (see [[FBC Code Reviewer — Code Corpus and Rule Authoring]]).

**Roadmap consequence: OCR of raster code-analysis blocks outranks additional rule
coverage.** On a set like this it is the difference between 0 findings and 20.

## Engine generalizations this set forced

Three fixes, all now in the repo, all regression-tested against Sculpted:

1. **Rotation-aware markup.** Widening the mediabox on the wrong axis corrupts a page
   carrying `/Rotate 270` — expanding x grows the *displayed* height. Per-rotation gutter
   plus a `_Surface` adapter that takes displayed coordinates and applies
   `page.derotation_matrix` before drawing.

```python
rot = p.rotation % 360
if   rot == 0:   box = pymupdf.Rect(-GUTTER, 0, w0, h0)
elif rot == 270: box = pymupdf.Rect(0, 0, w0, h0 + GUTTER)
elif rot == 90:  box = pymupdf.Rect(0, -GUTTER, w0, h0)
else:            box = pymupdf.Rect(0, 0, w0 + GUTTER, h0)
p.set_mediabox(box)
```

2. **Sheet code extraction by type size, not position.** The regex only accepted 1-2 digits,
   so NCS three-digit codes (`G-002`) fell through. A position-only fix — take the lowest
   match in the title block — **regressed Sculpted**, because that set's cover sheet puts a
   sheet-index table below the title block and `P-3` sat lowest. Correct rule: take the
   **largest-type** match. Extended again for the dotted `M0.1` form.

```python
_SHEET = re.compile(r"^([GCLSAIQFPMER])-?(\d{1,3}[A-Z]?|\d\.\d[A-Z]?)$")
```

3. **Title-block heuristics.** Skip lines containing `:`, date-like lines, and
   letter-spaced labels (`S H E E T  T I T L E`) — otherwise sheet titles come back as
   `Date:`.

## Related

- [[FBC Code Reviewer — Hub]]
- [[FBC Code Reviewer — Sculpted Hot Pilates Findings]]
- [[FBC Code Reviewer — Deterministic Engine]]
- [[FBC Code Reviewer — Vector PDF Extraction]]
- [[FBC Code Reviewer — Markup Output Spec]]
