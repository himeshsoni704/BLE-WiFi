import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { LiveLocation } from "./pages/LiveLocation";
import { Attendance } from "./pages/Attendance";
import { Anomalies } from "./pages/Anomalies";
import { EvidenceExplorer } from "./pages/EvidenceExplorer";
import { Feedback } from "./pages/Feedback";
import { DemoControl } from "./pages/DemoControl";

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/map" element={<LiveLocation />} />
          <Route path="/attendance" element={<Attendance />} />
          <Route path="/anomalies" element={<Anomalies />} />
          <Route path="/evidence" element={<EvidenceExplorer />} />
          <Route path="/feedback" element={<Feedback />} />
          <Route path="/demo" element={<DemoControl />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
