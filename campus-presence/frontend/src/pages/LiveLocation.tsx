import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { CampusMap, type MapStudent } from "../components/CampusMap";
import { StateBadge, SourceBadge } from "../components/Badge";
import { useWebSocket } from "../hooks/useWebSocket";
import { api } from "../services/api";
import type { AttendanceState, Classroom, EvidenceDetail, LocationsResponse } from "../types/api";

export function LiveLocation() {
  const [classrooms, setClassrooms] = useState<Classroom[]>([]);
  const [locations, setLocations] = useState<LocationsResponse | null>(null);
  const [states, setStates] = useState<Map<string, AttendanceState>>(new Map());
  const [selected, setSelected] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceDetail | null>(null);
  const { data: event, status } = useWebSocket<{ type: string }>("/ws");

  useEffect(() => {
    api.listClassrooms().then(setClassrooms).catch(() => {});
  }, []);

  const load = () => {
    api.locations().then(setLocations).catch(() => {});
    api
      .listSessions()
      .then(async (sessions) => {
        // Simulated sessions are historical by design (there's rarely an "in_progress" one),
        // so show each room's most recent session rather than requiring a live one.
        const latestPerRoom = new Map<string, number>();
        for (const s of [...sessions].sort((a, b) => b.start_ts - a.start_ts)) {
          if (!latestPerRoom.has(s.room)) latestPerRoom.set(s.room, s.id);
        }
        const rows = await Promise.all([...latestPerRoom.values()].map((id) => api.attendance(id)));
        const map = new Map<string, AttendanceState>();
        for (const r of rows) for (const row of r.rows) map.set(row.student_key, row.final_state);
        setStates(map);
      })
      .catch(() => {});
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 20000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (event) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event]);

  const students: MapStudent[] = useMemo(
    () => (locations?.students ?? []).map((s) => ({ ...s, state: states.get(s.student_key) })),
    [locations, states],
  );
  const selectedStudent = students.find((s) => s.student_id === selected) ?? null;
  const selectedRoom = classrooms.find((c) => c.id === selectedStudent?.zone) ?? null;

  useEffect(() => {
    if (!selected) {
      setEvidence(null);
      return;
    }
    let cancelled = false;
    api
      .evidence(selected)
      .then((d) => !cancelled && setEvidence(d))
      .catch(() => !cancelled && setEvidence(null));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const latestSession = evidence?.sessions[0] ?? null;

  return (
    <div>
      <PageHeader
        title="Live Location"
        subtitle="Campus map built from classroom coordinates; dots are students, colored by attendance state where known."
        actions={
          <span
            className="flex items-center gap-1.5 text-xs"
            style={{ color: status === "open" ? "var(--green)" : "var(--text-faint)" }}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${status === "open" ? "pulse" : ""}`}
              style={{ background: status === "open" ? "var(--green)" : "var(--text-faint)" }}
            />
            {status === "open" ? "Live feed connected" : status === "connecting" ? "Connecting…" : "Feed offline — polling"}
          </span>
        }
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_320px]">
        <div style={{ minHeight: 480 }}>
          <CampusMap classrooms={classrooms} students={students} selectedId={selected} onSelect={setSelected} />
        </div>

        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="mb-2 text-sm font-semibold" style={{ color: "var(--text)" }}>
              Selected student
            </h2>
            {!selectedStudent ? (
              <p className="text-sm" style={{ color: "var(--text-faint)" }}>
                Click a dot on the map to inspect their current evidence breakdown.
              </p>
            ) : (
              <div className="flex flex-col gap-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium" style={{ color: "var(--text)" }}>
                    {selectedStudent.name}
                  </span>
                  <SourceBadge source={selectedStudent.source} />
                </div>
                <div style={{ color: "var(--text-dim)" }}>{selectedRoom?.name ?? selectedStudent.zone}</div>
                <div className="flex items-center justify-between">
                  {selectedStudent.state ? <StateBadge state={selectedStudent.state} /> : <span style={{ color: "var(--text-faint)" }}>no active session</span>}
                  {selectedStudent.confidence !== null && (
                    <span style={{ color: "var(--text-faint)" }}>confidence {(selectedStudent.confidence * 100).toFixed(0)}%</span>
                  )}
                </div>
                <div className="text-xs" style={{ color: "var(--text-faint)" }}>
                  {selectedStudent.estimate} · updated {new Date(selectedStudent.ts * 1000).toLocaleTimeString()}
                </div>
              </div>
            )}
          </div>

          {latestSession && (
            <div className="border-t pt-3" style={{ borderColor: "var(--border)" }}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-faint)" }}>
                Evidence breakdown ({latestSession.session.code})
              </h3>
              <div className="flex flex-col gap-1.5">
                {([
                  ["BLE marker", latestSession.evidence.classroom_ble],
                  ["Wi-Fi", latestSession.evidence.wifi],
                  ["Peers", latestSession.evidence.peers],
                  ["Sustained", latestSession.evidence.sustained],
                ] as const).map(([label, comp]) => (
                  <div key={label} className="flex items-center justify-between text-xs">
                    <span style={{ color: "var(--text-dim)" }}>{label}</span>
                    <span style={{ color: (comp.points as number) > 0 ? "var(--green)" : "var(--text-faint)" }}>
                      {(comp.points as number).toFixed(0)} pts
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="border-t pt-3 text-xs leading-relaxed" style={{ borderColor: "var(--border)", color: "var(--text-faint)" }}>
            {locations ? (
              <>{students.length} tracked · {locations.access_points.length} Wi-Fi APs registered</>
            ) : (
              "Waiting for data…"
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
