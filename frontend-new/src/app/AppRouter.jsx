import { lazy, useEffect, useState } from "react";
import {
  createBrowserRouter,
  createRoutesFromElements,
  RouterProvider,
  Navigate,
  Outlet,
  Route,
  useLocation,
  useNavigate,
  useOutletContext,
  useRouteError,
} from "react-router-dom";
import AppShell from "../components/layout/AppShell";
import { ErrorNotice, PageFrame } from "../components/ui";
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
const MemoryPage = lazy(() => import("../pages/business/MemoryPage"));
const ProfilePage = lazy(() => import("../pages/business/ProfilePage"));
const ReportsPage = lazy(() => import("../pages/business/ReportsPage"));
const ReportComposerPage = lazy(
  () => import("../pages/business/ReportComposerPage"),
);
const AlertsPage = lazy(() => import("../pages/business/AlertsPage"));
const AlertDetailPage = lazy(() => import("../pages/business/AlertDetailPage"));
const ReportDetailPage = lazy(
  () => import("../pages/business/ReportDetailPage"),
);
const MessagesPage = lazy(() => import("../pages/business/MessagesPage"));
const AdminOverviewPage = lazy(
  () => import("../pages/admin/AdminOverviewPage"),
);
const TenantManagementPage = lazy(() => import("../pages/admin/TenantManagementPage"));
const KnowledgePage = lazy(() => import("../pages/admin/KnowledgePage"));
const ResourcesPage = lazy(() => import("../pages/admin/ResourcesPage"));
const MonitoringRulesPage = lazy(() => import("../pages/admin/MonitoringRulesPage"));
const ModelGovernancePage = lazy(
  () => import("../pages/admin/ModelGovernancePage"),
);
const ModelArchivePage = lazy(
  () => import("../pages/admin/ModelArchivePage"),
);
const ModelDriftPage = lazy(
  () => import("../pages/admin/ModelDriftPage"),
);
const SkillGovernancePage = lazy(
  () => import("../pages/admin/SkillGovernancePage"),
);
const MemoryGovernancePage = lazy(
  () => import("../pages/admin/MemoryGovernancePage"),
);
const SettingsPage = lazy(() => import("../pages/admin/SettingsPage"));
const SecurityPage = lazy(() => import("../pages/admin/SecurityPage"));
const MessagingPage = lazy(() => import("../pages/admin/MessagingPage"));

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

function RouteErrorFallback() {
  const error = useRouteError();
  const navigate = useNavigate();
  const detail = error?.status === 404
    ? "这个页面不存在或已经移动。"
    : "页面组件加载失败，你的其他工作区内容不会受到影响。";

  return (
    <PageFrame className="max-w-[920px]">
      <ErrorNotice
        title="页面暂时无法显示"
        detail={detail}
        onRetry={() => navigate(0)}
      />
    </PageFrame>
  );
}

export default function AppRouter() {
  const [router] = useState(() => createBrowserRouter(createRoutesFromElements(
      <>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/verify-email" element={<EmailVerificationPage />} />
        <Route element={<EmailGate />}>
          <Route element={<AppShell />}>
            <Route element={<AppGuard />} errorElement={<RouteErrorFallback />}>
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
              <Route path="workspace/memory" element={<MemoryPage />} />
              <Route path="workspace/profile" element={<ProfilePage />} />
              <Route path="workspace/reports" element={<ReportsPage />} />
              <Route
                path="workspace/reports/composer"
                element={<ReportComposerPage />}
              />
              <Route path="workspace/alerts" element={<AlertsPage />} />
              <Route path="workspace/alerts/:alertId" element={<AlertDetailPage />} />
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
              <Route path="admin/tenants" element={<TenantManagementPage />} />
              <Route path="admin/messages" element={<MessagesPage />} />
              <Route
                path="admin/messages/:messageId"
                element={<MessagesPage />}
              />
              <Route path="admin/knowledge" element={<KnowledgePage />} />
              <Route path="admin/resources" element={<ResourcesPage />} />
              <Route path="admin/monitoring-rules" element={<MonitoringRulesPage />} />
              <Route path="admin/model-training" element={<ModelingPage />} />
              <Route path="admin/models" element={<ModelGovernancePage />} />
              <Route path="admin/model-archive" element={<ModelArchivePage />} />
              <Route path="admin/model-drift" element={<ModelDriftPage />} />
              <Route path="admin/skills" element={<SkillGovernancePage />} />
              <Route path="admin/memory" element={<MemoryGovernancePage />} />
              <Route path="admin/settings" element={<SettingsPage />} />
              <Route path="admin/security" element={<SecurityPage />} />
              <Route path="admin/messaging" element={<MessagingPage />} />
              <Route path="admin/announcements" element={<Navigate to="/admin/messaging" replace />} />
              <Route path="admin/deliveries" element={<Navigate to="/admin/messaging" replace />} />
              <Route
                path="*"
                element={<Navigate to="/workspace/overview" replace />}
              />
            </Route>
          </Route>
        </Route>
      </>
  )));
  return <RouterProvider router={router} />;
}
