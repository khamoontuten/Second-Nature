import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { CalculatorPage } from "./pages/CalculatorPage";
import { ClimatePage } from "./pages/ClimatePage";
import { ComparePage } from "./pages/ComparePage";
import { FreeCoolingPage } from "./pages/FreeCoolingPage";
import { OptimizePage } from "./pages/OptimizePage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ReportPage } from "./pages/ReportPage";
import { SetupPage } from "./pages/SetupPage";
import { StrategiesPage } from "./pages/StrategiesPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout><ProjectsPage /></Layout>} />
      <Route path="/projects/:projectId" element={<Navigate to="setup" replace />} />
      <Route path="/projects/:projectId/setup" element={<Layout><SetupPage /></Layout>} />
      <Route path="/projects/:projectId/climate" element={<Layout><ClimatePage /></Layout>} />
      <Route path="/projects/:projectId/free-cooling" element={<Layout><FreeCoolingPage /></Layout>} />
      <Route path="/projects/:projectId/strategies" element={<Layout><StrategiesPage /></Layout>} />
      <Route path="/projects/:projectId/calculator" element={<Layout><CalculatorPage /></Layout>} />
      <Route path="/projects/:projectId/compare" element={<Layout><ComparePage /></Layout>} />
      <Route path="/projects/:projectId/optimize" element={<Layout><OptimizePage /></Layout>} />
      <Route path="/projects/:projectId/report" element={<Layout><ReportPage /></Layout>} />
      <Route path="*" element={<Layout><p>Page not found.</p></Layout>} />
    </Routes>
  );
}
