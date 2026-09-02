# docs/

| File | What it is |
| --- | --- |
| `DEPLOYMENT-PROMPT.md` | The kickoff brief. Six phases, with rationale for the non-obvious choices and an explicit do-not list. |
| `DEPLOYMENT.md` | The runbook, written during the build. Starts as a stub. |
| `TRAINING-MODE.md` | How client feedback changes what the reviewer reports — the taxonomy, the calibration overlay, the triage, and the five properties that keep it safe. Built. |
| `reference/` | Review findings from the two test permit sets. Context for what the output is meant to look like and where the engine's limits are. |

`../ARCHITECTURE.md` covers the engine's three-tier design and why the review path is
deterministic. `../CLAUDE.md` carries the standing rules.

## Feature prompts

| File | What it is |
| --- | --- |
| `FEATURE-PROMPT-declaration.md` | The Project Declaration questionnaire — 12 optional fields answered before upload, reconciled against the drawings as a second independent source. Build after deployment, or in parallel if the engine work is separable from the infrastructure work. |

Training mode generates its own prompts. An escalated piece of feedback exports
from the queue on `/refine` as a Markdown brief in the same shape as the files above — see
`TRAINING-MODE.md` §6.

Order: `DEPLOYMENT-PROMPT.md` first. The declaration feature touches `webapp/`, the Angular
app and the rule corpus, all of which are easier to change once there is a deploy pipeline
and a regression gate running in CI.
