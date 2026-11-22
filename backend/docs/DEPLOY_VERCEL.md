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
  `UPLOAD_DIR=/tmp/pocketlab_uploads`. The Neon integration also adds `NEON_*` variables.
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

* **APK uploads are capped at about 4.5 MB** by Vercel's request-body limit, and uploaded files live in the
  function's `/tmp`, which is not shared between instances or kept between invocations. Sign-in, projects, catalog,
  runs and reports work; for real APK testing run the API locally (or on a host with a disk) next to the worker.
* The Android execution worker cannot run on Vercel (no Android SDK or hardware virtualization). Run it on a laptop or
  VM with `POCKETLAB_API_URL` pointing at an API it can share APK storage with (see `ANDROID_WORKER.md`).
* The rate limiter is per function instance.

The backend also ships a `Dockerfile` for hosts that run containers with a persistent disk.
