import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../hooks/useAuth";
import { api } from "../services/api";

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demoMode, setDemoMode] = useState(false);

  useEffect(() => {
    api.config().then((c) => setDemoMode(c.demo_mode)).catch(() => {});
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
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
        className="w-full max-w-sm rounded-xl border p-6"
        style={{ background: "var(--bg-card)", borderColor: "var(--border)" }}
      >
        <div className="mb-1 text-lg font-semibold" style={{ color: "var(--text)" }}>
          Proof-of-Presence
        </div>
        <div className="mb-6 text-sm" style={{ color: "var(--text-faint)" }}>
          Authorized faculty/admin sign-in.
        </div>

        <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-dim)" }}>
          Username
        </label>
        <input
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-4 w-full rounded-lg border px-3 py-2 text-sm outline-none"
          style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
        />

        <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-dim)" }}>
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-5 w-full rounded-lg border px-3 py-2 text-sm outline-none"
          style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text)" }}
        />

        {error && (
          <div className="mb-4 rounded-lg border px-3 py-2 text-xs" style={{ borderColor: "var(--red)", color: "var(--red)" }}>
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !username || !password}
          className="w-full rounded-lg py-2 text-sm font-medium disabled:opacity-50"
          style={{ background: "var(--accent)", color: "#fff" }}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>

        {demoMode && (
          <div className="mt-5 rounded-lg border-l-2 px-3 py-2 text-xs leading-relaxed" style={{ borderColor: "var(--purple)", background: "var(--purple-soft)", color: "var(--text-dim)" }}>
            Demo mode is on: sign in as <strong>faculty</strong> or <strong>admin</strong>, password{" "}
            <strong>demo1234</strong>.
          </div>
        )}
      </form>
    </div>
  );
}
