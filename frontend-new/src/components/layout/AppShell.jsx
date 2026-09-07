import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import Icon from "../ui/Icon";
import { Button, RouteSkeleton } from "../ui";
import forgeSightLogo from "../../assets/forgesight-logo.png";
import { pageMeta, roleMenus } from "../../app/navigation";
import cn from "../../lib/cn";
import { pageFromPath, roleFromPath, routeByPage } from "../../app/routeConfig";
import { createSession, deleteSession, listSessions } from "../../api/analysis";
import { fetchMe, logout as apiLogout } from "../../api/auth";
import { getUnreadCount } from "../../api/messages";
import { getCurrentTenant, listMyTenants } from "../../api/tenants";
import { AUTH_EXPIRED_EVENT, getCachedUser, getToken, setSession } from "../../lib/auth";
import { clearTenantWorkspaceState, getActiveTenantId, setActiveTenantId } from "../../lib/tenant";

const users = {
  business: { name: "王敏", title: "生产部 · 业务分析", initials: "王" },
  admin: { name: "林涛", title: "数据治理组 · 管理员", initials: "林" },
};

const SIDEBAR_MIN = 210;
const SIDEBAR_MAX = 290;
const SIDEBAR_DEFAULT = 252;
const ANALYSIS_SESSION_KEY = "forgesight:analysis-session-id";
const ANALYSIS_NEW_SESSION_KEY = "forgesight:analysis-new-session";
const ANALYSIS_DRAFT_KEY = "forgesight:analysis-draft";
const ANALYSIS_SESSION_SELECTED_EVENT = "forgesight:analysis-session-selected";

export default function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const role = roleFromPath(location.pathname);
  const page = pageFromPath(location.pathname) ?? (role === "admin" ? "admin-overview" : "overview");
  const cachedUser = getCachedUser();
  const user = cachedUser
    ? {
        name: cachedUser.displayName || cachedUser.username,
        title: cachedUser.title || (cachedUser.role === "admin" ? "系统管理员" : "业务人员"),
        initials: (cachedUser.displayName || cachedUser.username).charAt(0),
      }
    : users[role];
  const [collapsed, setCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT);
  const [evidenceOpen, setEvidenceOpen] = useState(true);
  const [darkMode, setDarkMode] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [sessions, setSessions] = useState(null);
  const [sessionsError, setSessionsError] = useState(null);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [expandedItems, setExpandedItems] = useState(() => new Set(["data-semantic-governance"]));
  const [unreadCount, setUnreadCount] = useState(0);
  const [tenants, setTenants] = useState(() => cachedUser?.tenants ?? []);
  const [activeTenant, setActiveTenant] = useState(null);


  useEffect(() => {
    const activeParent = roleMenus[role]
      .flatMap((group) => group.items)
      .find((item) => item.children?.some((child) => child.id === page));
    if (!activeParent) return;
    setExpandedItems((current) => {
      if (current.has(activeParent.id)) return current;
      const next = new Set(current);
      next.add(activeParent.id);
      return next;
    });
  }, [page, role]);

  const refreshSessions = useCallback(async (preferredSessionId = null) => {
    setSessionsError(null);
    try {
      const data = await listSessions();
      setSessions(data);
      setActiveSessionId((current) => (
        preferredSessionId
        ?? (data.some((item) => item.id === current) ? current : null)
        ?? data[0]?.id
        ?? null
      ));
      return data;
    } catch (error) {
      setSessions([]);
      setSessionsError(error);
      return [];
    }
  }, []);

  useEffect(() => {
    if (role !== "business") return undefined;
    refreshSessions();
    return undefined;
  }, [refreshSessions, role]);

  useEffect(() => {
    let cancelled = false;
    const refreshUnread = () => getUnreadCount().then((count) => { if (!cancelled) setUnreadCount(count); }).catch(() => {});
    refreshUnread();
    const timer = window.setInterval(refreshUnread, 30000);
    window.addEventListener("forgesight:messages-changed", refreshUnread);
    return () => { cancelled = true; window.clearInterval(timer); window.removeEventListener("forgesight:messages-changed", refreshUnread); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getCurrentTenant(),
      getToken() ? listMyTenants().catch(() => ({ items: cachedUser?.tenants ?? [] })) : Promise.resolve({ items: [] }),
    ]).then(([current, memberships]) => {
      if (cancelled) return;
      setActiveTenant(current);
      setTenants(memberships.items?.length ? memberships.items : [current]);
    }).catch(() => {
      if (!cancelled) setActiveTenant({ id: getActiveTenantId(), name: "当前企业", role: "member" });
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (darkMode) document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");

    return () => {
      document.documentElement.removeAttribute("data-theme");
    };
  }, [darkMode]);

  // 已登录时刷新用户缓存（失败静默降级演示模式）；无 token 时两个 effect 均不触发
  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    fetchMe()
      .then((freshUser) => {
        if (!cancelled) setSession(getToken(), freshUser);
      })
      .catch(() => {
        // token 失效时 _http 已清理会话并广播事件
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onAuthExpired = () => navigate("/login", { replace: true });
    window.addEventListener(AUTH_EXPIRED_EVENT, onAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onAuthExpired);
  }, [navigate]);

  const handleLogout = async () => {
    await apiLogout();
    navigate("/login", { replace: true });
  };

  const navigatePage = (nextPage) => {
    const allowedPages = new Set(
      roleMenus[role].flatMap((group) => group.items.flatMap((item) => [item.id, ...(item.children ?? []).map((child) => child.id)])),
    );
    if (!allowedPages.has(nextPage)) return;
    const path = routeByPage[nextPage];
    if (path) navigate(path);
    setMobileNavOpen(false);
    if (nextPage === "analysis") setEvidenceOpen(true);
  };

  const switchTenant = (tenantId) => {
    if (!tenantId || tenantId === activeTenant?.id) return;
    setActiveTenantId(tenantId);
    clearTenantWorkspaceState();
    navigate(role === "admin" ? routeByPage["admin-overview"] : routeByPage.overview, { replace: true });
    window.location.reload();
  };

  const createTask = async () => {
    setSessionsError(null);
    try {
      const created = await createSession();
      setSessions((current) => [created, ...(current ?? []).filter((item) => item.id !== created.id)]);
      setActiveSessionId(created.id);
      window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, created.id);
      setEvidenceOpen(true);
      navigate(created.path);
      window.dispatchEvent(new CustomEvent(ANALYSIS_SESSION_SELECTED_EVENT, { detail: { sessionId: created.id } }));
    } catch (error) {
      setSessionsError(error);
    }
  };

  const startFreshAnalysis = () => {
    setSessionsError(null);
    setActiveSessionId(null);
    setMobileNavOpen(false);
    window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    window.sessionStorage.removeItem(ANALYSIS_DRAFT_KEY);
    window.sessionStorage.setItem(ANALYSIS_NEW_SESSION_KEY, "1");
    setEvidenceOpen(true);
    navigate(routeByPage.analysis);
    window.dispatchEvent(new CustomEvent(ANALYSIS_SESSION_SELECTED_EVENT, { detail: { newSession: true } }));
  };

  const deleteTask = async (sessionId) => {
    await deleteSession(sessionId);
    const remaining = (sessions ?? []).filter((item) => item.id !== sessionId);
    setSessions(remaining);
    if (activeSessionId !== sessionId) return;

    const next = remaining[0] ?? null;
    setActiveSessionId(next?.id ?? null);
    if (next) {
      window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, next.id);
      if (page === "analysis") {
        window.dispatchEvent(new CustomEvent(ANALYSIS_SESSION_SELECTED_EVENT, { detail: { sessionId: next.id } }));
      }
      return;
    }

    window.sessionStorage.removeItem(ANALYSIS_SESSION_KEY);
    if (page === "analysis") navigate(routeByPage.overview);
  };

  const startResize = (event) => {
    event.preventDefault();
    setCollapsed(false);
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    const move = (moveEvent) => setSidebarWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startWidth + moveEvent.clientX - startX)));
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  return (
    <div className={cn("app-shell flex h-full w-full overflow-hidden bg-canvas font-sans text-ink", darkMode && "theme-dark")}>
      {mobileNavOpen && <button type="button" aria-label="关闭导航" className="fixed inset-0 z-40 cursor-default bg-ink/25 md:hidden" onClick={() => setMobileNavOpen(false)} />}
      <Sidebar
        role={role}
        page={page}
        user={user}
        collapsed={collapsed}
        darkMode={darkMode}
        width={sidebarWidth}
        sessions={sessions}
        sessionsError={sessionsError}
        activeSessionId={activeSessionId}
        onNavigate={navigatePage}
        onCreateTask={createTask}
        onStartAnalysis={startFreshAnalysis}
        onDeleteSession={deleteTask}
        onToggleDarkMode={() => setDarkMode((value) => !value)}
        onRetrySessions={() => refreshSessions()}
        onLogout={handleLogout}
        onSelectSession={(session) => {
          setActiveSessionId(session.id);
          window.sessionStorage.setItem(ANALYSIS_SESSION_KEY, session.id);
          navigate(session.path);
          window.dispatchEvent(new CustomEvent(ANALYSIS_SESSION_SELECTED_EVENT, { detail: { sessionId: session.id } }));
        }}
        onToggle={() => setCollapsed((value) => !value)}
        onResizeStart={startResize}
        expandedItems={expandedItems}
        onToggleItem={(itemId) => setExpandedItems((current) => {
          const next = new Set(current);
          if (next.has(itemId)) next.delete(itemId);
          else next.add(itemId);
          return next;
        })}
        onResizeReset={() => { setCollapsed(false); setSidebarWidth(SIDEBAR_DEFAULT); }}
        mobileOpen={mobileNavOpen}
        onResizeKeyDown={(event) => {
          if (event.key === "ArrowLeft") setSidebarWidth((value) => Math.max(SIDEBAR_MIN, value - 10));
          if (event.key === "ArrowRight") setSidebarWidth((value) => Math.min(SIDEBAR_MAX, value + 10));
        }}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar
          role={role}
          page={page}
          user={user}
          evidenceOpen={evidenceOpen}
          onOpenNavigation={() => setMobileNavOpen(true)}
          unreadCount={unreadCount}
          onOpenMessages={() => navigate(role === "admin" ? routeByPage["admin-messages"] : routeByPage.messages)}
          onOpenProfile={() => navigate(routeByPage.profile)}
          onToggleEvidence={() => setEvidenceOpen((value) => !value)}
          activeTenant={activeTenant}
          tenants={tenants}
          onTenantChange={switchTenant}
        />
        <main className={cn("app-main min-h-0 flex-1", page === "analysis" ? "overflow-hidden" : "overflow-auto")}>
          <Suspense fallback={<RouteSkeleton />}>
            <Outlet context={{ evidenceOpen, setEvidenceOpen, navigatePage, refreshSessions }} />
          </Suspense>
        </main>
      </div>
    </div>
  );
}

function Sidebar({ role, page, user, collapsed, darkMode, width, sessions, sessionsError, activeSessionId, onNavigate, onCreateTask, onStartAnalysis, onDeleteSession, onSelectSession, onRetrySessions, onToggleDarkMode, onLogout, onToggle, onResizeStart, onResizeKeyDown, onResizeReset, mobileOpen, expandedItems, onToggleItem }) {
  const [searchText, setSearchText] = useState("");
  const [openSessionMenuId, setOpenSessionMenuId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [deletingSessionId, setDeletingSessionId] = useState(null);
  const [sessionMenuError, setSessionMenuError] = useState("");
  const filteredSessions = useMemo(
    () => (sessions ?? []).filter((session) => session.title?.includes(searchText.trim())),
    [searchText, sessions],
  );
  const currentWidth = collapsed ? 82 : width;


  const removeSession = async (session) => {
    setDeletingSessionId(session.id);
    setSessionMenuError("");
    try {
      await onDeleteSession(session.id);
      setOpenSessionMenuId(null);
      setConfirmDeleteId(null);
    } catch (error) {
      setSessionMenuError(error.message || "删除失败，请稍后重试");
    } finally {
      setDeletingSessionId(null);
    }
  };

  return (
    <aside
      data-testid="app-sidebar"
      data-collapsed={collapsed ? "true" : "false"}
      className={cn("app-sidebar-shell fixed inset-y-0 left-0 z-50 flex h-full shrink-0 flex-col overflow-hidden whitespace-nowrap border-r border-line bg-surface-2 shadow-card-hover md:relative md:z-auto md:translate-x-0 md:shadow-none", mobileOpen ? "translate-x-0" : "-translate-x-full")}
      style={{ width: currentWidth, flexBasis: currentWidth }}
    >
      <div className="sidebar-shell__header relative min-h-16 px-2 py-2.5">
        <button
          type="button"
          className={cn(
            "sidebar-shell__toggle grid h-8 w-8 cursor-pointer place-items-center rounded-control border transition-colors",
            collapsed
              ? "bg-surface text-muted shadow-card border-line hover:text-ink"
              : "border-transparent text-muted hover:border-line hover:bg-surface hover:text-ink",
          )}
          aria-label={collapsed ? "展开侧边栏" : "折叠侧边栏"}
          onClick={onToggle}
        >
          <Icon name="sidebar" size={18} />
        </button>
        {!collapsed && (
          <NavLink className="flex min-w-0 items-center gap-2.5 pl-10 pr-2" to={role === "business" ? routeByPage.overview : routeByPage["admin-overview"]}>
            <img
              className="h-[38px] w-[38px] shrink-0 rounded-control border border-line bg-surface object-contain p-1"
              src={forgeSightLogo}
              alt="ForgeSight logo"
            />
            <div className="flex min-w-0 flex-col leading-tight">
              <strong className="text-sm font-bold tracking-tight text-ink">ForgeSight</strong>
              <span className="mt-0.5 text-[10px] tracking-wide text-muted">Industrial Intelligence</span>
            </div>
          </NavLink>
        )}
      </div>

      <nav className="border-t border-line px-2 pb-2 pt-1" aria-label={role === "business" ? "业务人员导航" : "管理员导航"}>
        {roleMenus[role].map((group) => (
          <div key={group.label}>
            {!collapsed && <div className="px-2 pb-1.5 pt-2.5 text-[11px] font-semibold text-muted">{group.label}</div>}
            <div className={cn("flex flex-col gap-0.5", collapsed && "pt-2")}>
              {group.items.map((item) => (
                <SidebarNavItem
                  key={item.id}
                  item={item}
                  page={page}
                  collapsed={collapsed}
                  expanded={expandedItems.has(item.id)}
                  onNavigate={item.id === "analysis" ? onStartAnalysis : onNavigate}
                  onToggle={onToggleItem}
                />
              ))}
            </div>
          </div>
        ))}
      </nav>

      {role === "business" ? (
        <>
          <div className="border-t border-line px-2 py-2.5">
            <Button variant="primary" className="sidebar-create-task relative h-10 w-full text-[13px]" onClick={onCreateTask} aria-label="发起分析">
              {collapsed ? (
                <Icon name="plus" size={15} />
              ) : (
                <>
                  <span className="absolute left-3 inline-flex"><Icon name="plus" size={15} /></span>
                  发起分析
                </>
              )}
            </Button>
            {!collapsed && (
              <label className="relative mt-2 block">
                <span className="sr-only">搜索调查记录</span>
                <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"><Icon name="search" size={14} /></span>
                <input
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  placeholder="搜索调查记录"
                  className="h-9 w-full rounded-control border border-line bg-surface pl-8 pr-2.5 text-xs text-ink outline-none transition placeholder:text-faint focus:border-primary/50 focus:ring-2 focus:ring-primary/25"
                />
              </label>
            )}
          </div>
          <div className="flex min-h-0 flex-1 flex-col border-t border-line px-2 pb-2.5">
            {!collapsed && <div className="px-2 pb-1.5 pt-2.5 text-[11px] font-semibold text-muted">最近调查</div>}
            <div className={cn("flex min-h-0 flex-1 flex-col gap-1 overflow-auto px-0.5 pb-1.5", collapsed && "pt-2")}>
              {sessions === null && [1, 2, 3].map((item) => <i key={item} className="h-8 shrink-0 animate-pulse rounded-control bg-surface-3" />)}
              {sessions !== null && sessionsError && !collapsed && (
                <button
                  type="button"
                  onClick={onRetrySessions}
                  className="rounded-control border border-red/20 bg-red-soft px-2 py-2 text-left text-[11px] text-red transition-colors hover:border-red/35"
                >
                  最近分析加载失败，点击重试
                </button>
              )}
              {sessions !== null && filteredSessions.map((session) => {
                const menuOpen = openSessionMenuId === session.id;
                const confirming = confirmDeleteId === session.id;
                const deleting = deletingSessionId === session.id;
                return (
                  <div key={session.id}>
                    <div
                      className={cn(
                        "flex items-center gap-2 rounded-control p-2 transition-colors",
                        collapsed && "justify-center p-1.5",
                        activeSessionId === session.id ? "bg-surface text-ink shadow-card" : "text-muted hover:bg-surface-3",
                      )}
                    >
                      <Icon name="message" size={16} />
                      {!collapsed && (
                        <>
                          <button type="button" className="min-w-0 flex-1 cursor-pointer truncate text-left text-xs" onClick={() => onSelectSession(session)}>
                            {session.title}
                          </button>
                          <button
                            type="button"
                            aria-label={`会话操作：${session.title}`}
                            aria-expanded={menuOpen}
                            onClick={() => {
                              setOpenSessionMenuId((current) => current === session.id ? null : session.id);
                              setConfirmDeleteId(null);
                              setSessionMenuError("");
                            }}
                            className="grid h-6 w-6 shrink-0 cursor-pointer place-items-center rounded-tag text-faint transition-colors hover:bg-surface-3 hover:text-ink"
                          >
                            <Icon name="more" size={14} />
                          </button>
                        </>
                      )}
                    </div>
                    {menuOpen && !collapsed && (
                      <div className="mx-1.5 mt-1 rounded-control border border-line bg-surface p-1.5 shadow-card">
                        {!confirming ? (
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteId(session.id)}
                            className="flex w-full cursor-pointer items-center gap-2 rounded-tag px-2 py-1.5 text-left text-[11px] text-red transition-colors hover:bg-red-soft"
                          >
                            <Icon name="trash" size={13} />删除对话
                          </button>
                        ) : (
                          <div className="px-1 py-1">
                            <p className="whitespace-normal text-[10px] leading-4 text-muted">删除后将从列表隐藏，审计记录仍会保留。</p>
                            <div className="mt-2 flex justify-end gap-1.5">
                              <button
                                type="button"
                                disabled={deleting}
                                onClick={() => setConfirmDeleteId(null)}
                                className="h-7 cursor-pointer rounded-tag px-2 text-[10px] text-muted hover:bg-surface-3 disabled:cursor-not-allowed"
                              >
                                取消
                              </button>
                              <button
                                type="button"
                                disabled={deleting}
                                onClick={() => removeSession(session)}
                                className="h-7 cursor-pointer rounded-tag bg-red px-2.5 text-[10px] font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-wait disabled:opacity-60"
                              >
                                {deleting ? "删除中" : "确认删除"}
                              </button>
                            </div>
                            {sessionMenuError && <p className="mt-1.5 whitespace-normal text-[10px] text-red">{sessionMenuError}</p>}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              {sessions !== null && !sessionsError && !filteredSessions.length && !collapsed && (
                <div className="px-2 py-2.5 text-[11px] text-faint">没有匹配的分析会话</div>
              )}
            </div>
          </div>
        </>
      ) : (
        !collapsed && (
          <div className="border-t border-line px-2 pb-2.5">
            <div className="px-2 pb-1.5 pt-2.5 text-[11px] font-semibold text-muted">治理待办</div>
            <div className="flex flex-col gap-0.5">
              <QueueItem dotClass="bg-amber" title="3 个指标待审核" caption="质量、设备主题" count={3} onClick={() => onNavigate("knowledge")} />
              <QueueItem dotClass="bg-primary" title="2 个字段待映射" caption="设备运行数据" count={2} onClick={() => onNavigate("resources")} />
              <QueueItem dotClass="bg-red" title="1 条拦截待复核" caption="今天 10:21" count={1} onClick={() => onNavigate("security")} />
            </div>
          </div>
        )
      )}

      <div className={cn("mt-auto flex min-h-16 items-center gap-2 border-t border-line px-2 py-2", collapsed && "justify-center px-0")}>
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-control border border-line bg-surface text-xs font-bold text-ink shadow-card">{user.initials}</div>
        {!collapsed && (
          <>
            <div className="flex min-w-0 flex-1 flex-col">
              <strong className="truncate text-[11px] font-semibold text-ink">{user.name}</strong>
              <span className="mt-0.5 truncate text-[10px] text-muted">{user.title}</span>
            </div>
            <button
              type="button"
              aria-label="退出登录"
              title="退出登录"
              onClick={onLogout}
              className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-control border border-line bg-surface text-muted transition-colors hover:border-line-strong hover:bg-surface-2 hover:text-ink"
            >
              <Icon name="logout" size={15} />
            </button>
            <button
              type="button"
              aria-label="暗夜模式"
              aria-pressed={darkMode}
              title="暗夜模式"
              onClick={onToggleDarkMode}
              className={cn(
                "grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-control border transition-colors",
                darkMode ? "border-line-strong bg-surface text-ink shadow-card" : "border-line bg-surface text-muted hover:border-line-strong hover:bg-surface-2 hover:text-ink",
              )}
            >
              <Icon name={darkMode ? "sun" : "moon"} size={15} />
            </button>
          </>
        )}
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="调整侧栏宽度"
        tabIndex={0}
        className="absolute inset-y-0 right-0 z-10 w-1.5 translate-x-1/2 cursor-col-resize outline-none before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-transparent before:transition-colors before:content-[''] hover:before:bg-primary/40 focus-visible:before:bg-primary/60"
        onPointerDown={onResizeStart}
        onKeyDown={onResizeKeyDown}
        onDoubleClick={onResizeReset}
      />
    </aside>
  );
}

function SidebarNavItem({ item, page, collapsed, expanded, onNavigate, onToggle }) {
  const children = item.children ?? [];
  const hasChildren = children.length > 0;
  const childActive = children.some((child) => child.id === page);
  const active = item.id === page || childActive;

  if (!hasChildren) {
    return (
      <button
        type="button"
        title={item.label}
        onClick={() => onNavigate(item.id)}
        className={cn(
          "relative flex min-h-10 w-full cursor-pointer items-center gap-2 rounded-control px-2 py-1.5 text-left transition-colors",
          collapsed && "justify-center px-0",
          page === item.id ? "bg-surface text-ink shadow-card ring-1 ring-inset ring-line" : "text-muted hover:bg-surface-3 hover:text-ink",
        )}
      >
        {page === item.id && !collapsed && <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-primary" aria-hidden="true" />}
        <span className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-tag"><Icon name={item.icon} size={16} /></span>
        {!collapsed && <span className="min-w-0 flex-1 truncate text-body font-semibold">{item.label}</span>}
      </button>
    );
  }

  if (collapsed) {
    return children.map((child) => (
      <button
        type="button"
        key={child.id}
        title={child.label}
        aria-label={child.label}
        onClick={() => onNavigate(child.id)}
        className={cn(
          "relative flex min-h-10 w-full cursor-pointer items-center justify-center rounded-control px-0 text-muted transition-colors hover:bg-surface-3 hover:text-ink",
          page === child.id && "bg-surface text-ink shadow-card ring-1 ring-inset ring-line",
        )}
      >
        <span className="grid h-[26px] w-[26px] place-items-center rounded-tag"><Icon name={child.icon} size={16} /></span>
      </button>
    ));
  }

  return (
    <div className={cn("nav-tree-item", active && "nav-tree-item--active")}>
      <button
        type="button"
        title={item.label}
        aria-expanded={expanded}
        onClick={() => onToggle(item.id)}
        className={cn(
          "relative flex min-h-10 w-full cursor-pointer items-center gap-2 rounded-control px-2 py-1.5 text-left transition-colors",
          collapsed && "justify-center px-0",
          expanded || active ? "bg-surface-3 text-ink" : "text-muted hover:bg-surface-3 hover:text-ink",
        )}
      >
        <span className={cn("grid h-[26px] w-[26px] shrink-0 place-items-center rounded-tag", active && "bg-surface text-primary shadow-sm")}><Icon name={item.icon} size={16} /></span>
        {!collapsed && <span className="min-w-0 flex-1 truncate text-body font-semibold">{item.label}</span>}
        {!collapsed && <Icon name="chevron" size={15} className={cn("transition-transform duration-200 ease-out", expanded && "rotate-90")} />}
      </button>
      {!collapsed && (
        <div
          aria-hidden={!expanded}
          className={cn(
            "grid transition-[grid-template-rows,opacity,transform] duration-200 ease-out",
            expanded ? "grid-rows-[1fr] translate-y-0 opacity-100" : "pointer-events-none grid-rows-[0fr] -translate-y-1 opacity-0",
          )}
        >
          <div className="overflow-hidden">
            <div className="mt-1 flex flex-col gap-0.5 rounded-control bg-surface-3/75 p-1">
              {children.map((child) => (
                <button
                  type="button"
                  key={child.id}
                  title={child.label}
                  tabIndex={expanded ? 0 : -1}
                  onClick={() => onNavigate(child.id)}
                  className={cn(
                    "flex h-9 w-full cursor-pointer items-center gap-2 rounded-[8px] px-2 text-left text-muted transition-all hover:bg-surface/70 hover:text-ink",
                    page === child.id && "bg-surface text-ink shadow-card ring-1 ring-inset ring-line",
                  )}
                >
                  <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-tag", page === child.id && "bg-primary-soft text-primary")}><Icon name={child.icon} size={14} /></span>
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{child.label}</span>
                  {page === child.id && <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function QueueItem({ dotClass, title, caption, count, onClick }) {
  return (
    <button type="button" onClick={onClick} className="flex w-full cursor-pointer items-center gap-2.5 rounded-control p-2 text-left transition-colors hover:bg-surface-3">
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotClass)} />
      <span className="min-w-0 flex-1">
        <strong className="block truncate text-xs font-semibold text-ink">{title}</strong>
        <small className="mt-0.5 block text-[10px] text-faint">{caption}</small>
      </span>
      <b className="grid h-5 min-w-5 shrink-0 place-items-center rounded-full border border-line bg-surface px-1 text-[10px] font-semibold text-ink-2">{count}</b>
    </button>
  );
}

function Topbar({ page, user, evidenceOpen, unreadCount, onToggleEvidence, onOpenNavigation, onOpenMessages, onOpenProfile, activeTenant, tenants, onTenantChange }) {
  const meta = pageMeta[page] ?? pageMeta.overview;

  return (
      <header className="app-topbar flex h-16 shrink-0 items-center justify-between gap-2 border-b border-line bg-surface/95 px-3 backdrop-blur md:gap-5 md:px-6">
      <div className="flex min-w-0 flex-1 items-center gap-2 md:gap-3">
        <button type="button" aria-label="打开导航" onClick={onOpenNavigation} className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-control border border-line bg-surface text-muted md:hidden"><Icon name="menu" size={17} /></button>
        <h1 className="m-0 truncate text-lg font-semibold tracking-tight text-ink">{meta.title}</h1>
      </div>
      <div className="flex min-w-0 items-center gap-1.5 md:gap-3">
        <TenantSwitcher activeTenant={activeTenant} tenants={tenants} onChange={onTenantChange} />
        {page === "analysis" && (
          <Button
            variant="secondary"
            aria-expanded={evidenceOpen}
            aria-controls="analysis-evidence-panel"
            className={cn(evidenceOpen && "border-line-strong bg-surface-2 text-ink")}
            onClick={onToggleEvidence}
          >
            <Icon name="panel" size={16} />
            分析证据链
          </Button>
        )}
        <button
          type="button"
          className="relative grid h-[34px] w-[34px] cursor-pointer place-items-center rounded-control border border-transparent text-muted transition-colors hover:border-line hover:bg-surface-2 hover:text-ink"
          aria-label={unreadCount ? `消息中心，${unreadCount} 条未读` : "消息中心"}
          onClick={onOpenMessages}
        >
          <Icon name="bell" />
          {unreadCount > 0 && <span className="absolute -right-1 -top-1 grid min-h-4 min-w-4 place-items-center rounded-full bg-critical px-1 text-[9px] font-bold text-white ring-2 ring-surface">{unreadCount > 99 ? "99+" : unreadCount}</span>}
        </button>
        <button
          type="button"
          aria-label="打开个人主页"
          onClick={onOpenProfile}
          className="grid h-9 w-9 cursor-pointer place-items-center rounded-full border border-transparent bg-surface-3 text-xs font-bold text-ink-2 transition-[transform,color,background-color,border-color] hover:-translate-y-0.5 hover:border-line-strong hover:bg-surface-2 hover:text-ink active:translate-y-0"
          title={`${user.name} · ${user.title}`}
        >
          {user.initials}
        </button>
      </div>
    </header>
  );
}

function TenantSwitcher({ activeTenant, tenants, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (!ref.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  return (
    <div ref={ref} className="relative hidden sm:block">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="group flex h-9 max-w-[230px] cursor-pointer items-center gap-2 rounded-control border border-line bg-surface px-2.5 text-left transition-colors hover:border-line-strong hover:bg-surface-2"
      >
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-tag bg-primary-soft text-primary"><Icon name="database" size={13} /></span>
        <span className="min-w-0 flex-1">
          <strong className="block truncate text-[11px] font-semibold text-ink">{activeTenant?.name ?? "正在确认企业"}</strong>
          <small className="block text-[9px] text-muted">{activeTenant?.role === "owner" ? "企业所有者" : activeTenant?.role === "admin" ? "企业管理员" : "企业成员"}</small>
        </span>
        {tenants.length > 1 && <Icon name="chevron" size={13} className={cn("text-faint transition-transform", open && "rotate-90")} />}
      </button>
      {open && tenants.length > 1 && (
        <div role="listbox" aria-label="切换企业空间" className="absolute right-0 top-11 z-50 w-72 overflow-hidden rounded-card border border-line bg-surface p-1.5 shadow-card-hover">
          <div className="px-2.5 pb-1.5 pt-1 text-[10px] font-semibold text-muted">企业空间</div>
          {tenants.map((tenant) => (
            <button
              type="button"
              role="option"
              aria-selected={tenant.id === activeTenant?.id}
              key={tenant.id}
              onClick={() => { setOpen(false); onChange(tenant.id); }}
              className={cn(
                "flex w-full cursor-pointer items-center gap-2.5 rounded-control px-2.5 py-2 text-left transition-colors hover:bg-surface-2",
                tenant.id === activeTenant?.id && "bg-primary-soft",
              )}
            >
              <span className={cn("h-2 w-2 shrink-0 rounded-full", tenant.id === activeTenant?.id ? "bg-primary" : "bg-line-strong")} />
              <span className="min-w-0 flex-1">
                <strong className="block truncate text-xs font-semibold text-ink">{tenant.name}</strong>
                <small className="text-[10px] text-muted">{tenant.role === "owner" ? "所有者" : tenant.role === "admin" ? "管理员" : "成员"}</small>
              </span>
            </button>
          ))}
          <p className="mx-2.5 mt-1 border-t border-line py-2 text-[10px] leading-4 text-faint">切换后，数据源、调查记录、记忆与指标口径会同步切换。</p>
        </div>
      )}
    </div>
  );
}
