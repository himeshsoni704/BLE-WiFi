# Proof-of-Presence

Attendance and location evidence from the phones students already carry, with a faculty dashboard and an Android app.

> **Prototype.** The score is a transparent weighted checklist, not a validated probability. The anomaly layer only
> raises cases for a person to review; it never marks anyone absent. Read [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
> before showing this to people who will rely on it.

## How it works

Phones and classroom nodes collect evidence; a backend fuses it into one attendance state per student per session.

| Signal | Where it comes from |
|---|---|
| Classroom BLE | A student phone hears the room's rotating **marker** (or a classroom node hears the student's rotating token). Tokens are HMACs that change every 30 s. |
| Wi-Fi | The phone uploads the access points it hears; a model trained on surveyed rooms estimates the room. |
| Peers | Other phones seen in the same room corroborate, but only when the student has an independent signal of their own. |
| Sustained presence | How much of the session the BLE evidence covers. |
| Face / RFID | Optional, accepted only from staff accounts. No face system is included. |

States: `PRESENT` (score 70+ **and** two independent signal families), `LIKELY_PRESENT` (50+), `REVIEW_REQUIRED` (30+),
`ABSENT`. Deterministic rules (impossible movement, token reuse, Wi-Fi/BLE contradiction, rapid switching) and an
Isolation Forest flag unusual cases; faculty confirm or dismiss them, and a RAG + LLM explainer (offline mock by default,
Gemini optional) summarises the evidence.

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI service: ingest, fusion, rules, Isolation Forest, RAG, REST + WebSocket. |
| `frontend/` | React + Vite + TypeScript dashboard. See [frontend/README.md](frontend/README.md). |
| `android/` | The phone app: student, classroom node, Wi-Fi surveyor. See [android/README.md](android/README.md). |
| `simulation/` | Synthetic campus (20 rooms, 10 APs, 300 students, injected anomalies) behind *Demo Control*. |
| `ml/` | Training scripts and model reports. |
| `docs/` | [Limitations](docs/LIMITATIONS.md) and [third-party sources](docs/THIRD_PARTY.md). |

## Run it

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:create_app --factory --host 0.0.0.0        # http://localhost:8000
```

The first start trains the Wi-Fi and Isolation Forest models from synthetic data (about 30 s). Later starts take seconds.
Interactive API docs are at `/docs`.

```bash
cd ../frontend
npm install && npm run build                                # then open http://localhost:8000/ui/
# or: npm run dev                                           # hot reload on http://localhost:5173
```

Sign in as `faculty` or `admin`, password `demo1234` (demo accounts exist while `DEMO_MODE=1`, the default).

## Demo script (about 5 minutes, no phones)

1. **Demo Control**: *Start Simulation* (about 16 s: 300 students, 60 sessions, 12 injected anomalies).
2. **Dashboard**: attendance KPIs, four charts (zones, per-session presence, outcome mix, trend) and recent cases. The header search jumps to a student's Evidence Explorer; the bell shows open anomalies.
3. **Live Location**: about 237 students on the campus map, coloured by state. Click a dot for its evidence.
4. **Anomalies**: *Explain* a case (rule hits, Isolation Forest score, similar verified cases), then *Confirm* or *False Positive*.
5. **Demo Control** again: inject *Proxy Attendance* and watch a new anomaly arrive over the WebSocket.
6. **Feedback** and **Evidence Explorer**: the faculty decisions the explainer retrieves from, and per-student evidence.

Simulated rows are tagged `SIMULATED` everywhere. *Reset All Data* removes them and keeps live data, accounts and faculty decisions.

## Demo with real phones

Follow [android/README.md](android/README.md): survey the rooms' Wi-Fi, run one phone as the classroom node and one as a
student, and watch the student become `PRESENT` on the dashboard. Live phones and the simulation can run side by side:
the map keeps each source's own time window, so a live phone does not hide the simulated crowd.

## Explanations: Gemini, a small knowledge base, and score attribution

When faculty press **Explain** on an anomaly, three things are combined, all visible on the card:

1. **Why the forest flagged it.** Exact Shapley values split the Isolation Forest's score among its 12 features against a
   typical-normal baseline, so the card shows "flagged mostly because of implied travel speed and rooms within 5 minutes",
   and whether the record would have passed had those been typical. It explains the model's score, not anyone's intent.
2. **What the system knows.** A short text knowledge base (`backend/app/knowledge/*.md`, 16 passages: what each rule and
   feature means, common innocent causes, policy) plus similar cases faculty already resolved. Retrieval is deterministic
   (tag overlap plus TF-IDF). Add your own campus policy as new `## KB-... | Title` passages.
3. **Reasoning over both.** Ranked possible causes, each with a likelihood and the evidence fields and sources it cites, and
   checks faculty can make to tell them apart.

The default explainer is offline and deterministic. To use Gemini for the reasoning instead:

```powershell
# Windows PowerShell, in the same window you start the backend from
$env:LLM_PROVIDER = "gemini"; $env:GEMINI_API_KEY = "<your key>"; $env:GEMINI_MODEL = "<a model id your key can use>"
uvicorn app.main:create_app --factory --host 0.0.0.0
```

```bash
# macOS / Linux
LLM_PROVIDER=gemini GEMINI_API_KEY=... GEMINI_MODEL=... uvicorn app.main:create_app --factory --host 0.0.0.0
```

`GET /config` reports which provider is active, and the Explain card shows which one answered. **Gemini is never trusted
blindly.** Its answer is rejected, and the offline explainer's used instead (with the reason shown), if it:
mentions a number that is not in the evidence, cites an evidence field, passage or case that was not provided, offers a
cause with no citation at all, uses an unknown likelihood, or uses accusatory wording. This catches invented facts and
unsupported causes; it cannot prove that a sentence is true, which is why the cited sources are shown next to it.

What leaves your machine when Gemini is on: the pseudonymous student label (a hash, unless `GEMINI_SEND_REAL_IDS=1`), rooms,
signal values, scores, the retrieved knowledge passages, and the **text of similar past cases, including the faculty comments
typed on them**. Do not type anything into a Confirm/False Positive comment that should not reach Google.

Checking the models: `python ml/audit_models.py` (after `python ml/generate_dataset.py`) reports how stable the numbers are
across splits, where each model fails, and how much of each scenario the rules and the forest cover. See
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) for what it found.

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | SQLite in `backend/data/` | SQLAlchemy URL |
| `MODEL_DIR`, `ML_DATA_DIR` | `ml/models`, `ml/data` | Where model files and datasets live |
| `REPORT_DIR` | `backend/data/reports` | Reports written by the automatic first-run training |
| `JWT_SECRET`, `ID_PEPPER` | generated into `backend/data/` | Signing secret; pepper for hashed student keys |
| `JWT_TTL_S` | 43200 | Login lifetime |
| `TS_TOLERANCE_S` | 120 | Readings further than this from the server clock are rejected |
| `RETENTION_DAYS` | 30 | Observations and locations older than this are deleted at startup |
| `DEMO_MODE`, `DEMO_PASSWORD` | `1`, `demo1234` | Demo accounts, demo node keys (`node-<room>-demo`), the rolling `CS301` session |
| `LLM_PROVIDER` | `mock` | `gemini` needs `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`); real student ids are sent only if `GEMINI_SEND_REAL_IDS=1` |
| `RATE_LIMIT` | `1` | In-memory rate limiting (logins: 10/min) |
| `CORS_ORIGINS` | the Vite dev origins | Comma-separated |
| `FUSION_CONFIG`, `W_<NAME>` | built-in | JSON file or per-weight overrides for the fusion weights and thresholds |

## Tests

```bash
cd backend && pytest                    # about two minutes
cd ../android && ./gradlew test         # JVM unit tests for the app's protocol code
cd ../frontend && npm run build         # type-check + build
```

## Retraining the models

The server trains a quick synthetic model on first start. To train deliberately, from this directory (this refreshes the
tracked reports in `ml/models/`):

```bash
python ml/generate_dataset.py           # synthetic datasets into ml/data/ (about 40 s)
python ml/train_wifi_model.py
python ml/train_anomaly_model.py
python ml/retrain_anomaly_model.py      # only after faculty have confirmed/dismissed anomalies: false positives become normal rows
```

Then restart the backend or call `POST /models/reload`. Real Wi-Fi accuracy comes from surveying real rooms with the
Android app and retraining the live model (`POST /wifi/retrain`), not from these scripts.

## Troubleshooting

- **Phone readings rejected as `timestamp_out_of_window`**: the phone's clock is more than 2 minutes off. The app's *Check server* shows the offset.
- **Wi-Fi rejected as `no_known_aps`**: nobody has surveyed the rooms yet, so the server knows none of the access points.
- **A student never leaves `REVIEW_REQUIRED`**: BLE alone cannot reach `PRESENT`; the Wi-Fi model needs a survey of at least two rooms.
- **Student stays `LIKELY_PRESENT` with real phones**: the session is too old. Press *Start a fresh live session* in Demo Control, then start the phones.
- **No live session at all**: sessions last 60 minutes; press *Start a fresh live session* in Demo Control.
- **Too many logins**: the login endpoint allows 10 attempts a minute per client.
