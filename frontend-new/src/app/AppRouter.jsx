import { lazy, useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useOutletContext,
} from "react-router-dom";
import AppShell from "../components/layout/AppShell";
import { fetchMe } from "../api/auth";
import { clearSession, getCachedUser, getToken, setSession } from "../lib/auth";

const LoginPage = lazy(() => import("../pages/auth/LoginPage"));
const EmailVerificationPage = lazy(
  () => import("../pages/auth/EmailVerificationPage"),
);
const OverviewPage = lazy(() => import("../pages/business/OverviewPage"));
const AnalysisPage = lazy(() => import("../pages/business/AnalysisPage"));
const SessionHistoryPage = lazy(
  () => import("../pages/business/SessionHistoryPage"),
);
const ModelingPage = lazy(() => import("../pages/business/ModelingPage"));
const ReportsPage = lazy(() => import("../pages/business/ReportsPage"));
const ReportDetailPage = lazy(
  () => import("../pages/business/ReportDetailPage"),
);
const MessagesPage = lazy(() => import("../pages/business/MessagesPage"));
const AdminOverviewPage = lazy(
  () => import("../pages/admin/AdminOverviewPage"),
);
const KnowledgePage = lazy(() => import("../pages/admin/KnowledgePage"));
const ResourcesPage = lazy(() => import("../pages/admin/ResourcesPage"));
const ModelGovernancePage = lazy(
  () => import("../pages/admin/ModelGovernancePage"),
);
const SettingsPage = lazy(() => import("../pages/admin/SettingsPage"));
const SecurityPage = lazy(() => import("../pages/admin/SecurityPage"));
const AnnouncementsPage = lazy(
  () => import("../pages/admin/AnnouncementsPage"),
);
const DeliveriesPage = lazy(() => import("../pages/admin/DeliveriesPage"));

export function EmailGate({
  allowDemo = import.meta.env.VITE_DEMO_MODE === "true",
}) {
  const location = useLocation();
  const token = getToken();
  const cachedUser = getCachedUser();
  const cacheIsCurrent = Boolean(cachedUser?.emailStatus);
  const [user, setUser] = useState(cacheIsCurrent ? cachedUser : undefined);

  useEffect(() => {
    if (!token || cacheIsCurrent) return undefined;
    let active = true;
    fetchMe()
      .then((freshUser) => {
        if (!active) return;
        setSession(token, freshUser);
        setUser(freshUser);
      })
      .catch(() => {
        if (!active) return;
        clearSession();
        setUser(null);
      });
    return () => {
      active = false;
    };
  }, [cacheIsCurrent, token]);

  if (!token) {
    return allowDemo ? <Outlet /> : <Navigate to="/login" replace />;
  }
  if (user === undefined) {
    return (
      <div className="grid min-h-screen place-items-center" role="status">
        正在确认邮箱状态…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (user.emailStatus !== "verified") {
    return (
      <Navigate
        to="/verify-email"
        replace
        state={{ from: location.pathname }}
      />
    );
  }
  return <Outlet />;
}

// 角色守卫：business 用户访问 /admin/* 拦回业务工作台；无 token 演示模式放行（渐进式认证）。
// 作为无 path 的 layout route，必须渲染 <Outlet /> 让子路由内容落位（children prop 不会被传入）。
function AppGuard() {
  const location = useLocation();
  const outletContext = useOutletContext();
  const user = getCachedUser();
  if (
    user &&
    user.role === "business" &&
    location.pathname.startsWith("/admin")
  ) {
    return <Navigate to="/workspace/overview" replace />;
  }
  // 透传 AppShell 的 outlet context（如 navigatePage），否则子页面 useOutletContext() 拿到 undefined
  return <Outlet context={outletContext} />;
}

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/verify-email" element={<EmailVerificationPage />} />
        <Route element={<EmailGate />}>
          <Route element={<AppShell />}>
            <Route element={<AppGuard />}>
              <Route
                index
                element={<Navigate to="/workspace/overview" replace />}
              />
              <Route path="workspace/overview" element={<OverviewPage />} />
              <Route path="workspace/analysis" element={<AnalysisPage />} />
              <Route
                path="workspace/sessions"
                element={<SessionHistoryPage />}
              />
              <Route path="workspace/modeling" element={<ModelingPage />} />
              <Route path="workspace/reports" element={<ReportsPage />} />
              <Route
                path="workspace/reports/:reportId"
                element={<ReportDetailPage />}
              />
              <Route path="workspace/messages" element={<MessagesPage />} />
              <Route
                path="workspace/messages/:messageId"
                element={<MessagesPage />}
              />
              <Route path="admin/overview" element={<AdminOverviewPage />} />
              <Route path="admin/messages" element={<MessagesPage />} />
              <Route
                path="admin/messages/:messageId"
                element={<MessagesPage />}
              />
              <Route path="admin/knowledge" element={<KnowledgePage />} />
              <Route path="admin/resources" element={<ResourcesPage />} />
              <Route path="admin/models" element={<ModelGovernancePage />} />
              <Route path="admin/settings" element={<SettingsPage />} />
              <Route path="admin/security" element={<SecurityPage />} />
              <Route
                path="admin/announcements"
                element={<AnnouncementsPage />}
              />
              <Route path="admin/deliveries" element={<DeliveriesPage />} />
              <Route
                path="*"
                element={<Navigate to="/workspace/overview" replace />}
              />
            </Route>
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
