import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { CampusMap } from "../components/CampusMap";
import { StateBadge, SourceBadge } from "../components/Badge";
import { useWebSocket } from "../hooks/useWebSocket";
import { api } from "../services/api";
import type { Classroom, EvidenceDetail, LocationsFeed } from "../types/api";

export function LiveLocation() {
  const [classrooms, setClassrooms] = useState<Classroom[]>([]);
  const [fallback, setFallback] = useState<LocationsFeed | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceDetail | null>(null);
  const { data: live, status } = useWebSocket<LocationsFeed>("/ws/locations");

  useEffect(() => {
    api.listClassrooms().then(setClassrooms).catch(() => {});
  }, []);

  useEffect(() => {
    if (status !== "open") {
      api.locations().then(setFallback).catch(() => {});
    }
  }, [status]);

  const feed = live ?? fallback;
  const students = useMemo(() => feed?.students ?? [], [feed]);
  const selectedStudent = students.find((s) => s.student_id === selected) ?? null;

  useEffect(() => {
    if (!selected) {
      setEvidence(null);
      return;
    }
    let cancelled = false;
    api
      .evidence(selected, 60 * 60)
      .then((d) => !cancelled && setEvidence(d))
      .catch(() => !cancelled && setEvidence(null));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  return (
    <div>
      <PageHeader
        title="Live Location"
        subtitle="Campus map built from classroom coordinates; dots are students, colored by attendance state."
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
                <div style={{ color: "var(--text-dim)" }}>{selectedStudent.classroom_name}</div>
                <div className="flex items-center justify-between">
                  <StateBadge state={selectedStudent.state} />
                  <span style={{ color: "var(--text-faint)" }}>
                    score {selectedStudent.confidence.toFixed(0)}%
                  </span>
                </div>
                <div className="text-xs" style={{ color: "var(--text-faint)" }}>
                  updated {new Date(selectedStudent.last_update * 1000).toLocaleTimeString()}
                </div>
              </div>
            )}
          </div>

          {evidence?.latest && (
            <div className="border-t pt-3" style={{ borderColor: "var(--border)" }}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-faint)" }}>
                Evidence breakdown
              </h3>
              <div className="flex flex-col gap-1.5">
                {evidence.latest.components.map((c) => (
                  <div key={c.name} className="flex items-center justify-between text-xs">
                    <span style={{ color: "var(--text-dim)" }} title={c.reason}>
                      {c.name.replace(/_/g, " ")}
                    </span>
                    <span style={{ color: c.points > 0 ? "var(--green)" : "var(--text-faint)" }}>
                      {c.points.toFixed(0)} / {c.max_points.toFixed(0)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="border-t pt-3 text-xs leading-relaxed" style={{ borderColor: "var(--border)", color: "var(--text-faint)" }}>
            {feed ? (
              <>
                {feed.live_devices} live · {feed.simulated_devices} simulated · {students.length} total tracked
              </>
            ) : (
              "Waiting for data…"
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
