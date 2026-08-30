# FBC Code Review — web service

Drag in a permit set, choose the review parameters, get back a marked-up PDF and a findings
JSON. **No model calls in the request path** — see `../ARCHITECTURE.md`.

## Run it

```bash
pip install -r requirements.txt
uvicorn webapp.server:app --reload --port 8000
# open http://127.0.0.1:8000
```

or

```bash
docker build -t fbc-review .
docker run -p 8000:8000 -v fbcdata:/data fbc-review
```

## What the settings actually change

Nothing here is decorative — each one alters the rules that run or the findings that survive.

| Setting | Effect |
| --- | --- |
| **Occupancy group** | Table lookups for common path, travel distance and dead ends. Switching A-3 → B flips the dead-end limit from 20 ft to 50 ft, because the sprinklered exception in 1020.5 lists B and does not list A. |
| **Sprinklered** | Same tables, other axis. Unchecking it takes Group A travel distance from 250 ft to 200 ft, which turns a VERIFIED item into a HIGH finding on this test set. |
| **Report findings down to** | Severity floor. `HIGH` drops the four MEDIUMs. |
| **Mark what passes** | Emits the green VERIFIED markers and the verified register. Off = failures only. |
| **Measure egress geometry** | Runs `MEASURE.EGRESS_EXTENT`. Off = the rule records an abstention saying so, rather than going quiet. |
| **Code edition** | Only the 2023 8th Edition corpus is populated. The 9th Edition (effective 31 Dec 2026) is listed and disabled rather than hidden. |

Observed on the Sculpted Hot Pilates set (24 sheets, 17.8 MB):

```
defaults (A-3, sprinklered)    -> 13 findings {CRITICAL 1, HIGH 2, MEDIUM 4, VERIFIED 5, MEASURED 1}
Group B instead of A-3         -> 13 findings {CRITICAL 1, HIGH 3, MEDIUM 5, VERIFIED 3, MEASURED 1}
non-sprinklered                -> 13 findings {CRITICAL 1, HIGH 3, MEDIUM 4, VERIFIED 4, MEASURED 1}
HIGH and above only            ->  9 findings {CRITICAL 1, HIGH 2, VERIFIED 5, MEASURED 1}
verified off, measured off     ->  7 findings {CRITICAL 1, HIGH 2, MEDIUM 4}   (2 abstentions)
```

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/` | The page |
| `GET` | `/api/config` | Groups, editions, limits, whether mail is configured |
| `POST` | `/api/review` | multipart: `file` + `options` JSON → `202 {"id": …}` |
| `GET` | `/api/jobs/{id}` | State, stage, and the full summary when done |
| `GET` | `/api/jobs/{id}/markup.pdf` | The marked-up set |
| `GET` | `/api/jobs/{id}/findings.json` | Machine-readable findings |
| `GET` | `/healthz` | Liveness, and the version this build reports. Cloud Run's startup probe. |
| `GET` | `/api/healthz` | The same answer, on the path the browser can reach. Off the published schema. |

Training mode adds a second set, all of which return `400` unless
`FBC_TRAINING_MODE=1`. See `../docs/TRAINING-MODE.md`.

| Method | Path | Notes |
| --- | --- | --- |
| `GET`/`POST` | `/api/jobs/{id}/markups` | The caller's own markup on a set. Geometry is in PDF points, not pixels. |
| `PATCH`/`DELETE` | `/api/jobs/{id}/markups/{markup_id}` | Edit or remove one. Delete answers with what is left. |
| `GET`/`POST` | `/api/jobs/{id}/feedback` | Structured feedback on a finding, or on something the review missed. The triage verdict comes back in the response. |
| `GET` | `/api/admin/overview` | What is waiting, and which channels are live. |
| `GET`/`POST` | `/api/admin/feedback[/{id}[/decision\|prompt\|issue]]` | The queue, and the three ways out of it. |
| `GET` | `/api/admin/calibration` | The active profile, its history, and every lever there is. |
| `POST` | `/api/admin/digest` | Mail everything still waiting. |

Every `/api/admin/*` path is gated on `FBC_OWNER_EMAILS` and answers **404**,
not 403, to anyone else — the existence of an admin surface is not something to
confirm to somebody who is not on it.

## Email

Off unless SMTP is configured, and the UI says so rather than pretending. Set:

```
FBC_SMTP_HOST=smtp.example.com
FBC_SMTP_PORT=587
FBC_SMTP_USER=...
FBC_SMTP_PASS=...
FBC_MAIL_FROM="Code Review <review@example.com>"
```

Attachments over 20 MB are dropped from the mail and the body points at the download link
instead — a 24-sheet vector set lands around 17 MB, so this matters.

## Other environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `FBC_WORK_DIR` | system temp | Where uploads and outputs live |
| `FBC_WORKERS` | 2 | Concurrent reviews |
| `FBC_MAX_UPLOAD_MB` | 120 | Rejected above this, streamed to disk not buffered |
| `FBC_RETAIN_HOURS` | 24 | Jobs swept after this |

## Before this faces the public internet

This is a working service, not a hardened one. It has no auth, no rate limiting, and no
virus scanning on uploads; jobs live in memory so a restart loses in-flight work; and the
worker pool is in-process rather than a real queue. For anything beyond a trusted LAN or a
single-tenant deployment, put auth and a queue in front of it.
