import type { AttendanceState } from "./types/api";

/** Chart fills: saturated enough to read on white, same hue families as the pastel cards. */
export const STATE_COLOR: Record<AttendanceState, string> = {
  PRESENT: "#3fae7a",
  LIKELY_PRESENT: "#0088cc",
  REVIEW_REQUIRED: "#e8b93a",
  ABSENT: "#e58a6a",
};

export const STATE_LABEL: Record<AttendanceState, string> = {
  PRESENT: "Present",
  LIKELY_PRESENT: "Likely present",
  REVIEW_REQUIRED: "Review required",
  ABSENT: "Absent",
};

export const NEUTRAL_COLOR = "#94a3b8";

export const TOOLTIP_STYLE = {
  background: "#ffffff",
  border: "none",
  borderRadius: 12,
  boxShadow: "0 8px 24px rgba(0,0,0,0.10)",
  fontSize: 12,
  color: "#1e293b",
} as const;
