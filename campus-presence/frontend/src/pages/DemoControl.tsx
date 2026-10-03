import { useEffect, useRef, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card, StatCard } from "../components/Card";
import { api } from "../services/api";
import type { Classroom, DemoScenario, SimulationFullSummary, SimulationStartResponse, SimulationStatus } from "../types/api";

const SCENARIOS: { kind: DemoScenario; label: string; description: string }[] = [
  { kind: "proxy", label: "Proxy Attendance", description: "A student's token is read by two classroom markers in a time window too short for one person to walk between them." },
  { kind: "impossible_movement", label: "Impossible Movement", description: "Two sightings imply a travel speed no student could achieve on foot." },
  { kind: "wifi_ble_mismatch", label: "Wi-Fi / BLE Mismatch", description: "The Wi-Fi zone prediction and the BLE classroom marker disagree." },
  { kind: "token_replay", label: "Token Replay", description: "The same rotating token is observed again outside its valid time window." },
  { kind: "false_positive", label: "False Positive (benign)", description: "A scenario tuned to look odd but resolve as a false positive — exercises the review flow without real misconduct." },
  { kind: "short_presence", label: "Short Presence", description: "Evidence is sustained for far less than the session length, well under the dwell-time threshold." },
];

export function DemoControl() {
  const [seed, setSeed] = useState(1);
  const [starting, setStarting] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [injecting, setInjecting] = useState<DemoScenario | null>(null);
  const [log, setLog] = useState<{ kind: string; result: SimulationStartResponse; ts: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [rooms, setRooms] = useState<Classroom[]>([]);
  const [liveRoom, setLiveRoom] = useState("204");
  const [startingLive, setStartingLive] = useState(false);
  const [liveResult, setLiveResult] = useState<string | null>(null);

  const refreshStatus = () => api.simulationStatus().then(setStatus).catch(() => {});

  useEffect(() => {
    api.listClassrooms().then((rs) => setRooms(rs.filter((r) => r.marker))).catch(() => {});
  }, []);

  const startLiveSession = async () => {
    setStartingLive(true);
    setError(null);
    setLiveResult(null);
    try {
      const t = new Date();
      const code = `LIVE${String(t.getHours()).padStart(2, "0")}${String(t.getMinutes()).padStart(2, "0")}`;
      const r = await api.createSession(code, `${code} (live demo)`, liveRoom, 60);
      setLiveResult(`Session ${r.code} started in Room ${r.room} for 60 minutes; ${r.enrolled_live_students} live students enrolled. Start the phones now.`);
    } catch (e) {
      setError(String(e));
    } finally {
      setStartingLive(false);
    }
  };

  useEffect(() => {
    refreshStatus();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      await api.simulationStart("full", 300, seed, false);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const s = await api.simulationStatus();
        setStatus(s);
        if (!s.running) {
          if (pollRef.current) clearInterval(pollRef.current);
          setStarting(false);
        }
      }, 1200);
    } catch (e) {
      setError(String(e));
      setStarting(false);
    }
  };

  const reset = async () => {
    setResetting(true);
    setError(null);
    try {
      await api.simulationReset();
      setLog([]);
      await refreshStatus();
    } catch (e) {
      setError(String(e));
    } finally {
      setResetting(false);
    }
  };

  const inject = async (kind: DemoScenario) => {
    setInjecting(kind);
    setError(null);
    try {
      const result = await api.simulationStart(kind, 300, seed, true);
      setLog((prev) => [{ kind, result, ts: Date.now() / 1000 }, ...prev].slice(0, 20));
    } catch (e) {
      setError(String(e));
    } finally {
      setInjecting(null);
    }
  };

  const summary: SimulationFullSummary | null = status?.last ?? null;

  return (
    <div>
      <PageHeader title="Demo Control" subtitle="One-click synthetic data for live demonstrations. Never touches live device data." />

      <div
        className="mb-5 rounded-xl border px-4 py-3 text-xs"
        style={{ borderColor: "var(--purple)", background: "var(--purple-soft)", color: "var(--purple)" }}
      >
        Everything on this page generates or injects <strong>SIMULATED</strong> data, clearly tagged as such
        everywhere it surfaces (map, attendance, anomalies). It never represents a real device or a real student's
        location. The one exception is the <em>Live demo with real phones</em> card at the bottom, which only creates
        an empty session for real phones to check into.
      </div>

      {error && (
        <div className="mb-5 rounded-lg border px-3 py-2 text-xs" style={{ borderColor: "var(--red)", color: "var(--red)" }}>
          {error}
        </div>
      )}

      <Card className="mb-5">
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Seed campus population
        </h2>
        <p className="mb-3 text-sm" style={{ color: "var(--text-dim)" }}>
          Generates up to 300 students across 20 classrooms with 10 Wi-Fi APs and BLE markers, realistic RF noise,
          and 6 anomaly scenarios injected automatically. Safe to call again with the same seed.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs" style={{ color: "var(--text-faint)" }}>
            Seed
            <input
              type="number"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
              className="w-20 rounded-lg border px-2 py-1 text-sm outline-none"
              style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
            />
          </label>
          <button
            disabled={starting || status?.running}
            onClick={start}
            className="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            {status?.running ? "Running…" : "Start Simulation"}
          </button>
          <button
            disabled={resetting || status?.running}
            onClick={reset}
            className="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50"
            style={{ background: "var(--bg-elevated)", color: "var(--text-dim)" }}
          >
            {resetting ? "Resetting…" : "Reset All Data"}
          </button>
        </div>

        {status?.running && (
          <div className="mt-4">
            <div className="h-1.5 w-full overflow-hidden rounded-full" style={{ background: "var(--bg-elevated)" }}>
              <div className="h-full rounded-full transition-all" style={{ width: `${Math.round(status.progress * 100)}%`, background: "var(--accent)" }} />
            </div>
            <div className="mt-1.5 text-xs" style={{ color: "var(--text-faint)" }}>{status.message}</div>
          </div>
        )}

        {summary && !status?.running && (
          <div className="mt-4 grid grid-cols-3 gap-3 md:grid-cols-6">
            <StatCard label="Students" value={summary.students} />
            <StatCard label="Sessions" value={summary.sessions} />
            <StatCard label="Scenarios Injected" value={summary.injected.length} accent="var(--amber)" />
            <StatCard label="Skipped" value={summary.skipped_scenarios.length} />
            <StatCard label="Seconds" value={summary.seconds?.toFixed(0) ?? "—"} />
            <StatCard label="Simulated Students" value={status?.simulated_students ?? 0} />
          </div>
        )}
      </Card>

      <Card className="mb-5">
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Inject an anomaly
        </h2>
        <p className="mb-3 text-xs" style={{ color: "var(--text-faint)" }}>
          Requires a simulation to already exist (Start Simulation above, or a previous run).
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {SCENARIOS.map((k) => (
            <button
              key={k.kind}
              disabled={injecting !== null}
              onClick={() => inject(k.kind)}
              className="flex flex-col gap-1 rounded-xl border px-4 py-3 text-left transition-colors disabled:opacity-50"
              style={{ borderColor: "var(--border)", background: "var(--bg-elevated)" }}
            >
              <span className="text-sm font-medium" style={{ color: "var(--text)" }}>
                {injecting === k.kind ? "Injecting…" : k.label}
              </span>
              <span className="text-xs leading-relaxed" style={{ color: "var(--text-faint)" }}>
                {k.description}
              </span>
            </button>
          ))}
        </div>
      </Card>

      <Card className="mb-5">
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Live demo with real phones
        </h2>
        <p className="mb-3 text-xs leading-relaxed" style={{ color: "var(--text-faint)" }}>
          A student only reaches PRESENT when their Bluetooth evidence covers a fair share of the time since the session
          began, so start a fresh session just before the phones. Creates a LIVE session (not simulated) and enrolls the
          live student accounts. It does not touch simulated data.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs" style={{ color: "var(--text-faint)" }}>
            Room
            <select
              value={liveRoom}
              onChange={(e) => setLiveRoom(e.target.value)}
              className="rounded-lg border px-2 py-1 text-sm outline-none"
              style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
            >
              {rooms.length === 0 && <option value="204">Room 204</option>}
              {rooms.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          </label>
          <button
            disabled={startingLive}
            onClick={startLiveSession}
            className="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            {startingLive ? "Starting…" : "Start a fresh live session (60 min)"}
          </button>
        </div>
        {liveResult && (
          <div className="mt-3 text-xs" style={{ color: "var(--green)" }}>{liveResult}</div>
        )}
      </Card>

      <Card>
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Injection log
        </h2>
        {log.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-faint)" }}>Nothing injected yet this session.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {log.map((entry, i) => (
              <div key={i} className="rounded-lg px-3 py-2 text-xs" style={{ background: "var(--bg-elevated)" }}>
                <div className="flex items-center justify-between">
                  <span className="font-medium" style={{ color: "var(--text)" }}>
                    {SCENARIOS.find((k) => k.kind === entry.kind)?.label ?? entry.kind}
                  </span>
                  <span style={{ color: (entry.result.anomaly_ids?.length ?? 0) > 0 ? "var(--red)" : "var(--green)" }}>
                    {(entry.result.anomaly_ids?.length ?? 0) > 0 ? `${entry.result.anomaly_ids?.length} anomaly(ies) flagged` : "not flagged"}
                  </span>
                </div>
                <div className="mt-1" style={{ color: "var(--text-faint)" }}>
                  {entry.result.session_code} · Room {entry.result.room} · {entry.result.target_student_keys?.length ?? 0} target student(s) · see Anomalies page for details
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
