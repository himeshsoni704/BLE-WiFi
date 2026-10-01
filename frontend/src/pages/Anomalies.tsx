import { useEffect, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { SeverityBadge } from "../components/Badge";
import { api } from "../services/api";
import type { AnomalyDto, RetrievedCase } from "../types/api";

const STATUS_TABS = [
  { value: "open", label: "Open" },
  { value: "confirmed", label: "Confirmed" },
  { value: "false_positive", label: "False Positive" },
  { value: "", label: "All" },
];

export function Anomalies() {
  const [status, setStatus] = useState("open");
  const [anomalies, setAnomalies] = useState<AnomalyDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [explanations, setExplanations] = useState<Record<number, { text: string; cases: RetrievedCase[] }>>({});
  const [comments, setComments] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api
      .anomalies(status || undefined)
      .then((rows) => {
        setAnomalies(rows.sort((a, b) => b.ts - a.ts));
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [status]);

  const explain = async (id: number) => {
    setBusy(id);
    try {
      const res = await api.explainAnomaly(id);
      setExplanations((prev) => ({ ...prev, [id]: { text: res.explanation, cases: res.similar_verified_cases } }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const decide = async (id: number, decision: "confirmed" | "false_positive") => {
    setBusy(id);
    try {
      await api.submitFeedback(id, decision, comments[id]?.trim() || undefined);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="Anomalies"
        subtitle="Deterministic rules + Isolation Forest flags. Scores are relative outlier signals, not probabilities."
      />

      <div className="mb-4 flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-elevated)", width: "fit-content" }}>
        {STATUS_TABS.map((t) => (
          <button
            key={t.value}
            onClick={() => setStatus(t.value)}
            className="rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
            style={{
              background: status === t.value ? "var(--bg-card)" : "transparent",
              color: status === t.value ? "var(--text)" : "var(--text-dim)",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border px-3 py-2 text-xs" style={{ borderColor: "var(--red)", color: "var(--red)" }}>
          {error}
        </div>
      )}

      {!loading && anomalies.length === 0 && (
        <Card>
          <p className="text-sm" style={{ color: "var(--text-faint)" }}>
            No {status || ""} anomalies. Trigger one from Demo Control to see the review flow.
          </p>
        </Card>
      )}

      <div className="flex flex-col gap-3">
        {anomalies.map((a) => {
          const exp = explanations[a.id];
          return (
            <Card key={a.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={a.severity} />
                    <span className="font-medium" style={{ color: "var(--text)" }}>{a.student_id}</span>
                    <span style={{ color: "var(--text-dim)" }}>{a.type.replace(/_/g, " ")}</span>
                  </div>
                  <div className="mt-1 text-xs" style={{ color: "var(--text-faint)" }}>
                    {new Date(a.ts * 1000).toLocaleString()}
                    {a.iforest_score !== null && <> · iforest score {a.iforest_score.toFixed(3)}</>}
                    · status {a.status}
                  </div>
                  {a.reasons.length > 0 && (
                    <ul className="mt-2 list-disc pl-4 text-xs" style={{ color: "var(--text-dim)" }}>
                      {a.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <button
                    disabled={busy === a.id}
                    onClick={() => explain(a.id)}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                    style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                  >
                    Explain
                  </button>
                  {a.status === "open" && (
                    <>
                      <button
                        disabled={busy === a.id}
                        onClick={() => decide(a.id, "confirmed")}
                        className="rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                        style={{ background: "var(--red-soft)", color: "var(--red)" }}
                      >
                        Confirm
                      </button>
                      <button
                        disabled={busy === a.id}
                        onClick={() => decide(a.id, "false_positive")}
                        className="rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                        style={{ background: "var(--green-soft)", color: "var(--green)" }}
                      >
                        False Positive
                      </button>
                    </>
                  )}
                </div>
              </div>

              {a.status === "open" && (
                <input
                  value={comments[a.id] ?? ""}
                  onChange={(e) => setComments((prev) => ({ ...prev, [a.id]: e.target.value }))}
                  placeholder="Optional comment for Confirm / False Positive…"
                  className="mt-3 w-full rounded-lg border px-3 py-1.5 text-xs outline-none"
                  style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
                />
              )}

              {(exp || a.explanation) && (
                <div className="mt-3 rounded-lg border-l-2 px-3 py-2 text-xs leading-relaxed" style={{ borderColor: "var(--accent)", background: "var(--bg-elevated)", color: "var(--text-dim)" }}>
                  {exp?.text ?? a.explanation}
                  {exp && exp.cases.length > 0 && (
                    <div className="mt-2 text-xs" style={{ color: "var(--text-faint)" }}>
                      Similar past cases: {exp.cases.map((c) => `${c.resolution} (${(c.similarity * 100).toFixed(0)}%)`).join("; ")}
                    </div>
                  )}
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
