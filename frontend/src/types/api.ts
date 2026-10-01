// Mirrors the backend's JSON shapes (app/schemas.py, app/evidence.py,
// app/anomaly_iforest.py, app/main.py response bodies).

export type AttendanceState = "present" | "likely_present" | "review_required" | "absent";

export interface EvidenceComponent {
  name: string;
  points: number;
  max_points: number;
  reason: string;
}

export interface EvidenceResultDto {
  score: number;
  raw_points: number;
  max_possible: number;
  state: AttendanceState;
  components: EvidenceComponent[];
}

export interface LocationRow {
  student_id: string;
  name: string;
  classroom_id: string;
  classroom_name: string;
  x: number;
  y: number;
  state: AttendanceState;
  confidence: number;
  source: "live" | "simulated";
  last_update: number;
}

export interface LocationsFeed {
  ts: number;
  live_devices: number;
  simulated_devices: number;
  students: LocationRow[];
}

export interface Classroom {
  classroom_id: string;
  name: string;
  building: string | null;
  floor: number | null;
  x: number;
  y: number;
  ble_marker_id: string | null;
  display_name: string | null;
}

export interface Student {
  student_id: string;
  name: string;
  has_model: boolean;
}

export interface AnomalyDto {
  id: number;
  student_id: string;
  ts: number;
  type: string;
  severity: "low" | "medium" | "high" | "none";
  iforest_score: number | null;
  is_anomalous: boolean;
  reasons: string[];
  features: Record<string, number>;
  explanation: string | null;
  status: string;
}

export interface EvidenceHistoryPoint {
  ts: number;
  score: number;
  state: AttendanceState;
  source: "live" | "simulated";
}

export interface EvidenceDetail {
  student_id: string;
  latest: (EvidenceResultDto & { ts: number; session_id: string; classroom_id: string; source: string }) | null;
  history: EvidenceHistoryPoint[];
}

export interface RetrievedCase {
  case_id: number | null;
  case_type: string;
  issue: string;
  resolution: string;
  similarity: number;
}

export interface ExplainResponse {
  anomaly_id: number;
  explanation: string;
  similar_verified_cases: RetrievedCase[];
}

export interface SimulationSummary {
  students: number;
  classrooms: number;
  wifi_aps: number;
  ble_markers: number;
  sessions: number;
  events: number;
  anomalies_injected: number;
}

export type DemoInjectKind =
  | "proxy_attendance"
  | "impossible_movement"
  | "wifi_ble_mismatch"
  | "token_replay"
  | "device_clustering"
  | "short_presence";

export interface DemoInjectResult {
  anomaly_id: number | null;
  is_anomalous: boolean;
  severity: string;
  reasons: string[];
  isolation_forest_score: number | null;
  demo_risk_score: number | null;
}

export interface FeedbackRecord {
  id: number;
  anomaly_id: number;
  decision: "false_positive" | "confirmed";
  comment: string | null;
  ts: number;
  student_id: string | null;
  anomaly_type: string | null;
}
