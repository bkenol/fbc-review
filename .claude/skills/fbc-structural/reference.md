# fbc-structural — full registries

Source of truth: `docs/FEATURE-PROMPT-structural-consult.md` (§4, §6, §8). Repo-specific
adjustments: `docs/STRUCT-PLAN.md`. If this file and the brief disagree, the brief wins and this
file is stale — fix it.

## Encodings

| Encoding | What | Where | Who fills it |
| --- | --- | --- | --- |
| CORPUS | hand-transcribed code rows with per-row provenance | `fbcreview/codes/` | a person from licensed text; a licensed engineer verifies structural rows |
| DATA | public datasets with source URL, retrieval date, SHA-256 | `hazards/` (D4 for large files) | scripts; owner confirms the source |
| LIBRARY | engineer practice material | Firestore + bucket, or `.devdata/` on the filesystem backend — never git | engineers |
| EXEMPLAR | approved packages promoted to drafting examples; rejections kept with reason codes | store only — never git | owner promotes, never automatic |

## Knowledge registry

| ID | Knowledge | Source | Enc. | Used by |
| --- | --- | --- | --- | --- |
| KN-01 | editions, effective dates, ASCE 7 per edition | `fbcreview/codes/editions.py`; FBC adoption schedule | CORPUS | all |
| KN-02 | risk category by use and occupant load | FBC-B Table 1604.5 | CORPUS | SK-02, 03, 06 |
| KN-03 | structural data on construction documents | FBC-B 1603.1–1603.1.9 per edition | CORPUS | SK-03 |
| KN-04 | Vult, RC I–IV | FGDL `WINDZONES_CAT1..CAT4_ASCE7_22_JUN21` | DATA | SK-01, 03, 06 |
| KN-05 | Vult → Vasd | FBC-B 1609.3.1, edition-keyed | CORPUS | SK-03 |
| KN-06 | HPR, WBDR, opening protection | FBC-B 202, 1609.2; ASCE 7-22 26.2, 26.12.3 | CORPUS + DATA | SK-01, 05 |
| KN-07 | HVHZ | FBC-B HVHZ sections (verify numbering); TAS; approval types per jurisdiction | CORPUS | SK-01, 05 |
| KN-08 | product approval | F.S. 553.842; Rule 61G20-3; ASD factor 0.6 | CORPUS | SK-05 |
| KN-09 | threshold building | F.S. 553.71; F.S. 553.79(5) | CORPUS | SK-04 |
| KN-10 | special inspections | FBC-B Chapter 17 subset | CORPUS | SK-04 |
| KN-11 | flood | FBC-B 1612, ASCE 24; NFHL `FLD_ZONE`, `SFHA_TF`, `STATIC_BFE`, `V_DATUM`, `FIRM_PAN`; datum conversion from a published service, never a constant | DATA + CORPUS | SK-01, 07 |
| KN-12 | exposure categories, upwind fetch | ASCE 7-22 26.7 | CORPUS | SK-01, 06 |
| KN-13 | ASCE 7-22 C&C coefficients, h ≤ 60 ft | Tables 26.6-1, 26.10-1, 26.11-1, 26.8, 26.9-1, 26.13-1; Figs 30.3-1, 30.3-2A–G | CORPUS (licensed, D9) | SK-06, 08 |
| KN-14 | load combinations | FBC-B 1605; ASCE 7-22 Ch. 2 | CORPUS | SK-08+ |
| KN-15 | EOR and delegated engineering | Rule 61G15-31 (.001–.011) | CORPUS | SK-10 |
| KN-16 | chickee exemption | F.S. 553.73(10)(i) as amended 2026 (HB 929), from the chaptered law | CORPUS | SK-01, 08 |
| KN-17 | existing buildings | FBC-EB structural triggers | CORPUS | SK-09 |
| KN-18 | referenced standard editions | FBC-B Chapter 35 per edition | CORPUS | all design answers |
| KN-19 | practice library | engineers | LIBRARY | all |
| KN-20 | exemplars | approved packages | EXEMPLAR | AI drafting |

## Skill registry

| Key | Skill | Wave | Out of scope by default |
| --- | --- | --- | --- |
| SK-01 | Site Structural Brief | v1 | — |
| SK-02 | Risk Category Determination | v1 | — |
| SK-03 | Wind Design Data Block (1603.1.4) | v1 | — |
| SK-04 | Threshold and Special Inspection Screen | v1 | — |
| SK-05 | Product Approval Requirements and Design-Pressure Check | v1 | — |
| SK-06 | C&C wind pressure table, ASCE 7-22 Ch. 30 Pt 1 | v2 | h > 60 ft, open buildings, untranscribed roof shapes, topographic Kzt, irregular plans |
| SK-07 | Flood design data by zone | v2 | coastal site-specific analysis |
| SK-09 | Existing-building structural triggers | v2 | existing-member capacity |
| SK-10 | Delegated engineering coordination | v2 | design of the delegated system |
| SK-08 | Small-structure uplift screen | v3 | connection design is always EOR work |
| SK-11 | Member checks | v3 | D7 |

Module-wide out of scope: anything needing a site visit, inspection of existing conditions, or the
capacity of an existing member → "needs engineer-authored response".

Lifecycle: draft → validated → live. Only live skills appear in the question-type picker.

## `SkillSpec`

`key, title, version, required_facts, optional_facts, knowledge (KN ids), engine, drivers,
out_of_scope, status ("draft" | "validated" | "retired")`.

## Result package

`request_id, skill_key, skill_version, scope_flag, question, answer_structured, answer_text,
drivers (top 3–5 by magnitude), unverified_inputs, citations, derivation_ref, content_hash`
(SHA-256 of canonical JSON of every other field).

`Driver`: `key, chosen, alternative, effect, base_value, alt_value,
magnitude = |alt − base| / |base|`.

Arithmetic vectors (fixture coefficients, never corpus): V = 170, Kh = Kzt = Ke = 1.0 →
qh = 73.984 psf; Kd = 0.85, GCpi 0.18 → 0.55 → 23.268 psf strength, 13.961 psf ASD (± 0.01).

## Endpoints

| Method | Path | Who |
| --- | --- | --- |
| POST | `/api/struct/requests` | requester |
| GET | `/api/struct/requests/{id}` | requester (own) |
| POST | `/api/struct/requests/{id}/answers` | requester — follow-up answers |
| GET | `/api/struct/queue` | engineer |
| POST | `/api/struct/results/{id}/decision` `{decision, reason_code, note, content_hash}` | engineer |
| GET | `/api/struct/results/{id}` | requester — released only |

Plus an owner-only engineer-profile endpoint (STRUCT-PLAN C5). Regenerate `openapi.json` and the
generated client in `web/src/app/api/` after adding routes.

## Rejection reason codes → triage

| Code | Disposition |
| --- | --- |
| `WRONG_ASSUMPTION` | needs_component |
| `WRONG_CODE_BASIS` | escalate (citations never auto-tune) |
| `CALC_ERROR` | needs_component |
| `OUT_OF_SCOPE_NEEDS_ENGINEER` | escalate |
| `INSUFFICIENT_INFO` | needs_component (an intake question is missing) |
| `OTHER` (note required) | escalate |

## Wilson vectors (± 1e-4)

(98,100) 0.9300 · (49,50) 0.8950 · (50,50) 0.9286 · (30,30) 0.8865 · (100,100) 0.9630.
Zero misses: LB ≥ 0.90 needs n ≥ 35; LB ≥ 0.95 needs n ≥ 73.
