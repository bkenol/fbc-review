# secrets/

Nothing in this directory is committed except `README.md` and `*.example`.

| File | What it is |
| --- | --- |
| `local.env.example` | The template. Committed, holds no values. |
| `local.env` | Your copy, with real values. **Never committed.** |
| `firebase-sa.json` | A service account key, if you run `--authenticated`. **Never committed.** |

Create your copy with:

```bash
bash scripts/setup-secrets.sh          # copies the template, then says what is missing
bash scripts/setup-secrets.sh --check  # says what is configured, without changing anything
```

`secrets/local.env` is read by `scripts/share.sh` and `share.ps1`, which hand it
to `docker run --env-file`, and by `webapp/envfile.py` for a bare `uvicorn` run.
Values are literal — do not quote them. `webapp/envfile.py` explains why.
