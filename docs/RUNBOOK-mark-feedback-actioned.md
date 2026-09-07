---
title: Marking feedback e4e02383fed1 actioned
type: runbook
tags:
  - code-review
  - florida-building-code
  - training-feedback
  - operations
status: draft
created: 2026-09-07
---

# Marking feedback `e4e02383fed1` actioned

Everything below is verified against this repository and against the live service as
of 2026-09-07, not recalled. Where a value came from a file, the file is named.

---

## 1. The facts this rests on

| Thing | Value | Where it came from |
| --- | --- | --- |
| Live host | `https://fbc.omniflexfitness.com` | `docs/DEPLOYMENT.md` line 1; `/healthz` returns **200** |
| GCP / Firebase project | `fbc-reviewer` | `.firebaserc` |
| Cloud Run service | `fbc-review`, region `us-east1` | `firebase.json` rewrite |
| Firestore collection | `feedback` | `webapp/config.py:170`, env `FBC_FEEDBACK_COLLECTION` |
| Document id | `e4e02383fed1` | the feedback item |
| Endpoint | `POST /api/admin/feedback/{id}/decision` | `webapp/server.py:1785` |
| Decision value | `action` | `webapp/models.py:72` (`Decision.ACTION`) |
| Resulting state | `actioned` | `webapp/server.py` decision map |
| Who may call it | an email in `FBC_OWNER_EMAILS` | `webapp/config.py:159` |

> **Correction to something I said earlier.** I reported the service as unreachable.
> That was wrong — I tested `review.omniflexfitness.com`, which is the hostname in
> `CLAUDE.md`'s *Current task* line. The deployed host is `fbc.omniflexfitness.com`
> (`docs/DEPLOYMENT.md`), and it answers fine from here. The only thing I actually
> lack is an **owner ID token**. Worth reconciling those two hostnames in `CLAUDE.md`
> at some point.

---

## 2. Recommended: go through the API

**Use this one.** It is a single call, and the server stamps `decided_at`,
`decided_by` and `decision_note` for you — which is the whole reason
`CLAUDE.md` prefers it. A row written any other way records a decision made by
nobody at no time.

### Step 1 — get an owner ID token

Sign in at <https://fbc.omniflexfitness.com> with an account listed in
`FBC_OWNER_EMAILS`. Then, easiest route, open DevTools → **Network**, click any
`/api/...` request the page made, and copy the value of the `Authorization`
header (everything after `Bearer `).

If you would rather pull it from the console, the Firebase SDK keeps it in
IndexedDB — this reads it out:

```js
await new Promise((resolve, reject) => {
  const open = indexedDB.open('firebaseLocalStorageDb');
  open.onerror = () => reject(open.error);
  open.onsuccess = () => {
    const req = open.result
      .transaction('firebaseLocalStorage', 'readonly')
      .objectStore('firebaseLocalStorage')
      .getAll();
    req.onsuccess = () => {
      const rec = req.result.find(r => String(r.fbase_key).startsWith('firebase:authUser:'));
      resolve(rec ? rec.value.stsTokenManager.accessToken : 'NOT SIGNED IN');
    };
    req.onerror = () => reject(req.error);
  };
});
```

**ID tokens expire after one hour.** Get it immediately before you run the call.

### Step 2 — make the call

Git Bash / WSL:

```bash
TOKEN='PASTE_THE_ID_TOKEN_HERE'

curl -sS -X POST \
  "https://fbc.omniflexfitness.com/api/admin/feedback/e4e02383fed1/decision" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "decision": "action",
        "note": "Confirmation pinned as tests/test_code_edition.py (PR #24). Reproducing it found the fbc2017 ordinal collision and the missing 6TH lexicon entry; both fixed."
      }'
```

PowerShell:

```powershell
$Token = 'PASTE_THE_ID_TOKEN_HERE'
$Body  = @{
  decision = 'action'
  note     = 'Confirmation pinned as tests/test_code_edition.py (PR #24). Reproducing it found the fbc2017 ordinal collision and the missing 6TH lexicon entry; both fixed.'
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri 'https://fbc.omniflexfitness.com/api/admin/feedback/e4e02383fed1/decision' `
  -Headers @{ Authorization = "Bearer $Token" } `
  -ContentType 'application/json' `
  -Body $Body
```

### What you should get back

The full `Feedback` model with `state` now `actioned`, plus `decided_at`,
`decided_by` and `decision_note` populated.

| Response | Meaning |
| --- | --- |
| `200` | Done. |
| `401` | Token missing, malformed, or expired — mint a fresh one. |
| `403` | Signed in, but that email is not in `FBC_OWNER_EMAILS`. |
| `404` | No feedback with that id in this environment. |
| `400` | Only happens on `"accept"`. Don't use `accept` here — the triage attached no calibration proposal, so there is nothing to promote. `action` is correct. |

---

## 3. As asked: writing Firestore directly

This is the path you asked for. It works, and I'd still reach for §2 — not out of
caution, but because this route writes the audit fields by hand, so a typo in
`decided_by` or a forgotten `decided_at` is a decision attributed to nobody, with
nothing to catch it. `CLAUDE.md` allows it "when the service itself is
unreachable" and requires writing **every field the endpoint would have**. Both
recipes below do that.

The four fields `webapp/server.py` writes:

| Field | Type | Value |
| --- | --- | --- |
| `state` | string | `actioned` |
| `decided_at` | timestamp (UTC) | now |
| `decided_by` | string | your owner email |
| `decision_note` | string | free text |

### Prerequisite — application default credentials

```bash
gcloud auth application-default login
gcloud config set project fbc-reviewer
```

Use ADC, not a downloaded service-account key — that is `CLAUDE.md`'s security
rule and the same one CI follows.

### Option A — Python (recommended for this path)

Matches the repo's own stack, and `SERVER_TIMESTAMP` means the clock is
Firestore's rather than your laptop's.

```python
# scripts/mark_actioned.py — run once, then delete it or leave it uncommitted.
from google.cloud import firestore

PROJECT   = "fbc-reviewer"
COLLECTION = "feedback"          # webapp/config.py:170
DOC_ID    = "e4e02383fed1"
OWNER     = "bertin.kenol@omniflexfitness.com"   # must match FBC_OWNER_EMAILS
NOTE      = ("Confirmation pinned as tests/test_code_edition.py (PR #24). "
             "Reproducing it found the fbc2017 ordinal collision and the "
             "missing 6TH lexicon entry; both fixed.")

db  = firestore.Client(project=PROJECT)
ref = db.collection(COLLECTION).document(DOC_ID)

snap = ref.get()
if not snap.exists:
    raise SystemExit(f"No feedback {DOC_ID} in {PROJECT}/{COLLECTION}")
print("before:", snap.to_dict().get("state"))

ref.update({
    "state":         "actioned",
    "decided_at":    firestore.SERVER_TIMESTAMP,
    "decided_by":    OWNER,
    "decision_note": NOTE,
})

print("after :", ref.get().to_dict().get("state"))
```

```bash
pip install google-cloud-firestore==2.28.1   # the pin in requirements.txt
python scripts/mark_actioned.py
```

`update()` — not `set()`. `set()` without `merge=True` replaces the whole
document and would destroy the submission.

### Option B — Firestore REST API

```bash
ACCESS_TOKEN="$(gcloud auth print-access-token)"
OWNER='bertin.kenol@omniflexfitness.com'
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

curl -sS -X PATCH \
  "https://firestore.googleapis.com/v1/projects/fbc-reviewer/databases/(default)/documents/feedback/e4e02383fed1?updateMask.fieldPaths=state&updateMask.fieldPaths=decided_at&updateMask.fieldPaths=decided_by&updateMask.fieldPaths=decision_note" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
        \"fields\": {
          \"state\":         {\"stringValue\": \"actioned\"},
          \"decided_at\":    {\"timestampValue\": \"$NOW\"},
          \"decided_by\":    {\"stringValue\": \"$OWNER\"},
          \"decision_note\": {\"stringValue\": \"Confirmation pinned as tests/test_code_edition.py (PR #24).\"}
        }
      }"
```

**The `updateMask` is not optional.** A `PATCH` without it replaces the entire
document with just the four fields you sent, silently deleting the reviewer's
answers, the triage verdict and the job link. All four `updateMask.fieldPaths`
parameters must be present.

### Verify either option

```bash
curl -sS \
  "https://firestore.googleapis.com/v1/projects/fbc-reviewer/databases/(default)/documents/feedback/e4e02383fed1" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)"
```

---

## 4. Why I could not run this myself

| Requirement | Status here |
| --- | --- |
| Network to `fbc.omniflexfitness.com` | ✅ reachable (`/healthz` → 200) |
| Network to `firestore.googleapis.com` | ✅ reachable |
| `gcloud` / `firebase` CLI | ❌ not installed |
| ADC or any Google credential | ❌ none present |
| An owner Firebase ID token | ❌ none, and no way to complete an interactive Google sign-in from here |

The blocker is **credentials, not the `CLAUDE.md` rule** — which is why I did not
edit that file. Removing "go through the API, not the database" would have
unblocked nothing and would have retired the rule that keeps `decided_by` and
`decided_at` honest. Say the word and I'll change it, but it buys you nothing
today.

**Do not paste a token or key into the chat to get around this.** `CLAUDE.md` is
right that anything pasted into a session is burned and would need rotating. The
call takes about fifteen seconds in your own terminal.
