import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl border p-5 ${className}`}
      style={{ background: "var(--bg-card)", borderColor: "var(--border)" }}
    >
      {children}
    </div>
  );
}

export function StatCard({
  label, value, sub, accent = "var(--text)",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  accent?: string;
}) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-faint)" }}>
        {label}
      </span>
      <span className="text-3xl font-semibold tabular-nums" style={{ color: accent }}>
        {value}
      </span>
      {sub && <span className="text-xs" style={{ color: "var(--text-dim)" }}>{sub}</span>}
    </Card>
  );
}
