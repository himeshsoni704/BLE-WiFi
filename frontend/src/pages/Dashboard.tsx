import { useEffect, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card, StatCard } from "../components/Card";
import { SourceBadge, SeverityBadge } from "../components/Badge";
import { api } from "../services/api";
import type { AnomalyDto, Classroom, LocationsFeed } from "../types/api";

export function Dashboard() {
  const [locations, setLocations] = useState<LocationsFeed | null>(null);
  const [classrooms, setClassrooms] = useState<Classroom[]>([]);
  const [anomalies, setAnomalies] = useState<AnomalyDto[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      Promise.all([api.locations(), api.listClassrooms(), api.anomalies()])
        .then(([locs, rooms, anoms]) => {
          if (cancelled) return;
          setLocations(locs);
          setClassrooms(rooms);
          setAnomalies(anoms);
          setError(null);
        })
        .catch((e) => !cancelled && setError(String(e)));
    load();
    const id = setInterval(load, 10000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const students = locations?.students ?? [];
  const counts = {
    present: students.filter((s) => s.state === "present").length,
    likely_present: students.filter((s) => s.state === "likely_present").length,
    review_required: students.filter((s) => s.state === "review_required").length,
    absent: students.filter((s) => s.state === "absent").length,
  };
  const occupiedRooms = new Set(students.map((s) => s.classroom_id)).size;
  const openAnomalies = anomalies.filter((a) => a.status === "open");
  const highSeverity = openAnomalies.filter((a) => a.severity === "high").length;
  const recentAnomalies = [...anomalies]
    .sort((a, b) => b.ts - a.ts)
    .slice(0, 6);

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Campus-wide presence summary, refreshed every 10 seconds."
        actions={
          locations && (
            <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-faint)" }}>
              <SourceBadge source="live" /> {locations.live_devices}
              <SourceBadge source="simulated" /> {locations.simulated_devices}
            </div>
          )
        }
      />

      {error && (
        <div
          className="mb-5 rounded-xl border px-4 py-3 text-sm"
          style={{ borderColor: "var(--red)", background: "var(--red-soft)", color: "var(--red)" }}
        >
          Couldn't reach the backend: {error}
        </div>
      )}

      <div className="mb-5 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Present" value={counts.present} accent="var(--green)" />
        <StatCard label="Likely Present" value={counts.likely_present} accent="var(--accent)" />
        <StatCard label="Review Required" value={counts.review_required} accent="var(--amber)" />
        <StatCard label="Absent" value={counts.absent} accent="var(--red)" />
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Active Classrooms" value={occupiedRooms} sub={`of ${classrooms.length} registered`} />
        <StatCard label="Tracked Students" value={students.length} />
        <StatCard
          label="Open Anomalies"
          value={openAnomalies.length}
          accent={openAnomalies.length > 0 ? "var(--amber)" : "var(--text)"}
          sub={highSeverity > 0 ? `${highSeverity} high severity` : "none high severity"}
        />
        <StatCard
          label="Live vs Simulated"
          value={`${locations?.live_devices ?? 0} / ${locations?.simulated_devices ?? 0}`}
          sub="live / simulated devices"
        />
      </div>

      <Card>
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Recent anomalies
        </h2>
        {recentAnomalies.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-faint)" }}>
            No anomalies recorded yet. Run a simulation from Demo Control to generate some.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {recentAnomalies.map((a) => (
              <div
                key={a.id}
                className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm"
                style={{ background: "var(--bg-elevated)" }}
              >
                <div className="flex min-w-0 items-center gap-3">
                  <SeverityBadge severity={a.severity} />
                  <span className="truncate" style={{ color: "var(--text)" }}>
                    {a.student_id}
                  </span>
                  <span className="truncate" style={{ color: "var(--text-dim)" }}>
                    {a.type.replace(/_/g, " ")}
                  </span>
                </div>
                <span className="shrink-0 text-xs" style={{ color: "var(--text-faint)" }}>
                  {new Date(a.ts * 1000).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
