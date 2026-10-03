import { BrowserRouter, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { LiveLocation } from "./pages/LiveLocation";
import { Attendance } from "./pages/Attendance";
import { Anomalies } from "./pages/Anomalies";
import { EvidenceExplorer } from "./pages/EvidenceExplorer";
import { Feedback } from "./pages/Feedback";
import { DemoControl } from "./pages/DemoControl";

function Gate({ children }: { children: React.ReactNode }) {
  const { token, me, loading } = useAuth();
  if (!token) return <Login />;
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center" style={{ background: "var(--bg)", color: "var(--text-dim)" }}>
        Loading…
      </div>
    );
  }
  if (!me) return <Login />;
  return <Layout>{children}</Layout>;
}

function EvidenceRoute() {
  const [params] = useSearchParams();
  return <EvidenceExplorer key={params.get("student") ?? ""} />;
}

function Routed() {
  return (
    <Gate>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/map" element={<LiveLocation />} />
        <Route path="/attendance" element={<Attendance />} />
        <Route path="/anomalies" element={<Anomalies />} />
        <Route path="/evidence" element={<EvidenceRoute />} />
        <Route path="/feedback" element={<Feedback />} />
        <Route path="/demo" element={<DemoControl />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Gate>
  );
}

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <AuthProvider>
        <Routed />
      </AuthProvider>
    </BrowserRouter>
  );
}
