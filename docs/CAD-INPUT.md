---
title: Reviewing a drawing — DWG, DXF and zip uploads
type: guide
tags:
  - code-review
  - florida-building-code
  - cad
status: built
created: 2026-10-03
---

# Reviewing a drawing — DWG, DXF and zip uploads

For the person sending a permit set, and for whoever runs the service. The
review can start from the drawing itself instead of a PDF plot of it. It reads
more that way, and it gives back the review drawn into the drawing as well as
the usual report.

How it is built is `ARCHITECTURE-V2.md` §5. What it costs to run, and the
licence of the converter, are `DEPLOYMENT.md` §9a. Every timing below was
measured on one real drawing, an AutoCAD 2018-format precast set of eight
sheets (23 MB as DWG). Treat them as that drawing's numbers, not a promise
for every drawing.

---

## 1. What you can upload

| Upload | Accepted | Notes |
| --- | --- | --- |
| `.dwg` | The R13 format through the 2018 format (AutoCAD R13, R14, 2000, 2004, 2007, 2010, 2013, 2018). AutoCAD 2018 through at least 2025 save in the 2018 format by default. | Older formats (R12 and before) are refused with the version named: open the file in AutoCAD and save it as 2000 or later. A format newer than 2018, if one appears, is refused with the same kind of message: save as 2018 DWG, or export DXF. |
| `.dxf` | ASCII or binary DXF | The bulky form: the 23 MB DWG above is a 170 MB DXF. Zip it before uploading. |
| `.zip` | A zip of `.dwg` and/or `.dxf` files | The way to send sheets that reference a base plan as an xref: put the xref drawings in the same zip. Files that are not drawings (fonts, plot styles, PDFs) are skipped. |
| `.pdf` | As before | Nothing about a PDF review changes. |

The file name proves nothing; the service decides what a file is from its first
bytes. A DWG renamed `.pdf` is reviewed as a DWG.

**Limits.**

| Limit | Value | Where it is set |
| --- | --- | --- |
| Upload size | 120 MB (95 MB on the Cloudflare tunnel deployment) | `FBC_MAX_UPLOAD_MB` |
| Sheets | 300 plotted sheets per upload | `FBC_MAX_PAGES` — a drawing's sheets are its layouts |
| Zip contents | 400 files, 120 drawings, 1 GB unpacked, and no member compressed more than 200:1 | `fbcreview/cad/source.py` |
| DXF held for one review | 300 MB in all (`FBC_CAD_MAX_DXF_MB`), measured at about 6.6× that in memory — counting each DWG at its converted size as it converts, and each xref copy embedding makes into a sheet file. Past it the job fails `payload_too_large` (the adapter's own code is `drawing_too_large`) with the size in its message, before ezdxf reads anything | `fbcreview/cad/__init__.py` |
| Entities one drawing expands to | 2 000 000 through its blocks and block arrays (`FBC_CAD_MAX_ENTITIES`); the reference drawing expands to 304 147. Counted before anything is drawn; past it a single drawing is refused (`payload_too_large`) and a zip member is left out with a warning | `fbcreview/cad/read.py` |
| Time | 600 s to read and plot, 600 s to write the marked-up DXF | `FBC_CAD_TIMEOUT_S` |

A zip is refused outright, before anything is unpacked, if a member's path
would land outside the folder it unpacks into, if a member is encrypted, or if
its sizes do not add up — those are the shapes a zip takes when it is an attack
rather than a drawing set.

**Which sheets get reviewed.** Every paper-space layout — the tabs along the
bottom of AutoCAD — that has something drawn on it, in tab order. The default
`Layout1` nobody touched is not a sheet. A drawing with no such layouts is
plotted from model space as a single sheet, fitted to ARCH D (36 × 24 in), and
the review says so. If the upload has more layouts than this permit needs,
delete the others or send a copy with only these sheets.

---

## 2. What the review reads from a drawing that a PDF cannot carry

A PDF plot is ink. The drawing behind it knows what each piece of ink is.

| From the drawing | What it changes |
| --- | --- |
| **Every string exactly as typed** — text, multiline text, block attributes, dimension text, notes in model space seen through a viewport | No OCR, no misread characters, no text lost because the plot drew it as outlines. Each string is placed on the plotted sheet where the drawing puts it, as an invisible text layer the readers and the viewer search. |
| **Title-block fields** | The sheet number is read from the attribute whose name says it is the sheet number (`SHEET_NO`, `DWG No.`), not guessed from a corner of the sheet. A layout tab's name is never used as the sheet number. |
| **Block attributes that name a fact** | An attribute tagged, for example, `OCCUPANCY_GROUP` is read as that field, and the finding says the value came from the drawing, naming the block and the layout. |
| **Each viewport's exact scale** | The scale of each view is computed from the viewport itself, not read off a printed "1/8" = 1'-0"" label. A sheet with views at several scales has no single sheet scale, and the review says so rather than picking one. |
| **The real layer table** | Rules that select geometry by layer name see the drafter's own layer names, which a PDF keeps only if it was exported with layer information. |
| **Dimensions and room outlines** | Recorded — each dimension's printed text beside the length it measures, each closed outline on an area or room layer with its measured area — and labelled measured. **No rule uses them yet** (§5). |

What does not change: the rules, the code corpus and how a finding is decided.
A drawing gives the same rules better inputs, each marked with where it came
from. If the deployment has AI sheet reading on, the AI reader is shown the
plotted sheets, as it would be shown a PDF's.

---

## 3. What you get back

| Output | What it is |
| --- | --- |
| **Marked-up PDF report** | The sheets, plotted by the review from your drawing, with the findings marked and the register. It says plainly that the sheets were plotted from the DWG or DXF by this review, and that the drawing file, not the plot, is the document of record. Fonts are substituted (§5), so it looks like your plot, not identical to it. |
| **`findings.json`** | The findings register, exactly as for a PDF review. A value read from the drawing says so in its evidence. |
| **The plotted sheets** (`source.pdf`) | What the review read; the viewer shows it, and every finding's marker points into it. |
| **Marked-up DXF** (`markup-dxf.zip`) | Your drawing, as DXF, with every finding drawn on the layout it was found on, around the thing it is about. One `… — FBC REVIEW.dxf` per drawing that has sheets, and a `READ ME.txt`. |

**In the marked-up DXF**, the review is on three layers of its own. Freeze,
plot or delete them without touching a line of the original work:

| Layer | Holds |
| --- | --- |
| `FBC-REVIEW` | revision clouds around findings that need action |
| `FBC-REVIEW-VERIFIED` | plain rectangles around checks that passed |
| `FBC-REVIEW-TEXT` | each finding's id, severity and title beside its mark, and a list of the findings that have no single place on a sheet |

A finding raised or revised by the AI result review says so on its label, as it
does in the PDF. A finding that rests on the project declaration rather than
the drawing says that too. Each outline carries the finding's identity in
XDATA under the application name `FBC_REVIEW`, so two findings with the same
id on one sheet stay two findings. Nothing is dropped: a finding the review
cannot place on a sheet is listed beside its sheet or in the `READ ME.txt`.

The DXF is a conversion of your DWG, not your DWG. Use it to see where the
findings are, and make the changes in your own file.

---

## 4. How long it takes

Measured on the reference drawing (8 sheets, 23 MB DWG, about 296 000
model-space entities):

| Step | Time |
| --- | --- |
| DWG to DXF | 4–7 s |
| Reading the DXF | about 60 s |
| Plotting the sheets | 3–15 s a sheet; about 3 minutes for the whole ingest |
| The review itself | as for a PDF of the same sheets |
| Writing the marked-up DXF | about 85 s |

So expect a drawing review to take about five minutes where the PDF of the same
set takes under one. The service reads one drawing at a time per server; a
second drawing waits for the first rather than slowing both down.

---

## 5. Known limitations

- **Fonts are substituted.** A server has none of the drafter's fonts —
  `arial.ttf`, SHX fonts like `romans.shx` — so every one is drawn in DejaVu
  Sans. On the reference drawing all 13 fonts its text styles name came out
  that way. The words are exact; their shapes and widths are not the
  original's.
- **Dimension overrides are not checked.** A dimension whose printed text
  disagrees with what it measures is recorded, not reported.
- **Measured areas are not used.** Room and area outlines are measured and
  recorded, but no rule takes a measured area yet. Rules still read the areas
  the sheets state.
- **Leader notes depend on proxy graphics.** The plotting library draws a
  multileader from the proxy graphics stored with it in the file, not from the
  leader's own definition. On the reference drawing every multileader had
  them. A drawing whose multileaders lack them would lose those notes from the
  review, and the review does not yet warn about it.
- **External references must travel with the drawing.** An xref resolves only
  against drawings uploaded in the same zip, matched by file name. One that
  was not uploaded is reported by name, and its sheets say their content is
  missing.
- **No Revit files** (`.rvt`). See §7.
- **The whole drawing is reviewed.** Selecting a region to review is planned
  (`FEATURE-PROMPT-cad-region-plugin.md`), not built.

---

## 6. Troubleshooting

| What you see | What to do |
| --- | --- |
| "could not be converted for reading" / "could not be read" | Open the drawing in AutoCAD, run `AUDIT` (answer Yes to fix errors), then `PURGE`, save, and upload again. If it still fails, export DXF (`SAVEAS`, file type DXF), zip it, and upload that. |
| "saved in an old DWG format" | Open it and save as AutoCAD 2000 or later. |
| "newer than this service reads" | Save as AutoCAD 2018 DWG (`SAVEAS`, *Files of type*), or export DXF. |
| "DWG conversion is not installed on this deployment" | That deployment has no converter (a local run without LibreDWG). Upload a DXF, zipped. |
| "plots to N sheets. The limit is …" | Remove the layouts this permit does not need, or upload a copy with only the permit sheets. |
| Sheets come out empty except for the title block | The plan is in an xref that was not uploaded. Use `ETRANSMIT` in AutoCAD, which can package the drawing with every xref it uses as one zip, and upload that zip. |
| File too large | Upload the DWG rather than a DXF; or zip the DXF (DXF text compresses about tenfold). |
| Leader notes missing from the review | The drawing's multileaders carry no proxy graphics (§5). In a copy of the drawing, `EXPLODE` the multileaders into text and lines, and upload the copy. Tell whoever runs the service, so the case can be reproduced. |
| Wrong sheet number | The review reads the sheet number from a title-block attribute whose name says it is the sheet number. A title block drawn as plain text, not attributes, falls back to reading the plotted text, as for a PDF. |
| Text in the plot looks different from your plot | Expected: fonts are substituted (§5). The words are exact. |

**For whoever runs the service.** A drawing that fails is a typed error on the
job (`unsupported_cad_version`, `corrupt_cad`, `unsafe_archive`,
`cad_unavailable`), never a crash. The logs carry counts and an outcome code,
never a file name, a layer name or any drawing text. To reproduce outside the
service:

```bash
python -m fbcreview.cad ingest drawing.dwg /tmp/work --name drawing.dwg
python run.py drawing.dwg --json findings.json --markup-dxf markup.zip
```

`FBC_DWG2DXF` names the converter if it is not on `PATH`. The rendered PDF and
`cad.json` land in the working directory. Keep both out of the repository: no
client drawing, or anything converted from one, is ever committed.

---

## 7. Revit users

Revit's own `.rvt` format is closed. Only Autodesk's software and its paid
cloud service can read it, so this service does not.

**Today:** export the sheets to DWG.

1. In Revit, *File* > *Export* > *CAD Formats* > *DWG*.
2. In the *DWG Export* dialog, under *Export*, pick *<in session view/sheet
   set>* and choose the permit sheets.
3. To get one file per sheet with its views in it, clear *Export views on
   sheets and links as external references*. Left on, Revit writes the views as
   separate DWGs that each sheet references; then upload all of them in one zip.
4. Zip the DWGs and upload the zip.

(Autodesk's help: *Export to DWG or DXF*,
<https://help.autodesk.com/cloudhelp/2023/ENU/Revit-DocumentPresent/files/GUID-42C75024-4D71-4831-8910-2747168624A3.htm>.)

**Planned:** reading Revit's IFC export directly, which carries what a sheet
only draws — door widths, fire ratings, room areas as data. That is a separate
piece of work: `FEATURE-PROMPT-revit-ifc.md`.
