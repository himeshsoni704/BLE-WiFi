import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

const NAV = [
  { to: "/", label: "Dashboard", icon: "▣" },
  { to: "/map", label: "Live Location", icon: "◉" },
  { to: "/attendance", label: "Attendance", icon: "✓" },
  { to: "/anomalies", label: "Anomalies", icon: "⚠" },
  { to: "/evidence", label: "Evidence Explorer", icon: "⌚" },
  { to: "/feedback", label: "Feedback", icon: "⌨" },
  { to: "/demo", label: "Demo Control", icon: "▶" },
];

export function Layout({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  return (
    <div className="flex min-h-screen">
      <aside
        className="flex w-60 shrink-0 flex-col border-r px-3 py-5"
        style={{ background: "var(--bg-elevated)", borderColor: "var(--border)" }}
      >
        <div className="mb-6 px-3">
          <div className="text-sm font-semibold tracking-tight" style={{ color: "var(--text)" }}>
            Proof-of-Presence
          </div>
          <div className="mt-0.5 text-[11px]" style={{ color: "var(--text-faint)" }}>
            Campus attendance &amp; presence
          </div>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? "" : "hover:opacity-80"
                }`
              }
              style={({ isActive }) => ({
                background: isActive ? "var(--accent-soft)" : "transparent",
                color: isActive ? "var(--accent)" : "var(--text-dim)",
              })}
            >
              <span className="w-4 text-center">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-3 px-3 pt-4">
          {me && (
            <div className="flex items-center justify-between rounded-lg px-2 py-1.5 text-xs" style={{ background: "var(--bg-card)" }}>
              <div className="min-w-0">
                <div className="truncate font-medium" style={{ color: "var(--text)" }}>{me.username}</div>
                <div className="capitalize" style={{ color: "var(--text-faint)" }}>{me.role}</div>
              </div>
              <button onClick={logout} className="shrink-0 text-xs font-medium" style={{ color: "var(--accent)" }}>
                Sign out
              </button>
            </div>
          )}
          <div className="text-[11px] leading-relaxed" style={{ color: "var(--text-faint)" }}>
            Decentralized evidence generation with authorized aggregation.
            <br />
            This dashboard is an authorized faculty/admin view, not a public feed.
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl px-8 py-7">{children}</div>
      </main>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight" style={{ color: "var(--text)" }}>
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
