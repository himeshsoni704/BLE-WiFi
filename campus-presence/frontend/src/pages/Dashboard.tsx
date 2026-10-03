import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "../components/Layout";
import { Card, StatCard, StatPill } from "../components/Card";
import { SeverityBadge } from "../components/Badge";
import { api } from "../services/api";
import { STATE_COLOR, STATE_LABEL, TOOLTIP_STYLE } from "../theme";
import type { AnomalyRow, AttendanceState, LocationsResponse, SessionRow, SummaryResponse } from "../types/api";

const STATES: AttendanceState[] = ["PRESENT", "LIKELY_PRESENT", "REVIEW_REQUIRED", "ABSENT"];
const AXIS = { stroke: "#94a3b8", fontSize: 11, tickLine: false, axisLine: false } as const;

function ChartCard({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <Card>
      <h2 className="text-[15px] font-bold" style={{ color: "#1e293b" }}>{title}</h2>
      {note && <p className="mb-3 text-xs" style={{ color: "var(--text-dim)" }}>{note}</p>}
      <div className={note ? "" : "mt-3"}>{children}</div>
    </Card>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="flex h-56 items-center justify-center text-center text-sm" style={{ color: "var(--text-faint)" }}>{text}</div>;
}

export function Dashboard() {
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [anomalies, setAnomalies] = useState<AnomalyRow[]>([]);
  const [locations, setLocations] = useState<LocationsResponse | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      Promise.all([api.summary(), api.anomalies(undefined, "all", 6), api.locations("all"), api.listSessions("all")])
        .then(([s, a, l, ss]) => {
          if (cancelled) return;
          setSummary(s);
          setAnomalies(a.anomalies);
          setLocations(l);
          setSessions(ss);
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

  const att = summary?.attendance;
  const attTotal = STATES.reduce((n, k) => n + (att?.[k] ?? 0), 0);
  const openAnomalies = (summary?.anomalies?.open ?? 0) + (summary?.anomalies?.confirmed ?? 0);
  const highSeverity = anomalies.filter((a) => a.rule_details.some((r) => r.severity === "high") || a.isolation_flagged).length;

  const zones = useMemo(
    () => Object.entries(locations?.per_zone ?? {}).map(([zone, n]) => ({ zone, n })).filter((z) => z.n > 0)
      .sort((a, b) => b.n - a.n).slice(0, 8),
    [locations],
  );
  const bySession = useMemo(
    () => [...sessions].sort((a, b) => a.start_ts - b.start_ts).slice(-10).map((s) => ({
      label: s.code,
      PRESENT: s.counts.PRESENT ?? 0,
      LIKELY_PRESENT: s.counts.LIKELY_PRESENT ?? 0,
      REVIEW_REQUIRED: s.counts.REVIEW_REQUIRED ?? 0,
      ABSENT: s.counts.ABSENT ?? 0,
    })),
    [sessions],
  );
  const donut = STATES.map((k) => ({ key: k, name: STATE_LABEL[k], value: att?.[k] ?? 0 })).filter((d) => d.value > 0);

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Campus-wide presence summary, refreshed every 10 seconds."
        actions={
          summary && (
            <div className="flex items-center gap-2 bg-white px-3 py-1.5 text-xs font-medium" style={{ color: "var(--text-dim)", borderRadius: 9999 }}>
              <span className="h-1.5 w-1.5 rounded-full pulse" style={{ background: "#3fae7a" }} />
              {summary.students.live} live · {summary.students.simulated} simulated
            </div>
          )
        }
      />

      {error && (
        <div className="mb-5 px-4 py-3 text-sm" style={{ background: "var(--red-soft)", color: "var(--red)", borderRadius: "var(--radius-md)" }}>
          Couldn't reach the backend: {error}
        </div>
      )}

      {/* Row 1: KPI tiles */}
      <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-12">
        <div className="md:col-span-7">
          <StatCard tone="yellow" size="lg" label="Present" value={att?.PRESENT ?? 0}
                    sub={attTotal ? `${Math.round(((att?.PRESENT ?? 0) / attTotal) * 100)}% of ${attTotal} attendance records` : "no attendance records yet"} />
        </div>
        <div className="md:col-span-5">
          <StatCard tone="peach" label="Open anomalies" value={openAnomalies}
                    sub={highSeverity > 0 ? `${highSeverity} high severity among the latest` : "none high severity among the latest"} />
        </div>
      </div>
      <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard tone="blue" label="Likely present" value={att?.LIKELY_PRESENT ?? 0} sub={`${att?.REVIEW_REQUIRED ?? 0} need review · ${att?.ABSENT ?? 0} absent`} />
        <StatCard tone="green" label="Live nodes online" value={summary?.live_nodes_online.length ?? 0} sub={`${summary?.active_classrooms.length ?? 0} active classrooms`} />
        <StatCard tone="purple" label="Verified cases" value={summary?.verified_cases ?? 0} sub="feeding RAG retrieval" />
      </div>
      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatPill label="BLE devices seen" value={summary?.ble_devices_seen ?? 0} />
        <StatPill label="Wi-Fi APs registered" value={summary?.wifi_access_points_registered ?? 0} />
        <StatPill label="Wi-Fi survey samples" value={summary?.wifi_survey_samples ?? 0} />
      </div>

      {/* Row 2: charts, all from the same API responses as the tiles */}
      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="Students by zone" note="Latest zone estimate per student, last 15 min (room-level, not GPS).">
          {zones.length === 0 ? <Empty text="No recent location evidence. Run a simulation or start a live session." /> : (
            <div className="h-60">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={zones} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <CartesianGrid horizontal={false} stroke="#edf3f1" />
                  <XAxis type="number" allowDecimals={false} {...AXIS} />
                  <YAxis type="category" dataKey="zone" width={64} {...AXIS} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f5f7fa" }} />
                  <Bar isAnimationActive={false} dataKey="n" name="Students" fill="#0088cc" radius={[0, 8, 8, 0]} barSize={14} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>

        <ChartCard title="Present per session" note="Most recent sessions, oldest to newest.">
          {bySession.length === 0 ? <Empty text="No sessions yet." /> : (
            <div className="h-60">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={bySession} margin={{ left: -16, right: 8 }}>
                  <CartesianGrid vertical={false} stroke="#edf3f1" />
                  <XAxis dataKey="label" {...AXIS} />
                  <YAxis allowDecimals={false} {...AXIS} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f5f7fa" }} />
                  <Bar isAnimationActive={false} dataKey="PRESENT" name="Present" fill={STATE_COLOR.PRESENT} radius={[10, 10, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>

        <ChartCard title="Attendance outcome" note="All sessions, by evidence-based state.">
          {donut.length === 0 ? <Empty text="No attendance records yet." /> : (
            <div className="relative h-60">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie isAnimationActive={false} data={donut} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="82%" paddingAngle={2} cornerRadius={6} stroke="none">
                    {donut.map((d) => <Cell key={d.key} fill={STATE_COLOR[d.key]} />)}
                  </Pie>
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-x-0 top-[38%] text-center">
                <div className="text-3xl font-bold tabular-nums" style={{ color: "#1e293b" }}>{attTotal}</div>
                <div className="text-xs" style={{ color: "var(--text-dim)" }}>records</div>
              </div>
            </div>
          )}
        </ChartCard>

        <ChartCard title="Outcome trend by session" note="Counts per state across the most recent sessions.">
          {bySession.length < 2 ? <Empty text="Needs at least two sessions." /> : (
            <div className="h-60">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={bySession} margin={{ left: -16, right: 8 }}>
                  <CartesianGrid vertical={false} stroke="#edf3f1" />
                  <XAxis dataKey="label" {...AXIS} />
                  <YAxis allowDecimals={false} {...AXIS} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                  {STATES.map((k) => (
                    <Line isAnimationActive={false} key={k} type="monotone" dataKey={k} name={STATE_LABEL[k]} stroke={STATE_COLOR[k]} strokeWidth={2.5} dot={false} activeDot={{ r: 5 }} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>
      </div>

      <Card>
        <h2 className="mb-3 text-[15px] font-bold" style={{ color: "#1e293b" }}>Recent anomalies</h2>
        {anomalies.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-faint)" }}>
            No anomalies recorded yet. Run a simulation from Demo Control to generate some.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {anomalies.map((a) => (
              <div key={a.id} className="flex items-center justify-between gap-3 px-3 py-2.5 text-sm"
                   style={{ background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)" }}>
                <div className="flex min-w-0 items-center gap-3">
                  <SeverityBadge severity={a.rule_details[0]?.severity ?? (a.isolation_flagged ? "medium" : "low")} />
                  <span className="truncate font-medium" style={{ color: "var(--text)" }}>{a.name}</span>
                  <span className="hidden truncate sm:inline" style={{ color: "var(--text-dim)" }}>
                    {a.rules.length > 0 ? a.rules.join(", ").replace(/_/g, " ") : "unusual signal combination"}
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
