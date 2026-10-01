import { useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card, StatCard } from "../components/Card";
import { api } from "../services/api";
import type { DemoInjectKind, DemoInjectResult, SimulationSummary } from "../types/api";

const INJECT_KINDS: { kind: DemoInjectKind; label: string; description: string }[] = [
  { kind: "proxy_attendance", label: "Proxy Attendance", description: "A student's token is read by two classroom markers in a time window too short for one person to walk between them." },
  { kind: "impossible_movement", label: "Impossible Movement", description: "Two sightings imply a travel speed no student could achieve on foot." },
  { kind: "wifi_ble_mismatch", label: "Wi-Fi / BLE Mismatch", description: "The Wi-Fi zone prediction and the BLE classroom marker disagree." },
  { kind: "token_replay", label: "Token Replay", description: "The same rotating token is observed again outside its valid time window." },
  { kind: "device_clustering", label: "Device Clustering", description: "An unusually large number of distinct nearby devices for one student." },
  { kind: "short_presence", label: "Short Presence", description: "Evidence is sustained for far less than the session length, well under the dwell-time threshold." },
];

export function DemoControl() {
  const [seed, setSeed] = useState(0);
  const [starting, setStarting] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [summary, setSummary] = useState<SimulationSummary | null>(null);
  const [studentId, setStudentId] = useState("");
  const [injecting, setInjecting] = useState<DemoInjectKind | null>(null);
  const [log, setLog] = useState<{ kind: DemoInjectKind; result: DemoInjectResult; ts: number }[]>([]);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      setSummary(await api.simulationStart(300, 20, 10, 0.08, seed));
    } catch (e) {
      setError(String(e));
    } finally {
      setStarting(false);
    }
  };

  const reset = async () => {
    setResetting(true);
    setError(null);
    try {
      await api.simulationReset();
      setSummary(null);
      setLog([]);
    } catch (e) {
      setError(String(e));
    } finally {
      setResetting(false);
    }
  };

  const inject = async (kind: DemoInjectKind) => {
    setInjecting(kind);
    setError(null);
    try {
      const result = await api.simulationInject(kind, studentId.trim() || undefined);
      setLog((prev) => [{ kind, result, ts: Date.now() / 1000 }, ...prev].slice(0, 20));
    } catch (e) {
      setError(String(e));
    } finally {
      setInjecting(null);
    }
  };

  return (
    <div>
      <PageHeader title="Demo Control" subtitle="One-click synthetic data for live demonstrations. Never touches live device data." />

      <div
        className="mb-5 rounded-xl border px-4 py-3 text-xs"
        style={{ borderColor: "var(--purple)", background: "var(--purple-soft)", color: "var(--purple)" }}
      >
        Everything on this page generates or injects <strong>SIMULATED</strong> data, clearly tagged as such
        everywhere it surfaces (map, attendance, anomalies). It never represents a real device or a real student's
        location.
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
          Generates 300 students across 20 classrooms with 10 Wi-Fi APs and BLE markers, realistic RF noise, and an
          ~8% baseline anomaly rate. Safe to call again with the same seed.
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
            disabled={starting}
            onClick={start}
            className="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            {starting ? "Starting…" : "Start Simulation"}
          </button>
          <button
            disabled={resetting}
            onClick={reset}
            className="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50"
            style={{ background: "var(--bg-elevated)", color: "var(--text-dim)" }}
          >
            {resetting ? "Resetting…" : "Reset All Data"}
          </button>
          <input
            value={studentId}
            onChange={(e) => setStudentId(e.target.value)}
            placeholder="Target student_id (optional — random if blank)"
            className="ml-auto min-w-[260px] rounded-lg border px-3 py-1.5 text-xs outline-none"
            style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
          />
        </div>

        {summary && (
          <div className="mt-4 grid grid-cols-3 gap-3 md:grid-cols-6">
            <StatCard label="Students" value={summary.students} />
            <StatCard label="Classrooms" value={summary.classrooms} />
            <StatCard label="Wi-Fi APs" value={summary.wifi_aps} />
            <StatCard label="BLE Markers" value={summary.ble_markers} />
            <StatCard label="Sessions" value={summary.sessions} />
            <StatCard label="Anomalies Seeded" value={summary.anomalies_injected} accent="var(--amber)" />
          </div>
        )}
      </Card>

      <Card className="mb-5">
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Inject an anomaly
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {INJECT_KINDS.map((k) => (
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
                    {INJECT_KINDS.find((k) => k.kind === entry.kind)?.label ?? entry.kind}
                  </span>
                  <span style={{ color: entry.result.is_anomalous ? "var(--red)" : "var(--green)" }}>
                    {entry.result.is_anomalous ? `flagged (${entry.result.severity})` : "not flagged"}
                  </span>
                </div>
                {entry.result.anomaly_id !== null && (
                  <div className="mt-1" style={{ color: "var(--text-faint)" }}>
                    anomaly #{entry.result.anomaly_id}
                    {entry.result.isolation_forest_score !== null &&
                      ` · iforest ${entry.result.isolation_forest_score.toFixed(3)}`}
                    {entry.result.reasons.length > 0 && <> · {entry.result.reasons.join("; ")}</>}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
