export const routeByPage = {
  overview: "/workspace/overview",
  analysis: "/workspace/analysis",
  modeling: "/workspace/modeling",
  reports: "/workspace/reports",
  "admin-overview": "/admin/overview",
  knowledge: "/admin/knowledge",
  resources: "/admin/resources",
  "model-governance": "/admin/models",
  settings: "/admin/settings",
  security: "/admin/security",
};

export const pageByPath = Object.fromEntries(Object.entries(routeByPage).map(([page, path]) => [path, page]));

export function pageFromPath(pathname) {
  if (pathname.startsWith("/workspace/reports/")) return "reports";
  return pageByPath[pathname];
}

export function roleFromPath(pathname) {
  return pathname.startsWith("/admin") ? "admin" : "business";
}
