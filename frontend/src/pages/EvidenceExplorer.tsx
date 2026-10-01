import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { StateBadge, SourceBadge } from "../components/Badge";
import { api } from "../services/api";
import type { EvidenceDetail, Student } from "../types/api";

const RANGES = [
  { label: "15 min", seconds: 15 * 60 },
  { label: "1 hour", seconds: 60 * 60 },
  { label: "6 hours", seconds: 6 * 60 * 60 },
  { label: "24 hours", seconds: 24 * 60 * 60 },
];

export function EvidenceExplorer() {
  const [students, setStudents] = useState<Student[]>([]);
  const [studentId, setStudentId] = useState<string>("");
  const [rangeS, setRangeS] = useState(RANGES[1].seconds);
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.listStudents().then((list) => {
      setStudents(list);
      if (list.length > 0) setStudentId((prev) => prev || list[0].student_id);
    });
  }, []);

  useEffect(() => {
    if (!studentId) return;
    setLoading(true);
    api
      .evidence(studentId, rangeS)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [studentId, rangeS]);

  const chartData = (detail?.history ?? []).map((h) => ({
    time: new Date(h.ts * 1000).toLocaleTimeString(),
    score: Math.round(h.score),
    state: h.state,
    source: h.source,
  }));

  return (
    <div>
      <PageHeader title="Evidence Explorer" subtitle="Attendance score over time for a single student, with the weighted evidence breakdown." />

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <select
          value={studentId}
          onChange={(e) => setStudentId(e.target.value)}
          className="rounded-lg border px-3 py-1.5 text-sm outline-none"
          style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
        >
          {students.length === 0 && <option value="">No students enrolled</option>}
          {students.map((s) => (
            <option key={s.student_id} value={s.student_id}>
              {s.name} ({s.student_id})
            </option>
          ))}
        </select>
        <div className="flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-elevated)" }}>
          {RANGES.map((r) => (
            <button
              key={r.seconds}
              onClick={() => setRangeS(r.seconds)}
              className="rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
              style={{
                background: rangeS === r.seconds ? "var(--bg-card)" : "transparent",
                color: rangeS === r.seconds ? "var(--text)" : "var(--text-dim)",
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {detail?.latest && (
        <div className="mb-5 flex flex-wrap items-center gap-4">
          <StateBadge state={detail.latest.state} />
          <SourceBadge source={detail.latest.source as "live" | "simulated"} />
          <span className="text-sm" style={{ color: "var(--text-dim)" }}>
            {detail.latest.classroom_id} · score {detail.latest.score.toFixed(0)}% ·{" "}
            {new Date(detail.latest.ts * 1000).toLocaleString()}
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_280px]">
        <Card>
          <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
            Score timeline
          </h2>
          {loading ? (
            <p className="text-sm" style={{ color: "var(--text-faint)" }}>Loading…</p>
          ) : chartData.length === 0 ? (
            <p className="text-sm" style={{ color: "var(--text-faint)" }}>
              No evidence history in this window.
            </p>
          ) : (
            <div style={{ height: 320 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="time" stroke="var(--text-faint)" fontSize={11} tickLine={false} />
                  <YAxis domain={[0, 100]} stroke="var(--text-faint)" fontSize={11} tickLine={false} unit="%" />
                  <Tooltip
                    contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: "var(--text)" }}
                  />
                  <Line type="monotone" dataKey="score" stroke="var(--accent)" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card>
          <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
            Latest evidence breakdown
          </h2>
          {!detail?.latest ? (
            <p className="text-sm" style={{ color: "var(--text-faint)" }}>No evidence recorded yet.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {detail.latest.components.map((c) => (
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
          )}
        </Card>
      </div>
    </div>
  );
}
