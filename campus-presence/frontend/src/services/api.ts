import type {
  AnomaliesResponse, AnomalyDetail, AttendanceResponse, Classroom, ConfigResponse, EvidenceDetail,
  ExplainResponse, FeedbackSubmitResponse, FeedbackSummaryResponse, LocationsResponse, LoginResponse, Me,
  SessionRow, SimulationStartResponse, SimulationStatus, Source, StudentsResponse, SummaryResponse,
  VerifiedCaseEntry,
} from "../types/api";

// Dev: the Vite proxy maps /api to the backend. Built app served by the backend itself: same origin.
// Set VITE_API_BASE to point a build at a backend on another origin. A static host such as Vercel builds with
// VITE_ALLOW_BACKEND_URL=1, which lets the sign-in page store the backend's address (for example an HTTPS tunnel to
// a laptop) in this browser, so a changing tunnel address needs no rebuild.
const BASE_KEY = "cp_api_base";
const ENV_BASE: string = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "/api" : "");
export const BACKEND_URL_CONFIGURABLE: boolean = import.meta.env.VITE_ALLOW_BACKEND_URL === "1";

function storedBase(): string {
  if (!BACKEND_URL_CONFIGURABLE) return "";
  try {
    return localStorage.getItem(BASE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function getApiBase(): string {
  return storedBase() || ENV_BASE;
}

/** Remember the backend address (only on builds that allow it). Normalises away a trailing slash. */
export function setApiBase(url: string): void {
  if (!BACKEND_URL_CONFIGURABLE) return;
  const clean = url.trim().replace(/\/+$/, "");
  try {
    if (clean) localStorage.setItem(BASE_KEY, clean);
    else localStorage.removeItem(BASE_KEY);
  } catch {
    /* storage unavailable: the address just will not be remembered */
  }
}

const TOKEN_KEY = "cp_token";

let token: string | null = localStorage.getItem(TOKEN_KEY);
const listeners = new Set<(t: string | null) => void>();

export function getToken(): string | null {
  return token;
}

export function setToken(t: string | null): void {
  token = t;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
  listeners.forEach((fn) => fn(t));
}

export function onTokenChange(fn: (t: string | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const base = getApiBase();
  // ngrok's free tier puts a warning page in front of API calls unless this header is present.
  if (base.includes("ngrok")) headers["ngrok-skip-browser-warning"] = "1";
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, { ...init, headers: { ...headers, ...(init?.headers ?? {}) } });
  } catch {
    throw new Error(`Can't reach the backend${base ? ` at ${base}` : ""}. Is it running, and does CORS_ORIGINS allow this site?`);
  }
  if (res.status === 401) {
    setToken(null);
    throw new Error("401 session expired, please log in again");
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${path}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const parts = Object.entries(params).filter(([, v]) => v !== undefined).map(
    ([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export const api = {
  health: () => request<{ ok: boolean }>("/health"),
  config: () => request<ConfigResponse>("/config"),

  login: (username: string, password: string) =>
    request<LoginResponse>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  me: () => request<Me>("/auth/me"),

  listStudents: (source: Source | "all" = "all", q?: string) =>
    request<StudentsResponse>(`/students${qs({ source, q })}`),

  listClassrooms: () => request<Classroom[]>("/classrooms"),

  locations: (source: Source | "all" = "all", windowS = 900) =>
    request<LocationsResponse>(`/locations${qs({ source, window_s: windowS })}`),

  listSessions: (source: Source | "all" = "all") => request<SessionRow[]>(`/sessions${qs({ source })}`),

  attendance: (sessionId?: number, source: Source | "all" = "all") =>
    request<AttendanceResponse>(`/attendance${qs({ session_id: sessionId, source })}`),

  evidence: (studentId: string, sessionId?: number) =>
    request<EvidenceDetail>(`/evidence/${encodeURIComponent(studentId)}${qs({ session_id: sessionId })}`),

  anomalies: (status?: string, source: Source | "all" = "all", limit = 200) =>
    request<AnomaliesResponse>(`/anomalies${qs({ status, source, limit })}`),
  anomalyDetail: (id: number) => request<AnomalyDetail>(`/anomalies/${id}`),

  explainAnomaly: (anomalyId: number, provider?: "gemini" | "mock") =>
    request<ExplainResponse>("/explain-anomaly", {
      method: "POST",
      body: JSON.stringify({ anomaly_id: anomalyId, provider }),
    }),

  ragRetrieve: (opts: { anomalyId?: number; query?: string; k?: number }) =>
    request<{ tags_used: string[]; index_size: number; method: string; cases: VerifiedCaseEntry[] }>(
      "/rag/retrieve",
      { method: "POST", body: JSON.stringify({ anomaly_id: opts.anomalyId, query: opts.query, k: opts.k ?? 3 }) },
    ),

  submitFeedback: (anomalyId: number, action: "confirm" | "false_positive" | "comment", comment?: string) =>
    request<FeedbackSubmitResponse>("/feedback", {
      method: "POST",
      body: JSON.stringify({ anomaly_id: anomalyId, action, comment }),
    }),
  feedbackSummary: () => request<FeedbackSummaryResponse>("/feedback/summary"),

  summary: (source: Source | "all" = "all") => request<SummaryResponse>(`/summary${qs({ source })}`),

  createSession: (code: string, title: string, classroomId: string, minutes = 60) =>
    request<{ id: number; code: string; room: string; start_ts: number; end_ts: number; enrolled_live_students: number }>(
      "/sessions", { method: "POST", body: JSON.stringify({ code, title, classroom_id: classroomId, minutes }) }),

  simulationStart: (scenario: string, students = 300, seed = 1, wait = false) =>
    request<SimulationStartResponse>("/simulation/start", {
      method: "POST",
      body: JSON.stringify({ scenario, students, seed, wait }),
    }),
  simulationReset: () => request<{ status: string; deleted_simulated_rows: Record<string, number>; kept: string }>(
    "/simulation/reset", { method: "POST" }),
  simulationStatus: () => request<SimulationStatus>("/simulation/status"),
};

export function wsUrl(path: string): string {
  const t = token ? `?token=${encodeURIComponent(token)}` : "";
  const base = getApiBase();
  if (/^https?:\/\//.test(base)) {
    // backend on another origin: same host as the API, ws or wss to match
    return `${base.replace(/^http/, "ws")}${path}${t}`;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}${t}`;
}
