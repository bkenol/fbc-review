# web/ — the Angular client

Angular 22: standalone components, signals, zoneless change detection, strict
mode with `strictTemplates`, Vitest. No `NgModule`, no `zone.js`, no CSS or
component framework — this is one page, and `src/styles.css` is the whole design.

## Run it

The client needs the API. Start that first, from the repository root:

```bash
FBC_DEV_UNSAFE_AUTH=1 FBC_BUCKET=fbc-dev-local FBC_PROJECT_ID=fbc-dev-local   FBC_ALLOWED_EMAILS=you@example.com   .venv/bin/python -m uvicorn webapp.server:app --port 8060
```

Then:

```bash
npm start          # ng serve with proxy.conf.json -> http://127.0.0.1:8060
```

`FBC_DEV_UNSAFE_AUTH=1` swaps Firestore and Cloud Storage for filesystem
stand-ins (`webapp/devbackend.py`) and accepts unauthenticated requests, so the
client is usable without a GCP project. It cannot switch on in a deployed
service: `webapp/config.py` refuses the flag whenever `K_SERVICE` is set, and
Cloud Run always sets it.

Port 8060 rather than 8000: on some Windows machines 8000 falls inside a
reserved exclusion range and cannot be bound.

## The generated API client

`src/app/api/` is generated from the backend's OpenAPI schema and **committed**.
Do not hand-edit it.

```bash
npm run api:refresh    # dump ../openapi.json from the app, then regenerate
```

That is the whole point of a typed client: a field renamed in `webapp/models.py`
becomes a TypeScript compile error here rather than a runtime `undefined`. CI
regenerates it and fails if the result differs from what is committed.

`scripts/postgen.mjs` removes the NgModule variant the generator still emits and
strips its re-export from `index.ts`, so the committed output is a pure function
of `openapi.json`.

## Firebase

Fill in `src/app/core/firebase-config.ts` from the Firebase console. Those values
are public by design — access is decided server-side by verifying the ID token
and checking the email allowlist. Until they are filled in, `ng serve` stands in
a local user so the tool page is reachable; `ng build` output never does.
