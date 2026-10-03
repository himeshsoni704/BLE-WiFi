import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { StateBadge, SourceBadge } from "../components/Badge";
import { NEUTRAL_COLOR, STATE_COLOR, TOOLTIP_STYLE } from "../theme";
import { api } from "../services/api";
import type { EvidenceDetail, StudentRow } from "../types/api";


/** The header search links here with ?student=<id>; the route remounts this page when that id changes. */
export function EvidenceExplorer() {
  const [students, setStudents] = useState<StudentRow[]>([]);
  const [params] = useSearchParams();
  const [studentId, setStudentId] = useState<string>(params.get("student") ?? "");
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const [sessionIdx, setSessionIdx] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.listStudents().then((res) => {
      setStudents(res.students);
      if (res.students.length > 0) setStudentId((prev) => prev || res.students[0].student_id);
    });
  }, []);

  useEffect(() => {
    if (!studentId) return;
    setLoading(true);
    setSessionIdx(0);
    api
      .evidence(studentId)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [studentId]);

  const chartData = useMemo(
    () =>
      [...(detail?.sessions ?? [])].reverse().map((s) => ({
        label: s.session.code,
        score: Math.round(s.score),
        state: s.final_state,
      })),
    [detail],
  );

  const session = detail?.sessions[sessionIdx] ?? null;
  const relevantTimeline = useMemo(() => {
    if (!session || !detail) return [];
    const { start_ts, end_ts } = session.session;
    return detail.timeline.filter((t) => t.ts >= start_ts - 300 && t.ts <= end_ts + 120);
  }, [session, detail]);

  return (
    <div>
      <PageHeader title="Evidence Explorer" subtitle="Per-session attendance score for one student, with the weighted evidence breakdown and raw observation timeline." />

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
        {detail?.student && <SourceBadge source={detail.student.source} />}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_300px]">
        <Card>
          <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
            Score by session
          </h2>
          {loading ? (
            <p className="text-sm" style={{ color: "var(--text-faint)" }}>Loading…</p>
          ) : chartData.length === 0 ? (
            <p className="text-sm" style={{ color: "var(--text-faint)" }}>No session evidence recorded yet.</p>
          ) : (
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="label" stroke="var(--text-faint)" fontSize={11} tickLine={false} />
                  <YAxis domain={[0, 100]} stroke="var(--text-faint)" fontSize={11} tickLine={false} unit="%" />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    labelStyle={{ color: "var(--text)" }}
                  />
                  <Bar dataKey="score" radius={[8, 8, 0, 0]}>
                    {chartData.map((d, i) => (
                      <Cell key={i} fill={STATE_COLOR[d.state as keyof typeof STATE_COLOR] ?? NEUTRAL_COLOR} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <h3 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-faint)" }}>
            Observation timeline {session && `(${session.session.code})`}
          </h3>
          <div className="max-h-72 overflow-y-auto">
            {relevantTimeline.length === 0 ? (
              <p className="text-sm" style={{ color: "var(--text-faint)" }}>No observations for this session.</p>
            ) : (
              <table className="w-full text-xs">
                <tbody>
                  {relevantTimeline.map((t, i) => (
                    <tr key={i} className="border-b last:border-0" style={{ borderColor: "var(--border-soft)" }}>
                      <td className="py-1.5 pr-2" style={{ color: "var(--text-faint)" }}>{new Date(t.ts * 1000).toLocaleTimeString()}</td>
                      <td className="py-1.5 pr-2" style={{ color: "var(--text)" }}>{t.kind}</td>
                      <td className="py-1.5 pr-2" style={{ color: "var(--text-dim)" }}>
                        {t.room ?? t.wifi_zone ?? t.peer ?? "—"}
                      </td>
                      <td className="py-1.5 pr-2 tabular-nums" style={{ color: "var(--text-faint)" }}>{t.rssi !== null ? `${t.rssi} dBm` : ""}</td>
                      <td className="py-1.5" style={{ color: "var(--text-faint)" }}>{t.provenance}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>

        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="mb-2 text-sm font-semibold" style={{ color: "var(--text)" }}>Sessions</h2>
            <div className="flex flex-col gap-1">
              {(detail?.sessions ?? []).map((s, i) => (
                <button
                  key={s.session.id}
                  onClick={() => setSessionIdx(i)}
                  className="flex items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs"
                  style={{ background: i === sessionIdx ? "var(--bg-elevated)" : "transparent" }}
                >
                  <span style={{ color: "var(--text-dim)" }}>{s.session.code}</span>
                  <StateBadge state={s.final_state} />
                </button>
              ))}
            </div>
          </div>

          {session && (
            <div className="border-t pt-3" style={{ borderColor: "var(--border)" }}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-faint)" }}>
                Evidence breakdown
              </h3>
              <div className="flex flex-col gap-1.5">
                {([
                  ["BLE marker", session.evidence.classroom_ble],
                  ["Wi-Fi", session.evidence.wifi],
                  ["Peers", session.evidence.peers],
                  ["Sustained", session.evidence.sustained],
                ] as const).map(([label, comp]) => (
                  <div key={label} className="flex items-center justify-between text-xs">
                    <span style={{ color: "var(--text-dim)" }}>{label}</span>
                    <span style={{ color: (comp.points as number) > 0 ? "var(--green)" : "var(--text-faint)" }}>
                      {(comp.points as number).toFixed(0)} pts
                    </span>
                  </div>
                ))}
                <div className="mt-1 flex items-center justify-between text-xs">
                  <span style={{ color: "var(--text-dim)" }}>Signal families</span>
                  <span style={{ color: "var(--text)" }}>{session.evidence.independent_signal_families?.count ?? 0}</span>
                </div>
                {session.evidence.isolation_forest?.evaluated && (
                  <div className="mt-1 flex items-center justify-between text-xs">
                    <span style={{ color: "var(--text-dim)" }}>Isolation Forest</span>
                    <span style={{ color: session.evidence.isolation_forest.flagged ? "var(--amber)" : "var(--text)" }}>
                      {session.evidence.isolation_forest.flagged ? "anomalous" : "normal"}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
