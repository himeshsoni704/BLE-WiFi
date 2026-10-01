import type {
  AnomalyDto, Classroom, DemoInjectKind, DemoInjectResult, EvidenceDetail,
  ExplainResponse, FeedbackRecord, LocationsFeed, RetrievedCase, SimulationSummary, Student,
} from "../types/api";

const BASE = "/api";
const API_KEY = import.meta.env.VITE_API_KEY as string | undefined;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { ...headers, ...(init?.headers ?? {}) } });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${path}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ ok: boolean }>("/health"),

  listStudents: () => request<Student[]>("/students"),

  listClassrooms: () => request<Classroom[]>("/classrooms"),
  putClassroom: (id: string, body: Partial<Classroom>) =>
    request(`/classrooms/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),

  locations: (at?: number) => request<LocationsFeed>(`/locations${at ? `?at=${at}` : ""}`),

  evidence: (studentId: string, historyS?: number) =>
    request<EvidenceDetail>(`/evidence/${encodeURIComponent(studentId)}${historyS ? `?history_s=${historyS}` : ""}`),

  anomalies: (status?: string) => request<AnomalyDto[]>(`/anomalies${status ? `?status=${status}` : ""}`),

  submitFeedback: (anomalyId: number, decision: "false_positive" | "confirmed", comment?: string) =>
    request<{ accepted: boolean }>("/feedback", {
      method: "POST",
      body: JSON.stringify({ anomaly_id: anomalyId, decision, comment }),
    }),

  feedbackHistory: (limit = 200) => request<FeedbackRecord[]>(`/feedback?limit=${limit}`),

  explainAnomaly: (anomalyId: number) =>
    request<ExplainResponse>("/explain-anomaly", {
      method: "POST",
      body: JSON.stringify({ anomaly_id: anomalyId }),
    }),

  ragRetrieve: (query: string, k = 3) =>
    request<{ query: string; results: RetrievedCase[] }>("/rag/retrieve", {
      method: "POST",
      body: JSON.stringify({ query, k }),
    }),

  simulationStart: (students = 300, classrooms = 20, aps = 10, anomalyRate = 0.08, seed = 0) =>
    request<SimulationSummary>("/simulation/start", {
      method: "POST",
      body: JSON.stringify({ students, classrooms, aps, anomaly_rate: anomalyRate, seed }),
    }),

  simulationInject: (kind: DemoInjectKind, studentId?: string) =>
    request<DemoInjectResult>("/simulation/inject", {
      method: "POST",
      body: JSON.stringify({ kind, student_id: studentId }),
    }),

  simulationReset: () => request<{ reset: boolean }>("/simulation/reset", { method: "POST" }),
};

export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const key = API_KEY ? `?key=${encodeURIComponent(API_KEY)}` : "";
  return `${proto}//${window.location.host}${path}${key}`;
}
