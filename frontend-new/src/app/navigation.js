export const roleMenus = {
  business: [
    {
      label: "业务工作台",
      items: [
        { id: "overview", label: "分析总览", icon: "overview" },
        {
          id: "analysis-center",
          label: "分析中心",
          icon: "analysis",
          children: [
            { id: "analysis", label: "智能问析", icon: "question" },
            { id: "sessions", label: "历史会话", icon: "history" },
            { id: "modeling", label: "智能建模", icon: "model" },
            { id: "reports", label: "报告中心", icon: "report" },
          ],
        },
      ],
    },
  ],
  admin: [
    {
      label: "管理员工作台",
      items: [
        { id: "admin-overview", label: "治理总览", icon: "overview" },
        {
          id: "data-governance",
          label: "数据治理",
          icon: "database",
          children: [
            { id: "knowledge", label: "业务知识", icon: "knowledge" },
            { id: "resources", label: "数据资源", icon: "database" },
          ],
        },
        {
          id: "platform-governance",
          label: "平台治理",
          icon: "settings",
          children: [
            { id: "model-governance", label: "模型治理", icon: "model" },
            { id: "settings", label: "系统配置", icon: "settings" },
            { id: "security", label: "安全审计", icon: "shield" },
          ],
        },
      ],
    },
  ],
};

export const pageMeta = {
  overview: { eyebrow: "业务人员工作区", title: "分析总览", subtitle: "数据更新至 2026-07-15 · 3 项异常待关注" },
  analysis: { eyebrow: "业务人员工作区", title: "智能问析", subtitle: "自然语言查询、分析过程与执行结果" },
  sessions: { eyebrow: "业务人员工作区", title: "历史会话", subtitle: "全部历史问答会话，点击继续分析" },
  modeling: { eyebrow: "业务人员工作区", title: "智能建模", subtitle: "Agent 自动建模与人工复核" },
  reports: { eyebrow: "业务人员工作区", title: "报告中心", subtitle: "4 份报告 · 1 份待复核" },
  "admin-overview": { eyebrow: "管理员工作区", title: "治理总览", subtitle: "3 类治理事项待处理 · 最后巡检 10:36" },
  knowledge: { eyebrow: "管理员工作区", title: "业务知识", subtitle: "77 个知识项 · 3 个待审核" },
  resources: { eyebrow: "管理员工作区", title: "数据资源", subtitle: "10 张表 · 76 个字段 · 15 条关联关系" },
  "model-governance": { eyebrow: "管理员工作区", title: "模型治理", subtitle: "4 个模型 · 1 个待复核" },
  settings: { eyebrow: "管理员工作区", title: "系统配置", subtitle: "4 项运行依赖在线" },
  security: { eyebrow: "管理员工作区", title: "安全审计", subtitle: "过去 24 小时校验 482 次 · 拦截 3 次" },
};
