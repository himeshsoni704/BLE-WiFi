# Proof-of-Presence Dashboard

React + Vite + TypeScript + Tailwind faculty/admin dashboard for the
`campus-presence` backend (`../backend`). Dark-themed, built against the
REST + WebSocket API in `app/main.py` and `app/routers/*.py`.

## Pages

- **Login** — JWT sign-in against `POST /auth/login`. In demo mode (`DEMO_MODE=1`, the default) sign in as `faculty` or `admin`, password `demo1234`.
- **Dashboard** — campus-wide attendance counts, open anomalies, verified-case count, recent anomalies (`GET /summary`, `GET /anomalies`).
- **Live Location** — SVG campus map (`src/components/CampusMap.tsx`) fed by `GET /locations` + `GET /classrooms`, colored by each room's most recent session state (`GET /attendance`); `WS /ws` triggers a refetch on any event. Click a student for their evidence breakdown.
- **Attendance** — per-session table (`GET /attendance?session_id=`) with a session picker, state filter, and search.
- **Anomalies** — rule + Isolation Forest flags, with Explain (RAG + LLM, shows provider/grounding), Confirm, and False Positive actions (`GET /anomalies`, `POST /explain-anomaly`, `POST /feedback`).
- **Evidence Explorer** — per-student, per-session score chart and weighted evidence breakdown, plus the raw observation timeline (`GET /evidence/{student_id}`).
- **Feedback** — history of faculty decisions and the verified-case knowledge base RAG retrieves from (`GET /feedback/summary`).
- **Demo Control** — one-click simulation seeding (background job with progress polling) and scenario injection for live demos (`POST /simulation/start`, `/simulation/status`, `/simulation/reset`). Everything here is clearly tagged SIMULATED and never touches live device data.

## Run it

The dashboard has no mock data layer: it talks to the real API, so start the backend first
(from `../backend`, with its requirements installed):

```bash
uvicorn app.main:create_app --factory --host 0.0.0.0
```

The first start trains the Wi-Fi and Isolation Forest models from synthetic data if
`../ml/models/*.joblib` don't exist yet (about 30 s); later starts take a few seconds.

**Single process (best for a demo).** Build once and the backend serves the app itself:

```bash
npm install
npm run build        # type-checks (tsc -b), then builds to dist/
```

Open <http://localhost:8000/ui/> (`/` redirects there). Rebuild after changing the UI. Deep links
and refreshes work (`/ui/attendance`).

**Dev server with hot reload.**

```bash
npm run dev          # http://localhost:5173, proxies /api and /ws to http://127.0.0.1:8000
```

A built app calls the backend on its own origin; a dev server goes through the `/api` proxy. To point a
build at a backend on another origin, set `VITE_API_BASE` (for example `VITE_API_BASE=http://pc:8000 npm run build`;
the backend's `CORS_ORIGINS` must then allow the page's origin).

Auth is a real JWT (`POST /auth/login`, stored in `localStorage`), not an API key, so there is no `VITE_API_KEY` to set.
The WebSocket URL appends the token as `?token=`.
