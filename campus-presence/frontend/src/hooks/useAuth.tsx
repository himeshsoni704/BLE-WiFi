import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, onTokenChange, setToken } from "../services/api";
import type { Me } from "../types/api";

interface AuthState {
  token: string | null;
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tok, setTok] = useState<string | null>(getToken());
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => onTokenChange(setTok), []);

  useEffect(() => {
    if (!tok) {
      setMe(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .me()
      .then((m) => !cancelled && setMe(m))
      .catch(() => !cancelled && setMe(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [tok]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    setToken(res.access_token);
  }, []);

  const logout = useCallback(() => setToken(null), []);

  return <AuthContext.Provider value={{ token: tok, me, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
