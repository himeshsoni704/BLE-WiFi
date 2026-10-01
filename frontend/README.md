# Proof-of-Presence Dashboard

React + Vite + TypeScript + Tailwind faculty/admin dashboard for the
Proof-of-Presence backend (`../backend`). Dark-themed, built against the
REST + WebSocket API in `app/main.py`.

## Pages

- **Dashboard** — campus-wide state counts, live/simulated device split, recent anomalies.
- **Live Location** — SVG campus map (`src/components/CampusMap.tsx`) fed by `GET /locations` and the `WS /ws/locations` feed; click a student to see their evidence breakdown.
- **Attendance** — filterable/searchable table of every tracked student's current state.
- **Anomalies** — rule + Isolation Forest flags, with Explain (RAG + LLM), Confirm, and False Positive actions.
- **Evidence Explorer** — per-student score timeline and weighted evidence breakdown.
- **Feedback** — history of faculty Confirm/False Positive decisions.
- **Demo Control** — one-click simulation seeding and anomaly injection for live demos. Everything here is clearly tagged SIMULATED and never touches live device data.

## Development

```bash
npm install
npm run dev      # dev server on :5173, proxies /api and /ws to http://127.0.0.1:8000
npm run build    # type-checks (tsc -b) then builds to dist/
```

Run the backend separately (`uvicorn app.main:create_app --factory`, from `../backend`)
before starting the dev server — the dashboard has no mock data layer, it
talks to the real API.

Set `VITE_API_KEY` in a `.env.local` file if the backend was started with
`Settings.api_key` set; it is sent as the `X-API-Key` header and appended
to WebSocket URLs as `?key=`.
