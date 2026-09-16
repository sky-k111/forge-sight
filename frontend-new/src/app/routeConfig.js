export const routeByPage = {
  overview: "/workspace/overview",
  analysis: "/workspace/analysis",
  sessions: "/workspace/sessions",
  modeling: "/workspace/modeling",
  memory: "/workspace/memory",
  profile: "/workspace/profile",
  reports: "/workspace/reports",
  alerts: "/workspace/alerts",
  messages: "/workspace/messages",
  "admin-messages": "/admin/messages",
  "admin-overview": "/admin/overview",
  tenants: "/admin/tenants",
  knowledge: "/admin/knowledge",
  resources: "/admin/resources",
  "monitoring-rules": "/admin/monitoring-rules",
  "model-training": "/admin/model-training",
  "model-governance": "/admin/models",
  "model-archive": "/admin/model-archive",
  "model-drift": "/admin/model-drift",
  "skill-governance": "/admin/skills",
  "memory-governance": "/admin/memory",
  settings: "/admin/settings",
  security: "/admin/security",
  messaging: "/admin/messaging",
};

export const pageByPath = Object.fromEntries(Object.entries(routeByPage).map(([page, path]) => [path, page]));

export function pageFromPath(pathname) {
  if (pathname.startsWith("/workspace/reports/")) return "reports";
  if (pathname.startsWith("/workspace/alerts/")) return "alerts";
  if (pathname.startsWith("/workspace/messages/")) return "messages";
  if (pathname.startsWith("/admin/messages/")) return "admin-messages";
  return pageByPath[pathname];
}

export function roleFromPath(pathname) {
  return pathname.startsWith("/admin") ? "admin" : "business";
}
