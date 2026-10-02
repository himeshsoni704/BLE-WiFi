// Mirrors campus-presence/backend/app's JSON shapes (schemas.py, fusion.py,
// anomaly.py, routers/*.py response bodies).

export type AttendanceState = "PRESENT" | "LIKELY_PRESENT" | "REVIEW_REQUIRED" | "ABSENT";
export type Source = "LIVE" | "SIMULATED";
export type Role = "student" | "faculty" | "admin";

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: Role;
  username: string;
}

export interface Me {
  username: string;
  role: Role;
  student_key: string | null;
}

export interface ClassroomMarker {
  marker_id: string | null;
  marker_idx: number;
  label: string | null;
  kind: string;
  source: Source;
  last_heartbeat: number | null;
  students_detected: number;
}

export interface Classroom {
  id: string;
  name: string;
  building: string;
  x: number;
  y: number;
  is_corridor: boolean;
  marker: ClassroomMarker | null;
}

export interface StudentRow {
  student_id: string;
  student_key: string;
  name: string;
  source: Source;
  sessions: Partial<Record<AttendanceState, number>>;
}

export interface StudentsResponse {
  count: number;
  totals: { live: number; simulated: number };
  students: StudentRow[];
}

export interface LocationStudent {
  student_id: string;
  student_key: string;
  name: string;
  zone: string;
  x: number;
  y: number;
  signal: "ble_marker" | "wifi" | "fused";
  estimate: string;
  confidence: number | null;
  ts: number;
  source: Source;
}

export interface AccessPointRow {
  ap_id: string;
  name: string;
  x: number;
  y: number;
  bssid: string | null;
  source: Source;
}

export interface LocationsResponse {
  at: number;
  age_s: number;
  window_s: number;
  students: LocationStudent[];
  per_zone: Record<string, number>;
  access_points: AccessPointRow[];
}

export interface SessionRow {
  id: number;
  code: string;
  title: string;
  room: string;
  start_ts: number;
  end_ts: number;
  source: Source;
  status: "in_progress" | "completed" | "scheduled";
  counts: Partial<Record<AttendanceState, number>>;
}

export interface AttendanceRow {
  student_id: string;
  student_key: string;
  name: string;
  source: Source;
  final_state: AttendanceState;
  fused_state: AttendanceState;
  score: number;
  anomaly_id: number | null;
  components: Record<string, number | null>;
  families: number | null;
  updated_at: number;
}

export interface AttendanceResponse {
  sessions: SessionRow[];
  selected_session_id: number | null;
  totals: Record<AttendanceState, number>;
  score_note: string;
  rows: AttendanceRow[];
}

export interface EvidenceComponent {
  detected?: boolean;
  available?: boolean;
  points: number;
  provenance?: string;
  [key: string]: unknown;
}

export interface EvidenceSnapshot {
  score: number;
  score_note: string;
  state: AttendanceState;
  classroom_ble: EvidenceComponent;
  wifi: EvidenceComponent;
  peers: EvidenceComponent;
  sustained: EvidenceComponent;
  face: EvidenceComponent;
  rfid: EvidenceComponent;
  independent_signal_families: { count: number; ble: boolean; wifi: boolean; face: boolean; rfid: boolean };
  has_evidence: boolean;
  state_capped_for_single_family: boolean;
  isolation_forest?: { evaluated: boolean; reason?: string; raw_score?: number; flagged?: boolean; risk_demo_0_100?: number };
  rules?: { rule: string; severity: string; detail: string; data: Record<string, unknown> }[];
}

export interface EvidenceSessionEntry {
  session: { id: number; code: string; room: string; start_ts: number; end_ts: number; source: Source };
  final_state: AttendanceState;
  fused_state: AttendanceState;
  score: number;
  evidence: EvidenceSnapshot;
  anomaly: { id: number; status: string } | null;
}

export interface TimelineEvent {
  ts: number;
  kind: string;
  direction: string | null;
  rssi: number | null;
  duration: number | null;
  room: string | null;
  wifi_zone: string | null;
  wifi_conf: number | null;
  wifi_source: string | null;
  peer: string | null;
  provenance: string;
}

export interface EvidenceDetail {
  student: { student_id: string; student_key: string; name: string; source: Source };
  sessions: EvidenceSessionEntry[];
  timeline: TimelineEvent[];
}

export interface RuleHit {
  rule: string;
  severity: "info" | "warn" | "high";
  detail: string;
  data: Record<string, unknown>;
}

export interface AnomalyRow {
  id: number;
  student_id: string;
  student_key: string;
  name: string;
  session_id: number;
  session_code: string;
  room: string;
  ts: number;
  rules: string[];
  rule_details: RuleHit[];
  isolation_score_raw: number | null;
  isolation_flagged: boolean;
  risk_demo_0_100: number | null;
  status: "open" | "confirmed" | "false_positive" | "cleared";
  scenario: string | null;
  source: Source;
  has_explanation: boolean;
  explanation_provider: string | null;
}

export interface AnomaliesResponse {
  count: number;
  by_status: Record<string, number>;
  note: string;
  anomalies: AnomalyRow[];
}

export interface AnomalyFeedbackEntry {
  id: number;
  user: string;
  action: string;
  comment: string | null;
  label: string | null;
  ts: number;
}

export interface AnomalyDetail extends AnomalyRow {
  evidence: EvidenceSnapshot;
  features: Record<string, number>;
  explanation: ExplanationDto | null;
  explanation_ts: number | null;
  feedback: AnomalyFeedbackEntry[];
}

export interface ExplanationDto {
  text: string;
  key_points: string[];
  recommended_action: string;
  provider: "gemini" | "mock";
  model: string | null;
  grounding: { checked: boolean; passed: boolean; unverified_terms: string[] };
  fallback_reason: string | null;
  rejected_text: string | null;
  used_case_ids: number[];
}

export interface RetrievedCase {
  case_id: number;
  title: string;
  summary: string;
  resolution: "false_positive" | "confirmed_anomaly";
  faculty_comment: string | null;
  origin: string;
  anomaly_id: number | null;
  similarity: number;
}

export interface ExplainResponse {
  anomaly_id: number;
  source: Source;
  evidence: Record<string, unknown>;
  similar_cases: RetrievedCase[];
  explanation: ExplanationDto;
  provider: { configured: string; used: string; model: string | null; note: string | null; fallback_reason: string | null };
  disclaimer: string;
}

export interface FeedbackSubmitResponse {
  anomaly_id: number;
  status: string;
  verified_case_id: number | null;
  rag_cases_total: number;
  training_rows_total: number;
  attendance_final_state: AttendanceState | null;
  notes: string[];
}

export interface FeedbackEntry {
  id: number;
  anomaly_id: number;
  user: string;
  action: string;
  comment: string | null;
  label: string | null;
  ts: number;
  source: Source;
}

export interface VerifiedCaseEntry {
  case_id: number;
  title: string;
  summary: string;
  resolution: string;
  faculty_comment: string | null;
  origin: string;
  anomaly_id: number | null;
  ts: number;
}

export interface FeedbackSummaryResponse {
  feedback: FeedbackEntry[];
  verified_cases: VerifiedCaseEntry[];
  training_rows: number;
  retrain_command: string;
  note: string;
}

export type DemoScenario =
  | "proxy" | "impossible_movement" | "wifi_ble_mismatch"
  | "token_replay" | "false_positive" | "short_presence";

export interface SimulationFullSummary {
  students: number;
  sessions: number;
  injected: { scenario: string; session_id: number; session_code: string; room: string; target_student_keys: string[]; detail: Record<string, unknown> }[];
  skipped_scenarios: string[];
  seconds?: number;
}

export interface SimulationStartResponse {
  status: "completed" | "started";
  summary?: SimulationFullSummary;
  poll?: string;
  live_labels?: string;
  scenario?: string;
  session_id?: number;
  session_code?: string;
  room?: string;
  target_student_keys?: string[];
  detail?: Record<string, unknown>;
  anomaly_ids?: number[];
  label?: string;
}

export interface SimulationStatus {
  running: boolean;
  last: SimulationFullSummary | null;
  progress: number;
  message?: string;
  error?: string | null;
  simulated_students: number;
  exists: boolean;
}

export interface SummaryResponse {
  attendance: Record<AttendanceState, number>;
  active_classrooms: string[];
  live_nodes_online: string[];
  ble_devices_seen: number;
  wifi_access_points_registered: number;
  anomalies: Record<string, number>;
  verified_cases: number;
  wifi_survey_samples: number;
  students: { live: number; simulated: number };
  measured_counters_since_start: Record<string, number>;
  unavailable_metrics: string[];
}

export interface ConfigResponse {
  llm: { configured: string; active: string | null; note: string | null; model: string | null };
  token_window_s: number;
  demo_mode: boolean;
  fusion_weights: Record<string, number>;
  fusion_thresholds: Record<string, number>;
  score_note: string;
  architecture: string;
}
