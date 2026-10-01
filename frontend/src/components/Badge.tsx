import type { AttendanceState } from "../types/api";

const STATE_STYLE: Record<AttendanceState, { bg: string; fg: string; label: string }> = {
  present: { bg: "var(--green-soft)", fg: "var(--green)", label: "Present" },
  likely_present: { bg: "var(--accent-soft)", fg: "var(--accent)", label: "Likely Present" },
  review_required: { bg: "var(--amber-soft)", fg: "var(--amber)", label: "Review Required" },
  absent: { bg: "var(--red-soft)", fg: "var(--red)", label: "Absent" },
};

export function StateBadge({ state }: { state: AttendanceState }) {
  const s = STATE_STYLE[state] ?? STATE_STYLE.absent;
  return (
    <span
      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ background: s.bg, color: s.fg }}
    >
      {s.label}
    </span>
  );
}

export function SourceBadge({ source }: { source: "live" | "simulated" }) {
  const live = source === "live";
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase whitespace-nowrap"
      style={{
        background: live ? "var(--green-soft)" : "var(--border-soft)",
        color: live ? "var(--green)" : "var(--text-dim)",
      }}
    >
      {live && <span className="h-1.5 w-1.5 rounded-full pulse" style={{ background: "var(--green)" }} />}
      {live ? "Live" : "Simulated"}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const style: Record<string, { bg: string; fg: string }> = {
    high: { bg: "var(--red-soft)", fg: "var(--red)" },
    medium: { bg: "var(--amber-soft)", fg: "var(--amber)" },
    low: { bg: "var(--border-soft)", fg: "var(--text-dim)" },
    none: { bg: "var(--border-soft)", fg: "var(--text-faint)" },
  };
  const s = style[severity] ?? style.low;
  return (
    <span
      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize whitespace-nowrap"
      style={{ background: s.bg, color: s.fg }}
    >
      {severity}
    </span>
  );
}
