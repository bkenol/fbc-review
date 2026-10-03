# docs/

| File | What it is |
| --- | --- |
| `ENGINE-TEARDOWN.md` | The engine as it stood on 2026-09-27, taken apart and measured against the real Sculpted set: data flow, fact model, rule and code corpus, output contract, and the ten reasons it reproduced 4 of 14 findings. Diagrams throughout. |
| `ARCHITECTURE-V2.md` | **The architecture in force.** AI reads, rules decide: layout layer, field catalog, deterministic and AI readers, grounding verifier, fact store, and the CAD adapter as built (§5). |
| `CAD-INPUT.md` | Reviewing a drawing instead of a PDF: what can be uploaded (`.dwg` R13–2018 format, `.dxf`, a `.zip` with xrefs), what the review reads from a drawing that a plot cannot carry, what comes back (the marked-up PDF and a marked-up DXF), measured timings, limitations, troubleshooting, and what Revit users do today. |
| `DEPLOYMENT-PROMPT.md` | The kickoff brief. Six phases, with rationale for the non-obvious choices and an explicit do-not list. |
| `DEPLOYMENT.md` | The runbook, written during the build. §9a records the 2026-10-03 converter decision (LibreDWG) and what its GPL licence asks if the image is ever distributed. |
| `STRUCT-PLAN.md` | The structural consult module mapped onto the code as it is: conflicts found (C1–C13), owner decisions D1–D12 with recommendations, phases against files, corpus status. **Phases 2+ wait on its §3.** |
| `TRAINING-MODE.md` | How client feedback changes what the reviewer reports — the taxonomy, the calibration overlay, the triage, and the five properties that keep it safe. Built. |
| `reference/` | Review findings from the two test permit sets. Context for what the output is meant to look like and where the engine's limits are. |

`../ARCHITECTURE.md` is the original three-tier reasoning, kept as written;
`ARCHITECTURE-V2.md` supersedes it. `../CLAUDE.md` carries the standing rules.

## Feature prompts

| File | What it is |
| --- | --- |
| `FEATURE-PROMPT-declaration.md` | The Project Declaration questionnaire — 12 optional fields answered before upload, reconciled against the drawings as a second independent source. Build after deployment, or in parallel if the engine work is separable from the infrastructure work. |
| `FEATURE-PROMPT-inference-ladder.md` | **The standing plan for the engine.** Why "neither the drawings nor the declaration state this" is often false, and six phases to fix it: a resolution ladder from stated → tabulated → measured, lexicon-driven extraction instead of literal patterns, per-view scale attribution, and tracing a building footprint off vector geometry with no CAD layers. Build in the order §11 gives. |
| `FEATURE-PROMPT-revit-ifc.md` | Reading a Revit model through its IFC export with IfcOpenShell: what IFC carries (door widths and fire ratings, room areas, occupancy), how it maps onto claims with method `ifc`, and the design question a model with no sheets raises. A separate task from the DWG work, by the owner's decision of 2026-10-03. |
| `FEATURE-PROMPT-cad-region-plugin.md` | Reviewing a selected region: in the web viewer first (any upload), then from inside AutoCAD with a plugin that sends a picked window and brings the findings back as `FBC-REVIEW` layers. A plan with the decisions it needs, not code. |
| `FEATURE-PROMPT-structural-consult.md` | A structural consult module beside the reviewer: requesters ask structural questions, skills answer from a hand-verified corpus, public hazard data and deterministic engines, and a licensed engineer approves the one final package. Also the `STRUCT.WIND_STANDARD` 9th Edition fix. Read `STRUCT-PLAN.md` with it. |

Training mode generates its own prompts. An escalated piece of feedback exports
from the queue on `/refine` as a Markdown brief in the same shape as the files above — see
`TRAINING-MODE.md` §6.

Order: `DEPLOYMENT-PROMPT.md` first. The declaration feature touches `webapp/`, the Angular
app and the rule corpus, all of which are easier to change once there is a deploy pipeline
and a regression gate running in CI.
