import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { api } from "../services/api";
import type { StudentRow } from "../types/api";
import { BrandMark, Icon } from "./Icon";

const NAV = [
  { to: "/", label: "Dashboard", icon: "dashboard" },
  { to: "/map", label: "Live Location", icon: "map" },
  { to: "/attendance", label: "Attendance", icon: "attendance" },
  { to: "/anomalies", label: "Anomalies", icon: "alert" },
  { to: "/evidence", label: "Evidence Explorer", icon: "evidence" },
  { to: "/feedback", label: "Feedback", icon: "feedback" },
  { to: "/demo", label: "Demo Control", icon: "play" },
];

function StudentSearch() {
  const navigate = useNavigate();
  const [students, setStudents] = useState<StudentRow[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || students) return;
    api.listStudents().then((r) => setStudents(r.students)).catch(() => setStudents([]));
  }, [open, students]);

  useEffect(() => {
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle || !students) return [];
    return students
      .filter((s) => s.name.toLowerCase().includes(needle) || s.student_id.toLowerCase().includes(needle))
      .slice(0, 6);
  }, [q, students]);

  const go = (id: string) => {
    setOpen(false);
    setQ("");
    navigate(`/evidence?student=${encodeURIComponent(id)}`);
  };

  return (
    <div ref={box} className="relative min-w-0 flex-1 md:max-w-xs">
      <div className="flex items-center gap-2 bg-white px-4 py-2" style={{ borderRadius: 9999, boxShadow: "var(--shadow-card)" }}>
        <span style={{ color: "var(--text-faint)" }}><Icon name="search" size={16} /></span>
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => e.key === "Enter" && hits[0] && go(hits[0].student_id)}
          placeholder="Search student name or ID"
          aria-label="Search students"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          style={{ color: "var(--text)" }}
        />
      </div>
      {open && q.trim() && (
        <div className="absolute left-0 right-0 top-full z-20 mt-2 overflow-hidden bg-white py-1"
             style={{ borderRadius: "var(--radius-md)", boxShadow: "0 12px 32px rgba(0,0,0,0.10)" }}>
          {students === null ? (
            <div className="px-4 py-2 text-xs" style={{ color: "var(--text-faint)" }}>Loading…</div>
          ) : hits.length === 0 ? (
            <div className="px-4 py-2 text-xs" style={{ color: "var(--text-faint)" }}>No student matches “{q}”.</div>
          ) : (
            hits.map((s) => (
              <button key={s.student_id} onClick={() => go(s.student_id)}
                      className="flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm hover:bg-[var(--bg-elevated)]">
                <span className="truncate font-medium" style={{ color: "var(--text)" }}>{s.name}</span>
                <span className="shrink-0 text-xs" style={{ color: "var(--text-faint)" }}>
                  {s.student_id} · {s.source === "LIVE" ? "live" : "simulated"}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function Bell() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api.summary()
        .then((s) => !cancelled && setCount((s.anomalies?.open ?? 0) + (s.anomalies?.confirmed ?? 0)))
        .catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);
  return (
    <NavLink to="/anomalies" aria-label={`${count} open anomalies`} title={`${count} open anomalies`}
             className="relative flex h-10 w-10 items-center justify-center bg-white"
             style={{ borderRadius: 9999, boxShadow: "var(--shadow-card)", color: "var(--text-dim)" }}>
      <Icon name="bell" />
      {count > 0 && (
        <span className="absolute -right-0.5 -top-0.5 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[10px] font-bold"
              style={{ background: "#fde8d7", color: "#a2512a" }}>
          {count > 99 ? "99+" : count}
        </span>
      )}
    </NavLink>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const { pathname } = useLocation();
  const current = NAV.find((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)));

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside
        className="flex shrink-0 flex-col bg-white md:sticky md:top-0 md:h-screen md:w-60 md:px-4 md:py-6"
        style={{ boxShadow: "var(--shadow-card)" }}
      >
        <div className="flex items-center gap-3 px-4 py-3 md:mb-6 md:px-2 md:py-0">
          <BrandMark />
          <div className="min-w-0">
            <div className="text-[15px] font-bold leading-tight tracking-tight" style={{ color: "var(--text)" }}>Proof-of-Presence</div>
            <div className="text-[11px]" style={{ color: "var(--text-faint)" }}>Campus presence evidence</div>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:overflow-visible md:px-0 md:pb-0" aria-label="Main">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className="flex shrink-0 items-center gap-3 whitespace-nowrap px-3 py-2.5 text-sm font-medium transition-colors"
              style={({ isActive }) => ({
                background: isActive ? "var(--accent-soft)" : "transparent",
                color: isActive ? "var(--accent-ink)" : "var(--text-dim)",
                borderRadius: "var(--radius-sm)",
              })}
            >
              <Icon name={item.icon} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden px-2 pt-4 text-[11px] leading-relaxed md:block" style={{ color: "var(--text-faint)" }}>
          Authorized faculty/admin view, not a public feed. Anomalies are prompts for human review, not findings.
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-3 px-4 py-4 md:flex-nowrap md:px-8">
          <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm" style={{ color: "var(--text-dim)" }}>
            <span>Campus</span>
            <Icon name="chevron" size={14} />
            <span className="font-semibold" style={{ color: "var(--text)" }}>{current?.label ?? "Dashboard"}</span>
          </nav>
          <div className="ml-auto flex w-full min-w-0 items-center gap-3 md:w-auto md:flex-1 md:justify-end">
            <StudentSearch />
            <Bell />
            {me && (
              <div className="flex items-center gap-2 bg-white py-1 pl-1 pr-3" style={{ borderRadius: 9999, boxShadow: "var(--shadow-card)" }}>
                <span className="flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold uppercase"
                      style={{ background: "var(--purple-soft)", color: "var(--purple)" }}>
                  {me.username.slice(0, 1)}
                </span>
                <div className="hidden min-w-0 leading-tight sm:block">
                  <div className="truncate text-xs font-semibold" style={{ color: "var(--text)" }}>{me.username}</div>
                  <div className="text-[11px] capitalize" style={{ color: "var(--text-faint)" }}>{me.role}</div>
                </div>
                <button onClick={logout} title="Sign out" aria-label="Sign out" className="ml-1" style={{ color: "var(--text-dim)" }}>
                  <Icon name="logout" size={16} />
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="min-w-0 flex-1">
          <div className="mx-auto max-w-7xl px-4 pb-10 pt-2 md:px-8">{children}</div>
        </main>
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: "#1e293b" }}>
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-sm" style={{ color: "var(--text-dim)" }}>
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
