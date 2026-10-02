import type { AttendanceState, Source } from "../types/api";

const STATE_STYLE: Record<AttendanceState, { bg: string; fg: string; label: string }> = {
  PRESENT: { bg: "var(--green-soft)", fg: "var(--green)", label: "Present" },
  LIKELY_PRESENT: { bg: "var(--accent-soft)", fg: "var(--accent)", label: "Likely Present" },
  REVIEW_REQUIRED: { bg: "var(--amber-soft)", fg: "var(--amber)", label: "Review Required" },
  ABSENT: { bg: "var(--red-soft)", fg: "var(--red)", label: "Absent" },
};

export function StateBadge({ state }: { state: AttendanceState }) {
  const s = STATE_STYLE[state] ?? STATE_STYLE.ABSENT;
  return (
    <span
      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ background: s.bg, color: s.fg }}
    >
      {s.label}
    </span>
  );
}

export function SourceBadge({ source }: { source: Source }) {
  const live = source === "LIVE";
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
    warn: { bg: "var(--amber-soft)", fg: "var(--amber)" },
    medium: { bg: "var(--amber-soft)", fg: "var(--amber)" },
    info: { bg: "var(--border-soft)", fg: "var(--text-dim)" },
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
