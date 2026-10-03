import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../hooks/useAuth";
import { api, BACKEND_URL_CONFIGURABLE, getApiBase, setApiBase } from "../services/api";
import { BrandMark } from "../components/Icon";

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [backendUrl, setBackendUrl] = useState(BACKEND_URL_CONFIGURABLE ? getApiBase() : "");

  useEffect(() => {
    api.config().then((c) => setDemoMode(c.demo_mode)).catch(() => {});
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (BACKEND_URL_CONFIGURABLE) setApiBase(backendUrl);
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message.replace(/^\d+\s/, "") : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4" style={{ background: "var(--bg)" }}>
      <form
        onSubmit={submit}
        className="w-full max-w-sm p-7"
        style={{ background: "var(--bg-card)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-card)" }}
      >
        <div className="mb-3"><BrandMark size={40} /></div>
        <div className="mb-1 text-xl font-bold" style={{ color: "#1e293b" }}>
          Proof-of-Presence
        </div>
        <div className="mb-6 text-sm" style={{ color: "var(--text-dim)" }}>
          Authorized faculty/admin sign-in.
        </div>

        {BACKEND_URL_CONFIGURABLE && (
          <>
            <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-dim)" }}>
              Backend URL
            </label>
            <input
              value={backendUrl}
              onChange={(e) => setBackendUrl(e.target.value)}
              placeholder="https://your-tunnel.trycloudflare.com"
              aria-label="Backend URL"
              className="mb-4 w-full rounded-xl border px-3 py-2.5 text-sm outline-none"
              style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
            />
          </>
        )}

        <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-dim)" }}>
          Username
        </label>
        <input
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-4 w-full rounded-xl border px-3 py-2.5 text-sm outline-none"
          style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
        />

        <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-dim)" }}>
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-5 w-full rounded-xl border px-3 py-2.5 text-sm outline-none"
          style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
        />

        {error && (
          <div className="mb-4 rounded-xl px-3 py-2 text-xs" style={{ background: "var(--red-soft)", color: "var(--red)" }}>
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !username || !password}
          className="w-full rounded-xl py-2.5 text-sm font-semibold disabled:opacity-50"
          style={{ background: "var(--accent)", color: "#ffffff" }}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>

        {demoMode && (
          <div className="mt-5 rounded-xl px-3 py-2.5 text-xs leading-relaxed" style={{ background: "var(--purple-soft)", color: "var(--purple)" }}>
            Demo mode is on: sign in as <strong>faculty</strong> or <strong>admin</strong>, password{" "}
            <strong>demo1234</strong>.
          </div>
        )}
      </form>
    </div>
  );
}
