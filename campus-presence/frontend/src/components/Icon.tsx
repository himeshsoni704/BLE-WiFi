/** Outline (line) icons, 24x24 grid, drawn with currentColor. */
const PATHS: Record<string, string> = {
  dashboard: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  map: "M12 21s-6-5.4-6-10a6 6 0 1 1 12 0c0 4.6-6 10-6 10zM12 8.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  attendance: "M9 11l3 3 5-6M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z",
  alert: "M12 4l9 16H3zM12 10v4M12 17v.5",
  evidence: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l4 4M8.5 11h5M11 8.5v5",
  feedback: "M4 5h16v11H9l-5 4zM8 9h8M8 12h5",
  play: "M8 5l11 7-11 7z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l4 4",
  bell: "M6 17V11a6 6 0 1 1 12 0v6l1.5 2h-15zM10 21h4",
  chevron: "M9 6l6 6-6 6",
  logout: "M10 5H5v14h5M14 8l4 4-4 4M18 12H9",
};

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}

export function BrandMark({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <rect width="40" height="40" rx="12" fill="#0088cc" />
      <circle cx="20" cy="20" r="3.2" fill="#fff" />
      <path d="M12.5 20a7.5 7.5 0 0 1 15 0M8 20a12 12 0 0 1 24 0" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" opacity="0.85" />
    </svg>
  );
}
