---
title: Claude Code Prompt — Review a selected region, from the viewer and from AutoCAD
type: runbook
tags:
  - code-review
  - florida-building-code
  - cad
  - viewer
status: draft
created: 2026-10-03
source-session: CAD adapter build (DWG/DXF, 2026-10-03)
---

# Claude Code prompt — select a region of a drawing and review it there

Open Claude Code in the repository and paste everything below the line.
`CLAUDE.md` carries the standing rules and outranks anything here that
contradicts it. This is a plan, not a specification: §3 lists the decisions to
put to the owner before any code is written.

---

## The idea, and the order to build it in

The owner's idea, 2026-10-03: *select a region inside the drawing and run the
analysis there.* A drafter working on one stair, one tenant space or one door
schedule wants the findings that concern it, not the whole set's.

Build it in two steps, and in this order:

1. **Region selection in the web viewer.** It works for every upload — a PDF,
   and a DWG or DXF, which the CAD adapter plots to the same PDF the viewer
   already shows (`docs/ARCHITECTURE-V2.md` §5). The API gains one optional
   input, a region, and nothing about the engine needs a CAD-specific path.
2. **An AutoCAD plugin** that sends the open drawing and a window picked in it
   to the same API, waits, and brings the findings back as the `FBC-REVIEW`
   layers the marked-up DXF already carries. It reuses step 1's region end to
   end; its only new problem is translating "this window on this layout" into
   the viewer's coordinates, and the server already holds that mapping.

The viewer comes first because it is reachable by every user, needs no
installation and no per-AutoCAD-version build, and settles every semantic
question (§2) that the plugin would otherwise settle by accident.

## 1. What already exists to build on

- **One coordinate space for "where".** `findings.json`'s `rect` is in pdf.js
  viewport space at scale 1: points, origin top-left, rotation applied, on a
  0-based `page` (`docs/ARCHITECTURE-V2.md` §3.8; the client converts the page
  base in one place, `viewerPage()` in `web/src/app/viewer/findings.ts`). A
  region in the same space can be tested against every finding's `rect` and
  every claim's evidence box without conversion.
- **A drawing's pages know their layouts.** For a set plotted from a DWG or
  DXF, `cad.json` records for every page its drawing, its layout name and
  `to_page`, the affine map from the layout's paper coordinates to the page's
  points, and for each viewport its own map from model space
  (`fbcreview/cad/render.py`, `SheetPage`). `fbcreview/cad/markup.py` already
  inverts `to_page` to put a finding back on its layout. The plugin's
  translation is the same arithmetic, the other way round.
- **Re-runs are cheap.** `POST /api/jobs/{job_id}/rerun` reviews the stored
  upload again; a drawing's re-run re-plots from the first review's upload, and
  stored AI readings replay with no model call. A region review is naturally a
  re-run of a review that already exists.

## 2. What "run the analysis there" has to mean

This is the part to get right, because the obvious version is wrong.

**Wrong: read only the facts inside the region and run the rules on them.**
Most rules need building-wide facts that are printed nowhere near the stair a
user selected: occupancy group, construction type, sprinkler system, the code
edition — all on the G-sheets. Run on a region alone, those rules would abstain
on everything, or worse, a rule that totals something (areas, occupant load,
plumbing fixtures) would total a fraction and report it. That turns "not in the
region" into a wrong number, which `CLAUDE.md`'s first refinement property
forbids: *"Not checked" must never become indistinguishable from "checked and
passed".*

**Right: the facts come from the whole set; the region scopes what is
reported.** The review reads every sheet, as now. The region then selects:

- findings whose `rect` intersects it, and findings whose evidence was read
  inside it (a finding about a door in the region, whose citation lives on the
  code sheet, is in);
- abstentions whose rule would have looked inside it.

Everything else is not shown, and the report says so in numbers: *"Region
review of sheet A-201, 12 × 8 in at (…). 4 findings concern it. 31 findings
elsewhere in the set are not shown. Building-wide facts (occupancy A-3,
sprinklered NFPA 13) were read from the whole set."* A region review never
says or implies that the rest of the set passed.

The cost saving is real but secondary: with AI reading on, a region review that
is a re-run replays stored readings and costs nothing; a first review of a
region still reads every sheet, because the building-wide facts need it.

## 3. Decisions for the owner, before code

1. Region semantics as in §2 (facts from the set, reporting scoped) — or is
   there a case for a narrower read? If so, which rules may run on a region's
   facts alone, and how does each say what it did not see?
2. One region or several per request? On one sheet only, or one per sheet?
3. Is a region review its own job in the history (a re-run with `region` set),
   or a filter the client applies to an existing review with no new job? The
   second is cheaper and needs no API change for the viewer, but the plugin
   needs a job to download a DXF from.
4. The plugin: AutoCAD only, or AutoCAD LT as well (§5 — it decides the
   language)? Which AutoCAD versions?
5. How the plugin signs in (§5).

## 4. Step 1 — the viewer

**API.** `POST /api/review` and `POST /api/jobs/{job_id}/rerun` accept an
optional `region`:

```json
{"region": {"page": 7, "rect": [412.0, 188.5, 1276.0, 760.0]}}
```

`page` 0-based, `rect` `[x0, y0, x1, y1]` in the `findings.json` space. Validated
server-side against the page count and the page box; a degenerate or
off-page rect is a 422, never a silently empty review. The job record keeps the
region, `findings.json` carries it at the top level, and the summary counts
what was in and out. The OpenAPI schema and the generated client change, so
`npm run api:refresh` and both outputs are committed — CI's drift job fails
otherwise.

**Engine.** No rule changes. A pure function after `run_all` (and after the AI
result review and calibration, which keep their order) partitions findings and
abstentions by the region, using `rect`, `Finding.box` and evidence boxes. It
lives beside `fbcreview/payload.py`, not in `fbcreview/rules`, and never
removes a finding from the stored full result — the region is a view of a
complete review.

**Client.** A "Review a region" tool on the sheet viewer: drag a rectangle on
one page, confirm, and the job is re-run with the region (or filtered, per
§3.3). Signals for the selection state, no new libraries, the restraint
`CLAUDE.md` asks of the Angular app. The region is drawn on the sheet while its
findings are shown, so it is always clear that a subset is on screen.

**Marked-up PDF and DXF.** The region is outlined on its sheet, and the
register's header carries §2's sentence with the counts.

**Tests.** A finding inside, one outside, one straddling the edge, one with no
`rect` but evidence inside; a building-wide fact read outside the region still
reaching a rule; the counts reconcile (`in + out == total`); a region on a
rotated page; a 422 for an off-page rect; the full result unchanged by a region.

## 5. Step 2 — the AutoCAD plugin

**What it does.**

1. The user runs a command, say `FBCREVIEW`, and picks a window on the current
   layout (or in model space).
2. The plugin saves the drawing if needed and uploads the DWG — with its xrefs,
   zipped, if it has any — together with the layout name and the window's two
   corners in that space's drawing coordinates:

   ```json
   {"region": {"layout": "A-201", "space": "paper",
               "window": [2.5, 3.0, 14.5, 11.0], "drawing": "A-201.dwg"}}
   ```

3. The server ingests the drawing as any DWG upload, then finds the page whose
   `layout` (and `drawing`, for a zip) matches and maps the window to
   `{page, rect}` through that page's `to_page` — or, for a window picked in
   model space, through the viewport that shows it. From there it is step 1.
   The mapping lives on the server, beside the sidecar it reads; the plugin
   never computes page coordinates.
4. The plugin polls the job, downloads `markup-dxf.zip`, and brings the
   `FBC-REVIEW`, `FBC-REVIEW-VERIFIED` and `FBC-REVIEW-TEXT` layers into the
   open drawing — attaching the returned DXF as an overlay xref is the least
   invasive way, and keeps the review out of the drafter's own entities. It
   lists the region's findings in a palette, each zooming to its cloud.

**Language: .NET for the plugin, not AutoLISP alone.** Uploading a 23 MB file
as multipart form data and polling a JSON API is ordinary work for .NET's
`HttpClient` and awkward in AutoLISP. AutoLISP has run in AutoCAD LT since LT
2024, but LT does not support `vlax-create-object` and its relatives
(Autodesk's LT AutoLISP reference), which is how AutoLISP reaches an HTTP client on
Windows — so an LT version would need a separate small helper program. Decide
LT in or out (§3.4) before choosing.

**Per-version builds.** AutoCAD 2025 moved from .NET Framework 4.8 to .NET 8,
and a .NET Framework plugin must be migrated and rebuilt to load in it
(Autodesk's developer blog, *AutoCAD 2025 .NET 8 migration*). Supporting 2024
and 2025+ means two builds — Autodesk describes multi-targeting one project for
.NET 4.8 and .NET 8 — and each new AutoCAD release is a test run, sometimes a
rebuild.

**Packaging.** Autodesk's Autoloader format: a `.bundle` folder with a
`PackageContents.xml` at its root that says which AutoCAD versions
(`SeriesMin`/`SeriesMax`) load which build, installed under
`ApplicationPlugins` (all users under Program Files, one user under AppData).
That is also the format the Autodesk App Store distributes, with its own review
and listing process — decide whether to list it publicly or hand the bundle to
named offices.

**Signing in.** The API accepts a Firebase ID token and checks the email
allowlist server-side, and nothing about that changes. A desktop plugin gets a
token the way installed applications do: open the system browser for Google
sign-in, receive the result on a loopback address, keep the refresh token in
the user's Windows credential store, and refresh the one-hour ID token as
needed. Never a service-account key, never an API key baked into the bundle —
`CLAUDE.md`'s security rules apply to a plugin as to CI. Verify the current
Firebase and Google OAuth guidance for installed apps before writing it; those
pages move.

**What it sends.** The whole drawing, as the web upload does, under the same
retention. The plugin's install notes must say so in a sentence: the drawing
leaves the workstation for the deployment, and, when AI sheet reading is on,
plotted sheets go to the Anthropic API exactly as for a web upload.

**Tests.** The server half is ordinary pytest: a DXF built in code with ezdxf
with two layouts and a viewport, a window on each, mapped to `{page, rect}` and
compared with the finding `rect`s the plot produced; a model-space window
through a viewport at 1:96; a layout name that does not exist is a 422 naming
the layouts that do. The plugin half needs AutoCAD, which CI does not have:
keep its logic thin (pick, upload, poll, attach), unit-test the HTTP and the
JSON shapes outside AutoCAD, and write down a manual test script per supported
version.

## 6. Do not

- Do not run the rules on a region's facts alone (§2).
- Do not let a region review read as "the rest passed". Counts, every time.
- Do not compute page coordinates in the plugin. The server owns the mapping.
- Do not put credentials in the bundle.
- Do not change a rule, the code corpus, or the full stored result to serve a
  region.

## 7. Report back with

- The owner's answers to §3.
- Region-review counts on the reference sets for three regions each: one with
  findings, one without, one straddling a sheet edge.
- For the plugin: the AutoCAD versions it was run in by hand, and what the
  manual test script found.

---

## Sources

- Autodesk developer blog, *AutoCAD 2025 .NET 8 migration*:
  <https://blog.autodesk.io/autocad-2025-dotnet8-migration/>
- Autodesk developer blog, *Multi-targeting AutoCAD .NET plugin for .NET 4.8 and
  .NET 8.0*:
  <https://blog.autodesk.io/multi-targeting-autocad-net-plugin-for-net-48-and-net-80/>
- Autodesk, *What's New or Changed with AutoLISP* (AutoCAD LT 2025) — lists
  `vlax-create-object`, `vlax-get-object` and `vlax-get-or-create-object` as not
  supported in LT:
  <https://help.autodesk.com/cloudhelp/2025/ENU/AutoCAD-LT-AutoLISP/files/GUID-037BF4D4-755E-4A5C-8136-80E85CCEDF3E.htm>
- Autodesk, *How to use AutoCAD LT and AutoLISP* (AutoLISP arrives in LT 2024):
  <https://www.autodesk.com/blogs/autocad/autocad-lt-2024-autolisp/>
- Autodesk University, *An in-depth look at the Autodesk Autoloader module*
  (bundle format, `PackageContents.xml`, `ApplicationPlugins`):
  <https://static.au-uw2-stg.autodesk.com/DV2142_handout_2142_an_20in-depth_20look_20at_20the_20autodesk_20autoloader_20module.pdf>
