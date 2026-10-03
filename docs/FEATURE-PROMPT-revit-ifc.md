---
title: Claude Code Prompt — Reading a Revit model through IFC
type: runbook
tags:
  - code-review
  - florida-building-code
  - bim
  - ifc
status: draft
created: 2026-10-03
source-session: CAD adapter build (DWG/DXF, 2026-10-03)
---

# Claude Code prompt — read a Revit model through its IFC export

Open Claude Code in the repository and paste everything below the line.
`CLAUDE.md` carries the standing rules and outranks anything here that
contradicts it. Read `docs/ARCHITECTURE-V2.md` first, §5 in particular: the CAD
adapter is the nearest thing to what this asks for, and most of its decisions
carry over.

---

## What you are building, and what you are not

Revit users can already be reviewed: they export their sheets to DWG and upload
those (`docs/CAD-INPUT.md` §7). This is the next step — reading what a Revit
**model** knows and a sheet only draws: that a door is a door, how wide it is,
what fire rating somebody typed into it, which room it opens from, the area of
every room as data.

You are building a reader for **IFC**, the open exchange format Revit exports
natively, so that a model's data enters the fact store as claims, each saying
it came from the model and which element. You are **not** building a Revit
reader, and you are not sending anyone's model anywhere.

The owner decided on 2026-10-03 that Revit is a separate task from the DWG
work, and that it goes through IFC. Do not relitigate that; §1 says why it is
right.

## 1. Facts this rests on — verified 2026-10-03

Re-check each before relying on it. Sources are at the end.

**Native `.rvt` is closed.** Outside Revit itself, the only reader is Autodesk
Platform Services (Model Derivative): a paid, metered cloud API that requires
uploading the client's model to Autodesk. That is the same objection that
ruled APS out as the DWG converter (`docs/DEPLOYMENT.md` §9a), and it is
stronger here: a Revit model is the whole building, not a set of sheets.

**Revit exports IFC natively.** Autodesk's help: *"For export, Revit supports
the following standards: IFC4, IFC2x3, and IFC2x2."* The exporter is open
source and updated separately from Revit through the Autodesk App Store, so two
offices on the same Revit version can produce different IFC.

**IfcOpenShell reads IFC.** The `ifcopenshell` package on PyPI is licensed
LGPL-3.0-or-later; version 0.9.0, released 2026-09-29, supports Python 3.10 to
3.15 — so 3.12, which this service is pinned to. Measured here: the 0.9.0 wheel
installs to 221 MB, and it requires `shapely`, `numpy`, `isodate`,
`python-dateutil`, `lark`, `pyparsing` and `typing-extensions`. The reading API
is plain Python: `ifcopenshell.open(path)`, `model.by_type("IfcDoor")`,
`ifcopenshell.util.element.get_psets(element)` — which by default merges the
type's property sets under the occurrence's (`should_inherit=True`) —
`ifcopenshell.util.element.get_container(element)` for the storey or space,
and `ifcopenshell.util.unit.calculate_unit_scale(model)` for units.
`ifcopenshell.draw` generates 2-D SVG drawings from the model, floor plans
among them, with options for space names, space areas and door swings.

**What IFC carries that matters to an FBC review.** Checked against the IFC4
property-set templates bundled in IfcOpenShell 0.9.0
(`ifcopenshell.util.pset.get_template("IFC4")`), which are buildingSMART's own:

| Where | Properties | For |
| --- | --- | --- |
| `IfcDoor` attributes | `OverallWidth`, `OverallHeight` | door size — **nominal, not clear width** (see §3) |
| `Pset_DoorCommon` | `FireRating`, `FireExit`, `HandicapAccessible`, `SelfClosing`, `SmokeStop`, `IsExternal` | door ratings, egress, accessibility |
| `Qto_DoorBaseQuantities` | `Width`, `Height`, `Area` | quantities computed by the exporter |
| `IfcSpace` + `Qto_SpaceBaseQuantities` | `NetFloorArea`, `GrossFloorArea`, `Height` | room areas — computed, not typed |
| `Pset_SpaceCommon` | `GrossPlannedArea`, `NetPlannedArea`, `PubliclyAccessible`, `HandicapAccessible` | planned areas a person entered |
| `Pset_SpaceOccupancyRequirements` | `OccupancyType`, `OccupancyNumber`, `OccupancyNumberPeak`, `AreaPerOccupant` | occupancy and occupant load, per space |
| `Pset_SpaceFireSafetyRequirements` | `FireExit`, `SprinklerProtection`, `SprinklerProtectionAutomatic` | per-space fire safety |
| `Pset_BuildingCommon` | `OccupancyType`, `SprinklerProtection`, `SprinklerProtectionAutomatic`, `NumberOfStoreys`, `FireProtectionClass` | building-level classification |
| `Pset_WallCommon` | `FireRating`, `Combustible`, `LoadBearing`, `IsExternal` | rated assemblies |
| `IfcBuildingStorey` | the storeys, with elevations | stories, and the spatial index of everything else |

IFC2X3 has the common property sets too (its `Pset_DoorCommon` also has
`FireRating`, `FireExit`, `HandicapAccessible`), but its bundled templates have
no `Qto_` quantity sets. **Which of these a Revit export actually fills is not
known until a real export is measured** — the template says what may be there,
not what is. That is Phase 0.

An IFC file in its usual form (STEP physical file, `.ifc`) begins with
`ISO-10303-21;` and names its schema in the header, `FILE_SCHEMA(('IFC4'));` —
so it can be recognised from its bytes, as every other upload is.

## 2. The design question to put to the owner before Phase 3

**IFC has no sheets.** Everything this engine produces is anchored on a sheet:
`Finding.page` indexes the PDF, `rect` points into it, the marked-up PDF draws
on it. A model has storeys, spaces and elements instead. There are two honest
ways to fit that, and they are not exclusive:

1. **Companion mode (recommended first).** The user uploads the sheets (PDF or
   DWG, as today) *and* the model's IFC. Findings stay on the sheets. Model
   values enter the fact store as claims beside what the sheets print, and a
   disagreement between model and sheet is a conflict, reported the way a
   declaration-versus-drawn conflict is. This is the permit review as it
   actually works — the examiner reviews sheets — with the model as a second
   reader.
2. **Model-only mode.** The review plots each storey's plan from the model
   (`ifcopenshell.draw`), makes those the pages, and places each finding on
   its storey at its element; findings with no element are listed by storey and
   space. The marked-up PDF must then say that the plans were generated by this
   review from the model and are not the architect's sheets — the same
   honesty `render/markup.py` already applies to a plotted DWG.

Ask which, or both. Do not pick silently.

**And in companion mode, independence is the trap.** Sheets exported from a
Revit model are views of that model. A door width on the door schedule and the
same door's `OverallWidth` in the IFC come from one Revit parameter. Two
readers agreeing on it is *one* reading, exactly as the layout reader and the
CAD reader agreeing on one ATTRIB is one reading (`factstore.independent`).
Agreement between model and sheets must not earn HIGH confidence by itself.
Disagreement is still informative: a hand-overridden schedule cell, or a stale
export.

## 3. How model data becomes claims

Model values are claims, never findings — CLAUDE.md "AI reads; rules decide" is
about a model reading a sheet, and the same line holds for a building model:
the rules decide.

- **Method `ifc`**, added to `factstore` beside `cad`. Each claim names the
  element's `GlobalId`, its IFC class, the property set and property, and the
  storey and space it is in. That is the "check it in thirty seconds"
  provenance: an IFC GUID can be searched in any IFC viewer.
- **Basis is per property, and decided in a hand-reviewed table**, not by code:
  - typed by a person — `Pset_DoorCommon.FireRating`,
    `Pset_SpaceOccupancyRequirements.OccupancyType`, `Pset_BuildingCommon.
    SprinklerProtection` — is **stated**;
  - computed by the exporter from geometry — every `Qto_` quantity, and a
    storey count derived from `IfcBuildingStorey` — is **measured** or
    **inferred**, and never goes under a catalog key a rule reads as stated.
    It goes to the inference ladder's measured rung
    (`docs/FEATURE-PROMPT-inference-ladder.md`), as the CAD adapter's measured
    areas do.
- **`OverallWidth` is not clear width.** FBC egress checks need the clear
  opening; IFC's `OverallWidth` is the door's overall size. Map it to its own
  field. A rule that needs clear width must not read it, and an estimate of clear
  width from it is inferred, labelled, and a rung of its own.
- **Values parse through the catalog's parsers.** A fire rating typed as
  `"20 MIN"`, `"1 HR"` or `"90"` goes through the same parser as one printed on a
  door schedule, with the same refusals.
- **Units** from the model's `IfcUnitAssignment`, via
  `calculate_unit_scale`; a model with none is an abstention, never an
  assumed millimetre.
- **Absent is not false.** A model whose doors carry no `Pset_DoorCommon` says
  nothing about their ratings. The abstention says the model does not carry it,
  which is different from the set not stating it.

The mapping table (IFC class, property set, property → catalog field, basis) is
data, reviewed by hand like `read/catalog.py`, and it is the place to add a
mapping. It is not the code corpus and must not hold a threshold.

## 4. Phases

**Phase 0 — a real export, measured.** Get the IFC export of a reference set
whose sheets the engine already reviews, from the owner, exported with the
office's usual settings. Record: schema, file size, the exporter's name and
version from the header, read time and peak memory with IfcOpenShell, and for
each property in §1 how many elements carry it. Nothing is built before this:
the template says what may be there, the export says what is. The model stays
in `samples/`, git-ignored (`.gitignore` already ignores `*.ifc` and `*.ifczip`).

**Phase 1 — ingest.** Recognise IFC by its header, at admission, as `source.py`
recognises a DWG. Read it in a subprocess (`python -m fbcreview.ifc read SOURCE
WORKDIR`), the way `webapp/cadjob.py` runs the CAD adapter, with a timeout and a
concurrency limit, because a large model's memory should be returned when the
read ends. Write an `ifc.json` sidecar of plain data — no IfcOpenShell objects —
holding elements, their containers, and the mapped properties. Logs carry counts
only.

**Phase 2 — claims.** `fbcreview/read/ifc.py` turns the sidecar into claims
through the mapping table, as `read/cad.py` does for a drawing. Evidence notes
name the GlobalId and the property. Source-aware independence covers model and
sheet readings of one Revit parameter.

**Phase 3 — the mode the owner chose** (§2). Companion mode: a second upload slot,
reconciliation against the sheets, model-versus-sheet conflicts. Model-only
mode: storey plans as pages, and a report that says what they are.

**Phase 4 — output.** `findings.json` evidence with method `ifc` and the
GlobalId. Whether to also return the review as **BCF** (BIM Collaboration
Format, buildingSMART's issue format that model authoring tools can import, each
issue tied to element GUIDs) — the model-world counterpart of the marked-up DXF
— is a decision for the owner, not a default.

## 5. Tests

- Build every model in code with IfcOpenShell (`ifcopenshell.file(schema=...)`
  and `ifcopenshell.api`), never from a client file.
- A door with `Pset_DoorCommon.FireRating = "20 MIN"` gives one claim: method
  `ifc`, basis stated, the GlobalId in its evidence, the parsed value.
- A space's `Qto_SpaceBaseQuantities.NetFloorArea` never reaches a key a rule
  reads as stated.
- `OverallWidth` never satisfies a clear-width input.
- A model in millimetres and one in feet give the same values in feet; a model
  with no units abstains.
- IFC2X3 and IFC4 versions of the same model give the same claims.
- Companion mode: a sheet and the model agreeing on one door is not two
  independent readings; disagreeing, it is a conflict.
- A malformed file, an oversized file and a read past the timeout are typed
  errors, and the review of the sheets still completes.
- `fbcreview/rules` and `fbcreview/codes` never import `ifcopenshell` (extend
  the import-graph test in `tests/test_ai_guardrails.py` by adding a new test,
  not by editing that one).
- The PDF review with no IFC uploaded is byte-identical to before — the same
  gate `build_facts(cad=None)` holds.

## 6. What must survive

The five properties in `CLAUDE.md`, each with its IFC failure mode:

1. **Abstention stays honest.** A property the model does not carry is not
   checked, and says so — never "passed".
2. **Every value carries its provenance** — the GlobalId, the property set, the
   storey.
3. **An inferred value never masquerades as a stated one.** Quantities are
   computed; storey counts are derived; overall width is not clear width.
4. **The code corpus stays hand-transcribed.** The model supplies values, never
   thresholds or citations.
5. **The regression gate holds.** Every existing test passes unchanged.

## 7. Do not

- Do not read `.rvt`, and do not call Autodesk Platform Services or any other
  service with a client's model.
- Do not parse IFC with regular expressions. Use IfcOpenShell.
- Do not modify or vendor IfcOpenShell. Install the pinned wheel; its LGPL
  terms are then the ordinary ones for a library used unmodified. If the image
  is ever distributed, its source offer sits beside LibreDWG's
  (`docs/DEPLOYMENT.md` §9a).
- Do not treat a model value as more independent than it is.
- Do not commit a client model, or anything derived from one.
- Do not make the IFC reader a dependency of a PDF or DWG review: imported
  lazily, run in its own process, and absent without breaking anything.

## 8. Report back with

- The Phase 0 measurements, per property.
- The mapping table as built, with the basis of every row and why.
- Which mode the owner chose, and the conflict cases found on the reference set.
- Scorecard before and after, and the full test count.
- What a Revit export did not carry that the review needed, so the office can be
  told which export settings or parameters to fill in.

---

## Sources

- IfcOpenShell on PyPI — licence LGPLv3+, 0.9.0 released 2026-09-29, Python
  3.10–3.15: <https://pypi.org/project/ifcopenshell/>
- IfcOpenShell documentation, *Hello world* (`open`, `by_type`,
  `util.element.get_psets`):
  <https://docs.ifcopenshell.org/ifcopenshell-python/hello_world.html>
- Autodesk, *About Revit and IFC* (Revit 2023) — IFC4, IFC2x3, IFC2x2 export;
  the exporter is open source and updated through the App Store:
  <https://help.autodesk.com/cloudhelp/2023/ENU/Revit-DocumentPresent/files/GUID-6708CFD6-0AD7-461F-ADE8-6527423EC895.htm>
- Autodesk, *Export to DWG or DXF* (Revit 2023):
  <https://help.autodesk.com/cloudhelp/2023/ENU/Revit-DocumentPresent/files/GUID-42C75024-4D71-4831-8910-2747168624A3.htm>
- Autodesk Platform Services pricing: <https://aps.autodesk.com/pricing>
- buildingSMART IFC 4.3, `Pset_DoorCommon`:
  <https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/Pset_DoorCommon.htm>.
  That site refused automated fetches on 2026-10-03, so the property names in
  §1 were checked against the IFC2X3 and IFC4 templates IfcOpenShell 0.9.0
  bundles, read with `ifcopenshell.util.pset.get_template`, and against
  IfcOpenShell's IFC4 schema for `IfcDoor`'s attributes.
