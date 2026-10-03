import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`p-5 ${className}`}
      style={{ background: "var(--bg-card)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-card)" }}
    >
      {children}
    </div>
  );
}

export type Tone = "yellow" | "peach" | "blue" | "green" | "purple" | "neutral";

const TONES: Record<Tone, { bg: string; fg: string }> = {
  yellow: { bg: "#fef6d8", fg: "#7a6218" },
  peach: { bg: "#fde8d7", fg: "#a2512a" },
  blue: { bg: "#e1f0fa", fg: "#2b5876" },
  green: { bg: "#e4f8ec", fg: "#2e7d52" },
  purple: { bg: "#f4e8f8", fg: "#6b3a78" },
  neutral: { bg: "#f5f7fa", fg: "#1e293b" },
};

/** A KPI tile. `tone` picks a pastel fill with matching dark text; without it the tile is a plain white card. */
export function StatCard({
  label, value, sub, accent = "var(--text)", tone, size = "md", icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  accent?: string;
  tone?: Tone;
  size?: "md" | "lg";
  icon?: ReactNode;
}) {
  const t = tone ? TONES[tone] : null;
  return (
    <div
      className={`flex flex-col gap-1 ${size === "lg" ? "p-6" : "p-5"}`}
      style={{
        background: t ? t.bg : "var(--bg-card)",
        borderRadius: "var(--radius-lg)",
        boxShadow: t ? "none" : "var(--shadow-card)",
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium" style={{ color: t ? t.fg : "var(--text-dim)", opacity: t ? 0.85 : 1 }}>
          {label}
        </span>
        {icon && <span style={{ color: t?.fg }}>{icon}</span>}
      </div>
      <span
        className={`font-bold tabular-nums leading-tight ${size === "lg" ? "text-4xl" : "text-3xl"}`}
        style={{ color: t ? t.fg : accent }}
      >
        {value}
      </span>
      {sub && <span className="text-xs" style={{ color: t ? t.fg : "var(--text-dim)", opacity: t ? 0.8 : 1 }}>{sub}</span>}
    </div>
  );
}

/** Small outlined stat pill for secondary counters. */
export function StatPill({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div
      className="flex items-center justify-between gap-3 px-4 py-3"
      style={{ border: "1.5px solid #cfe3dc", borderRadius: "var(--radius-md)", background: "rgba(255,255,255,0.45)" }}
    >
      <span className="text-[13px] font-medium" style={{ color: "var(--text-dim)" }}>{label}</span>
      <span className="text-xl font-bold tabular-nums" style={{ color: "var(--text)" }}>{value}</span>
    </div>
  );
}
