import { useEffect, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { SourceBadge } from "../components/Badge";
import { api } from "../services/api";
import type { FeedbackSummaryResponse } from "../types/api";

export function Feedback() {
  const [data, setData] = useState<FeedbackSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .feedbackSummary()
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  const rows = data?.feedback ?? [];
  const confirmed = rows.filter((r) => r.action === "confirm").length;
  const falsePositive = rows.filter((r) => r.action === "false_positive").length;

  return (
    <div>
      <PageHeader
        title="Feedback"
        subtitle="Every Confirm / False Positive decision faculty have made. Each one becomes a verified case the RAG layer can retrieve, and feeds the retraining dataset."
      />

      {!loading && rows.length > 0 && (
        <div className="mb-5 flex gap-4 text-sm" style={{ color: "var(--text-dim)" }}>
          <span><strong style={{ color: "var(--red)" }}>{confirmed}</strong> confirmed</span>
          <span><strong style={{ color: "var(--green)" }}>{falsePositive}</strong> false positive</span>
          <span><strong style={{ color: "var(--text)" }}>{data?.training_rows ?? 0}</strong> retraining rows ready</span>
        </div>
      )}

      <Card className="mb-5 p-0">
        {!loading && rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm" style={{ color: "var(--text-faint)" }}>
            No feedback submitted yet. Resolve an anomaly from the Anomalies page to see it here.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide" style={{ borderColor: "var(--border)", color: "var(--text-faint)" }}>
                <th className="px-4 py-3 font-medium">Anomaly</th>
                <th className="px-4 py-3 font-medium">Faculty</th>
                <th className="px-4 py-3 font-medium">Decision</th>
                <th className="px-4 py-3 font-medium">Comment</th>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.id} className="border-b last:border-0" style={{ borderColor: "var(--border-soft)" }}>
                  <td className="px-4 py-2.5" style={{ color: "var(--text-dim)" }}>#{f.anomaly_id}</td>
                  <td className="px-4 py-2.5" style={{ color: "var(--text)" }}>{f.user}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium"
                      style={{
                        background: f.action === "confirm" ? "var(--red-soft)" : "var(--green-soft)",
                        color: f.action === "confirm" ? "var(--red)" : "var(--green)",
                      }}
                    >
                      {f.action === "confirm" ? "Confirmed" : f.action === "false_positive" ? "False Positive" : "Comment"}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 max-w-xs truncate" style={{ color: "var(--text-dim)" }} title={f.comment ?? ""}>
                    {f.comment ?? "—"}
                  </td>
                  <td className="px-4 py-2.5"><SourceBadge source={f.source} /></td>
                  <td className="px-4 py-2.5 text-xs" style={{ color: "var(--text-faint)" }}>
                    {new Date(f.ts * 1000).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card>
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text)" }}>
          Verified cases ({data?.verified_cases.length ?? 0})
        </h2>
        <p className="mb-3 text-xs" style={{ color: "var(--text-faint)" }}>
          What RAG retrieves for new anomaly explanations. Seeded with example cases, grown by faculty feedback above.
        </p>
        <div className="flex flex-col gap-2">
          {(data?.verified_cases ?? []).map((c) => (
            <div key={c.case_id} className="rounded-lg px-3 py-2 text-xs" style={{ background: "var(--bg-elevated)" }}>
              <div className="flex items-center justify-between">
                <span className="font-medium" style={{ color: "var(--text)" }}>{c.title}</span>
                <span style={{ color: c.resolution === "confirmed_anomaly" ? "var(--red)" : "var(--green)" }}>
                  {c.resolution.replace(/_/g, " ")}
                </span>
              </div>
              <div className="mt-1" style={{ color: "var(--text-dim)" }}>{c.summary}</div>
              <div className="mt-1" style={{ color: "var(--text-faint)" }}>{c.origin.replace(/_/g, " ")}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
