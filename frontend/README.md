# PocketLab frontend

Responsive React and TypeScript dashboard for PocketLab. Typed endpoint groups live in `src/services/api.ts`; TanStack Query owns read caching, cancellation, polling, retry policy, and mutation invalidation. `src/api/client.ts` remains a compatibility re-export for existing imports. The API base URL is configurable with `VITE_API_BASE_URL`.

## Run locally

Use Node.js 20.19+ or 22.12+ and npm. Start the backend first by following `../backend/README.md`, including its migrations and seed step. Then, from this directory:

```powershell
npm install
Copy-Item .env.example .env
npm run dev
```

Vite serves the app at `http://localhost:5173`. Set `VITE_API_BASE_URL` in `.env` to the backend API root, for example `http://localhost:8000/api/v1`.

## Checks

```powershell
npm run lint
npm run build
npm run test:e2e
```

The Playwright smoke tests use an installed Google Chrome and an API server configured at the API base URL. They register disposable synthetic users, visit every protected route, create and reload a project/profile, exercise APK validation errors, select a seeded quick profile, and check the mobile dock and reduced-motion preference. Run them against an isolated development/test database, not a shared user database. Test results and traces are written to the ignored `test-results/` directory.

## Product behavior

## Frontend-to-backend contract map

| Frontend page / workflow | Existing FastAPI endpoints |
| --- | --- |
| Login and registration | `POST /auth/login`, `POST /auth/register`, `POST /auth/refresh`, `POST /auth/logout` |
| Dashboard | `GET /projects`, `GET /devices`, `GET /test-runs`, `GET /test-runs/{id}/findings`, `GET /profiles` |
| Projects and APK builds | `GET/POST /projects`, `GET/PUT/DELETE /projects/{id}`, `GET/POST /projects/{id}/builds` |
| Device inventory | `GET /devices`, `POST /devices/register`, `GET /devices/{id}` |
| Profile builder and quick profiles | `GET/POST /profiles`, `GET /profiles/{id}`, `POST /profiles/{id}/versions` |
| Scenario library | `GET /scenarios`, `GET /scenarios/{id}` |
| Test Lab | `GET /projects`, `GET /projects/{id}/builds`, `GET /devices`, `GET /profiles`, `GET /profiles/{id}`, `GET /scenarios`, `GET /scenarios/{id}`, `POST /test-runs` |
| Live monitoring and run details | `GET /test-runs/{id}`, `POST /test-runs/{id}/cancel`, `GET /test-runs/{id}/metrics`, `GET /test-runs/{id}/findings`, `GET /test-runs/{id}/artifacts` |
| Findings | `GET /test-runs`, `GET /test-runs/{id}/findings` |
| User impact | `POST /test-runs/{id}/impact-estimates` |
| Local runner protocol (not called by the browser) | `/runner/heartbeat`, `/runner/jobs/claim`, `/runner/jobs/{id}/heartbeat`, `/runner/jobs/{id}/complete`, `/runner/jobs/{id}/fail`, `/runner/jobs/{id}/metrics/batch`, `/runner/jobs/{id}/findings` |

This map reflects the existing FastAPI OpenAPI contract. The API currently has no runner-agent listing, run-stage event stream, evidence download URL, or impact-estimate history endpoint; the UI does not synthesize those resources.

- Dashboard statistics, projects, devices, profiles, scenarios, runs, metrics and findings use the existing `/api/v1` routes. Requests are abortable; reads are cached; profile/build/run mutations invalidate their corresponding caches.
- Quick profile cards load backend profile versions and submit the selected version ID to Test Lab. The thermal observation card stays unavailable because there is no corresponding seeded profile.
- APK uploads show server-returned SHA-256, size, and scan state. Uploads remain in backend quarantine until an external scanner approves them; the frontend never executes uploaded APKs and prevents queueing a run without an approved build.
- Profiles are persisted and can be versioned with the profile-version endpoint. Only the nine measurement keys the backend marks configurable can be sent as requested settings. Thermal and memory readings remain observation-only.
- Live run status and metrics poll while the backend says a run is active, stop on terminal states, and pause in background tabs. ThoughtLine maps only the backend's `queued`, `claimed`, `running`, `completed`, `failed`, and `canceled` states; it does not fabricate device stages.
- Findings show runner-supplied expected/actual behavior, reproduction steps, evidence references, and any attached impact value. Profile/network join details are not exposed by the current findings/run API.
- Impact reports submit sourced caller inputs and show the estimate returned by the backend. The API has no read/list endpoint for saved estimate history.
- Auth uses the backend's JSON access/refresh-token contract with refresh rotation. Axios sends credentials for cross-origin deployments, but this backend does not set HTTP-only cookies, so tokens are persisted in browser storage. A cookie-only refresh flow requires a backend contract change.
- DitherField and GlowCursor are local implementations. GlowCursor has a static fallback and disables WebGL for reduced motion and touch-first devices. The supplied reference component source files were not present in the repository, so the local components follow the described behavior and visual direction rather than copying their source.

The backend does not expose runner-agent listing/connected status, individual device-stage progress events, evidence download URLs, or persisted impact-estimate reads. Production deployment still needs private object storage, a scanner approval workflow, per-agent credentials, a disposable Android runner, and security review as described in the backend README. The backend APK validator checks archive structure and manifest presence, not full binary-manifest/package-signature correctness or malware safety.
