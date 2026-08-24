export const routeByPage = {
  overview: "/workspace/overview",
  analysis: "/workspace/analysis",
  sessions: "/workspace/sessions",
  modeling: "/workspace/modeling",
  reports: "/workspace/reports",
  messages: "/workspace/messages",
  "admin-messages": "/admin/messages",
  "admin-overview": "/admin/overview",
  knowledge: "/admin/knowledge",
  resources: "/admin/resources",
  "model-governance": "/admin/models",
  "skill-governance": "/admin/skills",
  settings: "/admin/settings",
  security: "/admin/security",
  announcements: "/admin/announcements",
  deliveries: "/admin/deliveries",
};

export const pageByPath = Object.fromEntries(Object.entries(routeByPage).map(([page, path]) => [path, page]));

export function pageFromPath(pathname) {
  if (pathname.startsWith("/workspace/reports/")) return "reports";
  if (pathname.startsWith("/workspace/messages/")) return "messages";
  if (pathname.startsWith("/admin/messages/")) return "admin-messages";
  return pageByPath[pathname];
}

export function roleFromPath(pathname) {
  return pathname.startsWith("/admin") ? "admin" : "business";
}
