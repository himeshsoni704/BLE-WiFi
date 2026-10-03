Campus Proof-of-Presence

«Don't trust one check-in. Cross-validate multiple sources of evidence.»

An IEEE BPDC CampusOPS hackathon project that uses the phones students already carry to build a more reliable proof-of-presence system.

Instead of depending on a single QR scan, face scan, RFID tap, or Bluetooth signal, the system combines BLE, Wi-Fi, peer-device observations, movement patterns, and existing attendance systems.

The goal is simple:

«If multiple independent signals agree, we have stronger evidence that the student is actually there. If they don't, we ask a human to review it.»

---

What problem are we solving?

Most attendance systems answer:

«"Did the student check in?"»

But that doesn't always mean:

«"Was the student actually present?"»

A QR code can be shared. A phone can be left in a classroom. A single wireless signal can be noisy. Faculty also usually don't get much information about why an attendance record looks suspicious.

We wanted to build something that doesn't completely replace existing systems, but instead adds another layer of evidence around them.

---

Our approach

The system collects multiple signals and compares them.

- BLE gives us short-range proximity to a classroom or device.
- Wi-Fi gives us room or zone-level context using RSSI fingerprints.
- Peer devices can provide additional proximity evidence.
- Face scan, QR or RFID can be connected as additional evidence from existing systems.
- Rules and Isolation Forest look for unusual combinations of signals.
- Explainability shows what contributed to an anomaly.
- RAG retrieves similar verified cases from previous reviews.
- Google Gemini can turn the evidence into a simple explanation for faculty.
- Faculty feedback becomes verified data for future retrieval and optional model retraining.

No single signal decides whether someone was present.

---

How it works

BLE ───────────────┐
Wi-Fi ─────────────┤
Peer devices ──────┤
Face / QR / RFID ──┤
                   ↓
             Evidence Fusion
                   ↓
          Rules + Isolation Forest
                   ↓
             Explainability
                   ↓
                  RAG
                   ↓
             Google Gemini
                   ↓
            Faculty Dashboard
                   ↓
              Human Feedback
                ↙       ↘
              RAG       ML Dataset

BLE

Student phones and classroom nodes exchange short-lived BLE tokens.

The tokens rotate every 30 seconds and don't directly contain the student's identity.

BLE provides information such as:

- proximity
- RSSI
- detection duration
- classroom marker detection
- nearby devices

Wi-Fi localization

The phone collects RSSI information from nearby access points.

The system uses Wi-Fi fingerprints to estimate the student's room or zone.

This is room-level localization, not GPS.

Evidence fusion

Different signals are combined instead of trusting one check-in.

For example:

BLE  → Room 204
Wi-Fi → Room 204
QR   → Room 204

→ Strong agreement
→ PRESENT

But:

QR   → Room 204
BLE  → Room 204
Wi-Fi → Block C

→ Signals disagree
→ REVIEW REQUIRED

The current prototype uses a transparent weighted scoring system and requires multiple independent signal families before marking someone "PRESENT".

The score is an evidence score, not a probability.

---

ML anomaly detection

We use two layers.

Rules

The prototype checks for:

- token reuse
- impossible movement
- BLE/Wi-Fi contradiction
- rapid classroom switching

Isolation Forest

Isolation Forest looks for unusual combinations of features such as:

- BLE duration
- BLE RSSI
- Wi-Fi confidence
- nearby device count
- token reuse
- time between locations
- estimated movement speed
- classroom switches
- signal consistency
- peer RSSI behaviour

The model doesn't say that someone cheated.

It says:

«"This combination of signals is unusual and should be reviewed."»

The current model is trained on synthetic data, so its results are not real-world accuracy measurements.

---

Explainability + RAG + Gemini

When an anomaly is detected, the system shows which evidence contributed to it.

RAG retrieves relevant information from the knowledge base and previously verified cases.

Google Gemini is an optional explanation layer. It receives the structured evidence and retrieved context and converts it into a readable explanation.

Gemini does not make the attendance decision.

Faculty can then confirm the anomaly, dismiss it as a false positive, and add a comment.

That feedback can immediately be used for future RAG retrieval and can also be added to the ML feedback dataset for explicit retraining.

---

Tech Stack

Layer| Technology
Android| Kotlin
BLE| Android BLE APIs
Wi-Fi| Android Wi-Fi APIs
Backend| Python + FastAPI
Database| SQLite + SQLAlchemy
ML| scikit-learn
Anomaly Detection| Isolation Forest
Explainability| Shapley attribution
RAG| TF-IDF + knowledge base
LLM| Google Gemini
Frontend| React + TypeScript + Vite
Dashboard| Tailwind + Recharts
Simulation| NumPy
Testing| pytest + JUnit
CI| GitHub Actions

---

Setup

Requirements

For the live demo, you need:

- Python 3.10+
- Node.js 20+
- npm
- Android Studio
- JDK 17
- 2 Android phones
- Both phones and your laptop/PC connected to the same Wi-Fi network
- Optional Google Gemini API key

The two phones are used as:

Phone 1 → Student phone
Phone 2 → Classroom node
Laptop  → Backend + Dashboard

---

1. Clone the repository

git clone https://github.com/himeshsoni704/BLE-WiFi.git
cd BLE-WiFi

---

2. Start the backend

cd campus-presence/backend

python -m venv .venv

Linux/macOS

source .venv/bin/activate

Windows

.venv\Scripts\activate

Install dependencies:

pip install -r requirements.txt

Start the server:

uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000

The backend should now be available at:

http://localhost:8000

API documentation:

http://localhost:8000/docs

---

3. Build and start the dashboard

Open another terminal:

cd campus-presence/frontend
npm install
npm run build

The backend serves the dashboard at:

http://localhost:8000/ui/

Open that address in your browser.

For development with Vite:

npm run dev

---

4. Get your PC's LAN IP

Your Android phones cannot use:

http://localhost:8000

because "localhost" on the phone means the phone itself.

Find the IP address of the computer running the backend.

Linux

ip addr

or:

hostname -I

Example:

192.168.1.42

Windows

ipconfig

Look for the IPv4 address of the active Wi-Fi adapter.

For example:

192.168.1.42

Then the Android app should use:

http://192.168.1.42:8000

Make sure:

- laptop and phones are on the same network
- port "8000" is allowed through the firewall
- the backend is running with "--host 0.0.0.0"

You can test from the phone browser:

http://192.168.1.42:8000/health

If the backend is reachable, the phone should receive the health response.

---

5. Build the Android app

Open:

campus-presence/android

in Android Studio.

Use JDK 17.

You can also build from the terminal:

cd campus-presence/android
./gradlew assembleDebug

On Windows:

gradlew.bat assembleDebug

The debug APK will be generated under the Android project's build output.

You can also use the Android APK GitHub Actions workflow to build the APK.

---

6. Install the app on two phones

Install the APK on both Android phones.

The phones should have:

- Bluetooth enabled
- Wi-Fi enabled
- Location enabled where required by Android for Wi-Fi scanning
- Nearby Devices permissions enabled
- Location permission enabled
- Notification permission where requested

The app does not use GPS to determine classroom location.

Location permission is required by Android for certain Wi-Fi scanning APIs.

---

7. Configure the classroom phone

Use one phone as the classroom node.

For example:

Classroom: Room 204
Node: node-204-demo

The classroom phone advertises the Room 204 BLE marker and listens for student devices.

Keep the phone physically inside the classroom.

---

8. Configure the student phone

Use the second phone as the student device.

For the demo account:

Student: HIMESH
Password: demo1234

The student phone receives its pseudonymous token after authentication.

It then:

1. Advertises its rotating student token.
2. Scans for classroom BLE markers.
3. Collects nearby Wi-Fi information.
4. Sends the observations to the backend.
5. Receives the resulting presence state.

---

9. Start a live classroom session

On the dashboard:

Faculty Login
     ↓
Sessions
     ↓
Create / select classroom session
     ↓
Room 204
     ↓
Start session

Then keep the student phone and classroom phone running.

After the backend receives enough observations, the dashboard should start showing the collected evidence.

For example:

Student: HIMESH

BLE:
✓ Classroom marker detected
✓ Student token valid
✓ Sustained presence

Wi-Fi:
✓ Predicted Room 204

Evidence:
BLE + Wi-Fi agree

Presence:
PRESENT

The exact result depends on the physical environment and signal quality.

---

10. Test an anomaly live

You can also demonstrate what happens when the signals disagree.

For example, move the student phone away from the classroom while keeping the classroom node running.

The system can begin seeing something like:

BLE → Room 204
Wi-Fi → Different zone

This can contribute to a contradiction or anomaly.

You can then open:

Dashboard
→ Anomalies
→ Select anomaly
→ Explain

The dashboard shows:

- triggered rules
- Isolation Forest result
- feature attribution
- relevant RAG cases
- possible explanations
- evidence used
- faculty review controls

---

11. Test the feedback loop

From the anomaly page, faculty can provide feedback.

For example:

False Positive
Comment:
"Student was temporarily outside the room while Wi-Fi
still reported the previous AP fingerprint."

The case is then available to the RAG system as a verified case.

The feedback dataset is also regenerated at:

ml/data/feedback_dataset.csv

If you want to explicitly retrain the anomaly model:

cd campus-presence

python ml/retrain_anomaly_model.py

Then reload the models from the dashboard/API.

Retraining is manual, not automatic.

---

Running without physical phones

If you don't have two Android phones available, you can still demonstrate most of the system using the simulator.

Start the backend and open:

http://localhost:8000/ui/

Then:

Faculty Login
→ Demo Control
→ Start Simulation

The simulator creates a synthetic campus with:

- up to 300 students
- 20 rooms
- 10 access points
- 22 zones
- simulated BLE/Wi-Fi observations
- attendance states
- anomaly scenarios

You can then demonstrate:

Simulation
   ↓
Evidence Fusion
   ↓
Anomaly Detection
   ↓
Explainability
   ↓
RAG
   ↓
Gemini / Offline Explanation
   ↓
Faculty Feedback

This is the easiest way to demonstrate the full software pipeline without depending on physical wireless conditions.

---

API

Some of the main endpoints are:

POST /auth/login
GET  /auth/me
GET  /auth/provision

POST /ble-observation
POST /wifi-observation
POST /presence

GET  /students
GET  /classrooms
GET  /attendance
GET  /evidence/{student_id}
GET  /locations

GET  /anomalies
GET  /anomalies/{id}
POST /explain-anomaly

POST /rag/retrieve
POST /feedback

POST /simulation/start
POST /simulation/reset
GET  /simulation/status

GET  /health
GET  /config

WS   /ws

Full interactive API documentation is available at:

http://localhost:8000/docs

---

Project structure

.
├── campus-presence/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── fusion.py
│   │   │   ├── rules.py
│   │   │   ├── anomaly.py
│   │   │   ├── xai.py
│   │   │   ├── rag.py
│   │   │   ├── llm/
│   │   │   └── routers/
│   │   └── tests/
│   │
│   ├── frontend/
│   ├── android/
│   ├── simulation/
│   ├── ml/
│   └── docs/
│
├── docs/screenshots/
└── .github/workflows/

---

Current limitations

This is a working prototype, not a production university attendance system.

- BLE RSSI is noisy.
- Wi-Fi localization is room/zone level, not GPS.
- Android Wi-Fi scanning is affected by OS restrictions and permissions.
- iOS is not currently supported.
- ML models currently use synthetic training data.
- Real-world accuracy has not been established.
- Proxy-phone detection is still weak.
- Attendance weights are hand-selected and not calibrated probabilities.
- Gemini is optional and does not determine attendance.
- Privacy, retention and access policies would need university-level review before deployment.
- An anomaly is not proof of misconduct.

---

Future work

- Collect and validate on real campus data
- Calibrate BLE RSSI per device
- Improve Wi-Fi fingerprinting
- Learn evidence-fusion weights from real labelled data
- Improve proxy-phone detection
- Add LMS/ERP integrations
- Add hostel/nighttime presence
- Add emergency presence workflow
- Add event-specific attendance
- Add SSO and department-level access
- Add automated model drift monitoring
- Improve RAG with embedding retrieval
- Add iOS support

---

The idea in one line

«Don't trust one check-in. Cross-validate multiple sources of evidence.»

We're not trying to replace the attendance systems universities already use.

We're trying to make them harder to fool by asking a more useful question:

«"Do multiple independent signals agree that this person was actually there?"»

And when they don't, the system gives the evidence to a human instead of making the decision itself.
