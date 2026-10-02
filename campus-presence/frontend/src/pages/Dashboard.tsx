import { useEffect, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card, StatCard } from "../components/Card";
import { SeverityBadge } from "../components/Badge";
import { api } from "../services/api";
import type { AnomalyRow, SummaryResponse } from "../types/api";

export function Dashboard() {
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [anomalies, setAnomalies] = useState<AnomalyRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      Promise.all([api.summary(), api.anomalies(undefined, "all", 6)])
        .then(([s, a]) => {
          if (cancelled) return;
          setSummary(s);
          setAnomalies(a.anomalies);
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
  const openAnomalies = (summary?.anomalies?.open ?? 0) + (summary?.anomalies?.confirmed ?? 0);
  const highSeverity = anomalies.filter((a) => a.rule_details.some((r) => r.severity === "high") || a.isolation_flagged).length;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Campus-wide presence summary, refreshed every 10 seconds."
        actions={
          summary && (
            <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-faint)" }}>
              <span className="h-1.5 w-1.5 rounded-full pulse" style={{ background: "var(--green)" }} />
              {summary.students.live} live · {summary.students.simulated} simulated
            </div>
          )
        }
      />

      {error && (
        <div className="mb-5 rounded-xl border px-4 py-3 text-sm" style={{ borderColor: "var(--red)", background: "var(--red-soft)", color: "var(--red)" }}>
          Couldn't reach the backend: {error}
        </div>
      )}

      <div className="mb-5 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Present" value={att?.PRESENT ?? 0} accent="var(--green)" />
        <StatCard label="Likely Present" value={att?.LIKELY_PRESENT ?? 0} accent="var(--accent)" />
        <StatCard label="Review Required" value={att?.REVIEW_REQUIRED ?? 0} accent="var(--amber)" />
        <StatCard label="Absent" value={att?.ABSENT ?? 0} accent="var(--red)" />
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Active Classrooms" value={summary?.active_classrooms.length ?? 0} />
        <StatCard label="Live Nodes Online" value={summary?.live_nodes_online.length ?? 0} />
        <StatCard
          label="Open Anomalies"
          value={openAnomalies}
          accent={openAnomalies > 0 ? "var(--amber)" : "var(--text)"}
          sub={highSeverity > 0 ? `${highSeverity} high severity` : "none high severity"}
        />
        <StatCard label="Verified Cases" value={summary?.verified_cases ?? 0} sub="feeding RAG retrieval" />
      </div>

      <Card>
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Recent anomalies
        </h2>
        {anomalies.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-faint)" }}>
            No anomalies recorded yet. Run a simulation from Demo Control to generate some.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {anomalies.map((a) => (
              <div
                key={a.id}
                className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm"
                style={{ background: "var(--bg-elevated)" }}
              >
                <div className="flex min-w-0 items-center gap-3">
                  <SeverityBadge severity={a.rule_details[0]?.severity ?? (a.isolation_flagged ? "medium" : "low")} />
                  <span className="truncate" style={{ color: "var(--text)" }}>{a.name}</span>
                  <span className="truncate" style={{ color: "var(--text-dim)" }}>
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
