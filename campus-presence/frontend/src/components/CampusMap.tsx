import { useMemo } from "react";
import type { AttendanceState, Classroom, LocationStudent } from "../types/api";

const ROOM_W = 10;
const ROOM_H = 8;
const CORRIDOR_W = 96;
const CORRIDOR_H = 3;
const PADDING = 6;

const STATE_COLOR: Record<AttendanceState, string> = {
  PRESENT: "#34d399",
  LIKELY_PRESENT: "#5b8cff",
  REVIEW_REQUIRED: "#fbbf24",
  ABSENT: "#f87171",
};
const SIGNAL_COLOR = "#8d97ab";

export type MapStudent = LocationStudent & { state?: AttendanceState };

interface Props {
  classrooms: Classroom[];
  students: MapStudent[];
  selectedId: string | null;
  onSelect: (studentId: string) => void;
}

export function CampusMap({ classrooms, students, selectedId, onSelect }: Props) {
  const { minX, minY, width, height } = useMemo(() => {
    if (classrooms.length === 0) return { minX: 0, minY: 0, width: 100, height: 100 };
    const xs = classrooms.map((c) => c.x);
    const ys = classrooms.map((c) => c.y);
    const minX = Math.min(...xs) - ROOM_W / 2 - PADDING;
    const maxX = Math.max(...xs) + ROOM_W / 2 + PADDING;
    const minY = Math.min(...ys) - ROOM_H / 2 - PADDING;
    const maxY = Math.max(...ys) + ROOM_H / 2 + PADDING;
    return { minX, minY, width: maxX - minX, height: maxY - minY };
  }, [classrooms]);

  const byZone = useMemo(() => {
    const map = new Map<string, MapStudent[]>();
    for (const s of students) {
      const list = map.get(s.zone) ?? [];
      list.push(s);
      map.set(s.zone, list);
    }
    return map;
  }, [students]);

  if (classrooms.length === 0) {
    return (
      <div
        className="flex h-full min-h-[320px] items-center justify-center rounded-xl border text-sm"
        style={{ background: "var(--bg-card)", borderColor: "var(--border)", color: "var(--text-faint)" }}
      >
        No classrooms registered yet. Run a simulation to see the campus map.
      </div>
    );
  }

  return (
    <svg
      viewBox={`${minX} ${minY} ${width} ${height}`}
      className="h-full w-full rounded-xl border"
      style={{ background: "var(--bg-card)", borderColor: "var(--border)", minHeight: 340 }}
    >
      {classrooms.map((c) => {
        const occupants = byZone.get(c.id) ?? [];
        const w = c.is_corridor ? CORRIDOR_W : ROOM_W;
        const h = c.is_corridor ? CORRIDOR_H : ROOM_H;
        const rx = c.x - w / 2;
        const ry = c.y - h / 2;
        return (
          <g key={c.id}>
            <rect
              x={rx} y={ry} width={w} height={h} rx={0.4}
              fill={c.is_corridor ? "transparent" : "var(--bg-elevated)"}
              stroke="var(--border)" strokeWidth={c.is_corridor ? 0.06 : 0.12}
              strokeDasharray={c.is_corridor ? "0.6 0.6" : undefined}
            />
            {!c.is_corridor && (
              <>
                <text x={c.x} y={ry + 0.9} textAnchor="middle" fontSize={0.62} fill="var(--text-dim)" fontWeight={600}>
                  {c.name}
                </text>
                {occupants.length > 0 && (
                  <text x={c.x} y={ry + h - 0.4} textAnchor="middle" fontSize={0.5} fill="var(--text-faint)">
                    {occupants.length} here
                  </text>
                )}
              </>
            )}
            {occupants.map((s, i) => {
              const cols = Math.max(1, Math.ceil(Math.sqrt(occupants.length) * (c.is_corridor ? 3 : 1)));
              const row = Math.floor(i / cols);
              const col = i % cols;
              const gridW = w - (c.is_corridor ? 2 : 2);
              const gridH = c.is_corridor ? h - 1 : h - 3;
              const px = rx + 1 + (col + 0.5) * (gridW / cols);
              const py = (c.is_corridor ? ry + h / 2 : ry + 1.7) + (row + 0.5) * (gridH / Math.max(1, Math.ceil(occupants.length / cols)));
              const color = s.state ? STATE_COLOR[s.state] : SIGNAL_COLOR;
              const selected = s.student_id === selectedId;
              return (
                <g key={s.student_id} onClick={() => onSelect(s.student_id)} style={{ cursor: "pointer" }} className="fade-in">
                  {selected && (
                    <circle cx={px} cy={py} r={0.45} fill="none" stroke={color} strokeWidth={0.1} opacity={0.7} />
                  )}
                  <circle
                    cx={px} cy={py} r={0.3} fill={color}
                    stroke={s.source === "LIVE" ? "#fff" : "none"} strokeWidth={0.08}
                    className={s.source === "LIVE" ? "pulse" : undefined}
                  />
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
