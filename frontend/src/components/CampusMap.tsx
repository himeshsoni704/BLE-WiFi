import { useMemo } from "react";
import type { Classroom, LocationRow } from "../types/api";

const ROOM_W = 10;
const ROOM_H = 8;
const PADDING = 6;

const STATE_COLOR: Record<string, string> = {
  present: "#34d399",
  likely_present: "#5b8cff",
  review_required: "#fbbf24",
  absent: "#f87171",
};

interface Props {
  classrooms: Classroom[];
  students: LocationRow[];
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

  const byClassroom = useMemo(() => {
    const map = new Map<string, LocationRow[]>();
    for (const s of students) {
      const list = map.get(s.classroom_id) ?? [];
      list.push(s);
      map.set(s.classroom_id, list);
    }
    return map;
  }, [students]);

  if (classrooms.length === 0) {
    return (
      <div
        className="flex h-full min-h-[320px] items-center justify-center rounded-xl border text-sm"
        style={{ background: "var(--bg-card)", borderColor: "var(--border)", color: "var(--text-faint)" }}
      >
        No classrooms registered yet. Run a simulation or register a classroom to see the campus map.
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
        const occupants = byClassroom.get(c.classroom_id) ?? [];
        const rx = c.x - ROOM_W / 2;
        const ry = c.y - ROOM_H / 2;
        return (
          <g key={c.classroom_id}>
            <rect
              x={rx} y={ry} width={ROOM_W} height={ROOM_H} rx={0.4}
              fill="var(--bg-elevated)" stroke="var(--border)" strokeWidth={0.12}
            />
            <text
              x={c.x} y={ry + 0.9} textAnchor="middle" fontSize={0.62}
              fill="var(--text-dim)" fontWeight={600}
            >
              {c.display_name ?? c.name}
            </text>
            {occupants.length > 0 && (
              <text x={c.x} y={ry + ROOM_H - 0.4} textAnchor="middle" fontSize={0.5} fill="var(--text-faint)">
                {occupants.length} present
              </text>
            )}
            {occupants.map((s, i) => {
              const cols = Math.max(1, Math.ceil(Math.sqrt(occupants.length)));
              const row = Math.floor(i / cols);
              const col = i % cols;
              const gridW = ROOM_W - 2;
              const gridH = ROOM_H - 3;
              const px = rx + 1 + (col + 0.5) * (gridW / cols);
              const py = ry + 1.7 + (row + 0.5) * (gridH / Math.max(1, Math.ceil(occupants.length / cols)));
              const color = STATE_COLOR[s.state] ?? "#8d97ab";
              const selected = s.student_id === selectedId;
              return (
                <g
                  key={s.student_id}
                  onClick={() => onSelect(s.student_id)}
                  style={{ cursor: "pointer" }}
                  className="fade-in"
                >
                  {selected && (
                    <circle cx={px} cy={py} r={0.45} fill="none" stroke={color} strokeWidth={0.1} opacity={0.7} />
                  )}
                  <circle
                    cx={px} cy={py} r={0.3} fill={color}
                    stroke={s.source === "live" ? "#fff" : "none"} strokeWidth={0.08}
                    className={s.source === "live" ? "pulse" : undefined}
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
