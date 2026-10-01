import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { StateBadge, SourceBadge } from "../components/Badge";
import { api } from "../services/api";
import type { AttendanceState, LocationRow } from "../types/api";

const STATE_FILTERS: { value: AttendanceState | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "present", label: "Present" },
  { value: "likely_present", label: "Likely Present" },
  { value: "review_required", label: "Review Required" },
  { value: "absent", label: "Absent" },
];

export function Attendance() {
  const [rows, setRows] = useState<LocationRow[]>([]);
  const [asOf, setAsOf] = useState<number | null>(null);
  const [filter, setFilter] = useState<AttendanceState | "all">("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .locations()
        .then((feed) => {
          if (cancelled) return;
          setRows(feed.students);
          setAsOf(feed.ts);
          setLoading(false);
        })
        .catch(() => !cancelled && setLoading(false));
    load();
    const id = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows
      .filter((r) => filter === "all" || r.state === filter)
      .filter((r) => !q || r.name.toLowerCase().includes(q) || r.student_id.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [rows, filter, query]);

  return (
    <div>
      <PageHeader
        title="Attendance"
        subtitle={asOf ? `Snapshot as of ${new Date(asOf * 1000).toLocaleTimeString()}` : "Loading…"}
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-elevated)" }}>
          {STATE_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className="rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
              style={{
                background: filter === f.value ? "var(--bg-card)" : "transparent",
                color: filter === f.value ? "var(--text)" : "var(--text-dim)",
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or ID…"
          className="rounded-lg border px-3 py-1.5 text-sm outline-none"
          style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
        />
        <span className="ml-auto text-xs" style={{ color: "var(--text-faint)" }}>
          {filtered.length} of {rows.length}
        </span>
      </div>

      <Card className="p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wide" style={{ borderColor: "var(--border)", color: "var(--text-faint)" }}>
              <th className="px-4 py-3 font-medium">Student</th>
              <th className="px-4 py-3 font-medium">Classroom</th>
              <th className="px-4 py-3 font-medium">State</th>
              <th className="px-4 py-3 font-medium">Score</th>
              <th className="px-4 py-3 font-medium">Source</th>
              <th className="px-4 py-3 font-medium">Last update</th>
            </tr>
          </thead>
          <tbody>
            {!loading && filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-sm" style={{ color: "var(--text-faint)" }}>
                  No students match. Run a simulation from Demo Control to populate this view.
                </td>
              </tr>
            )}
            {filtered.map((r) => (
              <tr key={r.student_id} className="border-b last:border-0" style={{ borderColor: "var(--border-soft)" }}>
                <td className="px-4 py-2.5">
                  <div style={{ color: "var(--text)" }}>{r.name}</div>
                  <div className="text-xs" style={{ color: "var(--text-faint)" }}>{r.student_id}</div>
                </td>
                <td className="px-4 py-2.5" style={{ color: "var(--text-dim)" }}>{r.classroom_name}</td>
                <td className="px-4 py-2.5"><StateBadge state={r.state} /></td>
                <td className="px-4 py-2.5 tabular-nums" style={{ color: "var(--text-dim)" }}>{r.confidence.toFixed(0)}%</td>
                <td className="px-4 py-2.5"><SourceBadge source={r.source} /></td>
                <td className="px-4 py-2.5 text-xs" style={{ color: "var(--text-faint)" }}>
                  {new Date(r.last_update * 1000).toLocaleTimeString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
