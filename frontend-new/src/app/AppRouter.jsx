import { lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import AppShell from "../components/layout/AppShell";

const OverviewPage = lazy(() => import("../pages/business/OverviewPage"));
const AnalysisPage = lazy(() => import("../pages/business/AnalysisPage"));
const ModelingPage = lazy(() => import("../pages/business/ModelingPage"));
const ReportsPage = lazy(() => import("../pages/business/ReportsPage"));
const ReportDetailPage = lazy(() => import("../pages/business/ReportDetailPage"));
const AdminOverviewPage = lazy(() => import("../pages/admin/AdminOverviewPage"));
const KnowledgePage = lazy(() => import("../pages/admin/KnowledgePage"));
const ResourcesPage = lazy(() => import("../pages/admin/ResourcesPage"));
const ModelGovernancePage = lazy(() => import("../pages/admin/ModelGovernancePage"));
const SettingsPage = lazy(() => import("../pages/admin/SettingsPage"));
const SecurityPage = lazy(() => import("../pages/admin/SecurityPage"));

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/workspace/overview" replace />} />
          <Route path="workspace/overview" element={<OverviewPage />} />
          <Route path="workspace/analysis" element={<AnalysisPage />} />
          <Route path="workspace/modeling" element={<ModelingPage />} />
          <Route path="workspace/reports" element={<ReportsPage />} />
          <Route path="workspace/reports/:reportId" element={<ReportDetailPage />} />
          <Route path="admin/overview" element={<AdminOverviewPage />} />
          <Route path="admin/knowledge" element={<KnowledgePage />} />
          <Route path="admin/resources" element={<ResourcesPage />} />
          <Route path="admin/models" element={<ModelGovernancePage />} />
          <Route path="admin/settings" element={<SettingsPage />} />
          <Route path="admin/security" element={<SecurityPage />} />
          <Route path="*" element={<Navigate to="/workspace/overview" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
