import { useEffect, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Card } from "../components/Card";
import { api } from "../services/api";
import type { FeedbackRecord } from "../types/api";

export function Feedback() {
  const [rows, setRows] = useState<FeedbackRecord[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .feedbackHistory()
      .then(setRows)
      .finally(() => setLoading(false));
  }, []);

  const confirmed = rows.filter((r) => r.decision === "confirmed").length;
  const falsePositive = rows.filter((r) => r.decision === "false_positive").length;

  return (
    <div>
      <PageHeader
        title="Feedback"
        subtitle="Every Confirm / False Positive decision faculty have made. Each one becomes a verified case the RAG layer can retrieve."
      />

      {!loading && rows.length > 0 && (
        <div className="mb-5 flex gap-4 text-sm" style={{ color: "var(--text-dim)" }}>
          <span><strong style={{ color: "var(--red)" }}>{confirmed}</strong> confirmed</span>
          <span><strong style={{ color: "var(--green)" }}>{falsePositive}</strong> false positive</span>
          <span><strong style={{ color: "var(--text)" }}>{rows.length}</strong> total</span>
        </div>
      )}

      <Card className="p-0">
        {!loading && rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm" style={{ color: "var(--text-faint)" }}>
            No feedback submitted yet. Resolve an anomaly from the Anomalies page to see it here.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide" style={{ borderColor: "var(--border)", color: "var(--text-faint)" }}>
                <th className="px-4 py-3 font-medium">Anomaly</th>
                <th className="px-4 py-3 font-medium">Student</th>
                <th className="px-4 py-3 font-medium">Decision</th>
                <th className="px-4 py-3 font-medium">Comment</th>
                <th className="px-4 py-3 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.id} className="border-b last:border-0" style={{ borderColor: "var(--border-soft)" }}>
                  <td className="px-4 py-2.5" style={{ color: "var(--text-dim)" }}>
                    #{f.anomaly_id} {f.anomaly_type && <span>· {f.anomaly_type.replace(/_/g, " ")}</span>}
                  </td>
                  <td className="px-4 py-2.5" style={{ color: "var(--text)" }}>{f.student_id ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium"
                      style={{
                        background: f.decision === "confirmed" ? "var(--red-soft)" : "var(--green-soft)",
                        color: f.decision === "confirmed" ? "var(--red)" : "var(--green)",
                      }}
                    >
                      {f.decision === "confirmed" ? "Confirmed" : "False Positive"}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 max-w-xs truncate" style={{ color: "var(--text-dim)" }} title={f.comment ?? ""}>
                    {f.comment ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-xs" style={{ color: "var(--text-faint)" }}>
                    {new Date(f.ts * 1000).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
