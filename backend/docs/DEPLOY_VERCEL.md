# Deploying PocketLab on Vercel (free)

| Resource | Vercel project | URL |
|---|---|---|
| Dashboard (static Vite build) | `pocketlab`, root directory `frontend` | <https://pocketlab-wheat.vercel.app> |
| API (FastAPI as a Python function) | `pocketlab-api`, root directory `backend` | <https://pocketlab-api.vercel.app> (docs at `/docs`) |
| PostgreSQL | Neon (Vercel Marketplace, Free plan) `pocketlab-db`, region `sin1` | connected to `pocketlab-api` |

Both projects are on the Hobby plan and the database on Neon's Free plan, so nothing is billed.

## API (`backend/`)

* `api/index.py` exports the FastAPI `app`; `vercel.json` rewrites every path to it and pins the function to `sin1`
  (next to the database). `.python-version` selects Python 3.12; dependencies come from `requirements.txt`.
* Environment variables (Project → Settings → Environment Variables): `DATABASE_URL` (Neon's *unpooled* URL; the
  config switches it to the psycopg 3 driver), `JWT_SECRET`, `RUNNER_TOKEN`, `ENVIRONMENT=production`,
  `CORS_ORIGINS`, `CORS_ORIGIN_REGEX=^https://pocketlab[a-z0-9-]*\.vercel\.app$`, `TRUSTED_HOSTS=["*"]`,
  `UPLOAD_DIR=/tmp/pocketlab_uploads`, `BLOB_READ_WRITE_TOKEN` (added by the Blob store), `GEMINI_API_KEY` (sensitive;
  enables the assistant) and optionally `GEMINI_MODEL` (default `gemini-3.5-flash`). The Neon integration adds `NEON_*` variables.
* Deploy: from `backend/`, `npx vercel link --yes --project pocketlab-api` then `npx vercel deploy --prod --yes`.

Serverless functions do not run migrations on start. After a schema change, run them once against Neon from a
machine with the backend's virtualenv:

```powershell
$env:DATABASE_URL = "<NEON_DATABASE_URL_UNPOOLED>"   # vercel env pull, or the Neon dashboard
alembic upgrade head
python -m scripts.seed
```

## Dashboard (`frontend/`)

`frontend/vercel.json` holds the build settings and SPA rewrites; `frontend/.env.production` holds the public API URL
(`https://pocketlab-api.vercel.app/api/v1`). Deploy from `frontend/` with `npx vercel deploy --prod --yes`.

## Limits of the free serverless API

* **APK uploads go straight to Vercel Blob.** Functions reject bodies above ~4.5 MB, so the dashboard asks the API for
  a short-lived upload token (`POST /projects/{id}/builds/upload-token`), uploads the APK from the browser to the
  private Blob store `pocketlab-apks`, then registers it (`POST /projects/{id}/builds/from-upload`); the API downloads
  and validates it like a normal upload. Without `BLOB_READ_WRITE_TOKEN` (local development) the token endpoint returns
  404 and the dashboard falls back to the multipart upload.
* The Android worker downloads Blob-stored APKs in 3 MB ranges (`/runner/jobs/{id}/apk?offset=`) and checks the SHA-256.
  It cannot run on Vercel itself (no Android SDK or hardware virtualization); run it on a laptop or VM.
* The rate limiter is per function instance.

The backend also ships a `Dockerfile` for hosts that run containers with a persistent disk.

## AI assistant

The "Ask AI" button in the dashboard opens PocketLab Assistant (`POST /api/v1/assistant/chat`). The API sends Gemini
the user's question plus their own projects, recent runs and, on a run page, that run's findings and metrics, with
instructions not to invent measurements. Without `GEMINI_API_KEY` the assistant reports that it is not configured.
