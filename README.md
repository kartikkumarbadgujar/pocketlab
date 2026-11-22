# PocketLab — The Cheap Phone Test

PocketLab tests Android application reliability under constrained device and network conditions. This repository contains the FastAPI backend and React dashboard.

## Repository layout

- `backend/` — FastAPI, SQLAlchemy, Alembic migrations, PostgreSQL configuration, and runner API.
- `frontend/` — React, TypeScript, Vite dashboard.

## Local development

Start the backend from `backend/`. Install the requirements and configure `backend/.env` first. PostgreSQL is the deployment database; see `backend/README.md` for PostgreSQL/Docker setup and migrations. SQLite is supported for local development only.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set database credentials and fresh JWT_SECRET and RUNNER_TOKEN values in .env.
alembic upgrade head
python -m scripts.seed
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then start the dashboard from `frontend/` in another terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev -- --host 127.0.0.1 --port 5173
```

- Dashboard: <http://127.0.0.1:5173>
- API docs: <http://127.0.0.1:8000/docs>

To execute APKs on real Android environments, start the execution worker on a machine with the Android SDK and
hardware acceleration (a third terminal for local development):

```powershell
cd backend
python -m app.workers.emulator_runner --capabilities   # what this host can really do
python -m app.workers.emulator_runner                  # claim and execute queued runs
```

To deploy publicly for free (dashboard and API on Vercel, PostgreSQL on Neon), see `backend/docs/DEPLOY_VERCEL.md`.
Live: dashboard https://pocketlab-wheat.vercel.app, API https://pocketlab-api.vercel.app/docs.

See `backend/docs/ANDROID_WORKER.md` for setup and `IMPLEMENTATION_REPORT.md` for what has and has not been verified.

Never commit `.env` files, credentials, local databases, APKs, or runner artifacts. See each application's README for tests, API contracts, and deployment limitations.

## Streamlit dashboard

`streamlit_app/streamlit_app.py` is a Streamlit version of the dashboard that uses the same API and accounts as the React app.
Run locally with `pip install -r streamlit_app/requirements.txt` and `streamlit run streamlit_app/streamlit_app.py`.
Deploy on https://share.streamlit.io: repository `kartikkumarbadgujar/pocketlab`, branch `main`, main file `streamlit_app/streamlit_app.py`.
Set the `POCKETLAB_API_URL` secret to use another API (default: https://pocketlab-api.vercel.app/api/v1).
