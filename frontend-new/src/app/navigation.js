export const roleMenus = {
  business: [
    {
      label: "业务工作台",
      items: [
        { id: "overview", label: "今日工作台", icon: "overview" },
        {
          id: "analysis-center",
          label: "分析与调查",
          icon: "analysis",
          children: [
            { id: "analysis", label: "发起分析", icon: "question" },
            { id: "sessions", label: "调查记录", icon: "history" },
            { id: "modeling", label: "智能建模", icon: "model" },
          ],
        },
        { id: "reports", label: "报告与交接", icon: "report" },
        { id: "alerts", label: "智能异常监测", icon: "alert" },
      ],
    },
  ],
  admin: [
    {
      label: "管理员工作台",
      items: [
        { id: "admin-overview", label: "治理工作台", icon: "overview" },
        {
          id: "data-semantic-governance",
          label: "数据与语义",
          icon: "database",
          children: [
            { id: "resources", label: "数据源与资源", icon: "database" },
            { id: "knowledge", label: "业务知识与指标", icon: "knowledge" },
            { id: "monitoring-rules", label: "监控规则", icon: "alert" },
          ],
        },
        {
          id: "ai-model-governance",
          label: "AI 与模型",
          icon: "model",
          children: [
            { id: "model-governance", label: "模型治理", icon: "model" },
            { id: "skill-governance", label: "分析技能", icon: "knowledge" },
            { id: "memory-governance", label: "记忆治理", icon: "memory" },
          ],
        },
        {
          id: "platform-operations",
          label: "平台运维",
          icon: "settings",
          children: [
            { id: "security", label: "权限与审计", icon: "shield" },
            { id: "deliveries", label: "消息渠道与投递", icon: "send" },
            { id: "announcements", label: "公告管理", icon: "message" },
            { id: "settings", label: "系统设置", icon: "settings" },
          ],
        },
      ],
    },
  ],
};

export const pageMeta = {
  overview: {
    eyebrow: "业务人员工作区",
    title: "今日工作台",
    subtitle: "关注数据状态、继续调查并完成当日分析工作",
  },
  analysis: {
    eyebrow: "业务人员工作区",
    title: "发起分析",
    subtitle: "用业务语言提出问题，获得可追溯的分析结果",
  },
  sessions: {
    eyebrow: "业务人员工作区",
    title: "调查记录",
    subtitle: "回看业务问题、分析过程与已有结论",
  },
  modeling: {
    eyebrow: "业务人员工作区",
    title: "智能建模",
    subtitle: "Agent 自动建模与人工复核",
  },
  reports: {
    eyebrow: "业务人员工作区",
    title: "报告与交接",
    subtitle: "周报、月报、专题分析与交接信息统一管理",
  },
  alerts: {
    eyebrow: "业务人员工作区",
    title: "智能异常监测",
    subtitle: "查看生产异常、恢复状态与可追溯证据",
  },
  messages: {
    eyebrow: "消息工作区",
    title: "消息中心",
    subtitle: "周报与管理员公告统一送达",
  },
  "admin-messages": {
    eyebrow: "管理员工作区",
    title: "消息中心",
    subtitle: "周报与管理员公告统一送达",
  },
  "admin-overview": {
    eyebrow: "管理员工作区",
    title: "治理工作台",
    subtitle: "集中处理数据、语义、模型和平台治理事项",
  },
  knowledge: {
    eyebrow: "管理员工作区",
    title: "业务知识与指标",
    subtitle: "维护业务对象、指标口径、规则与物理绑定",
  },
  resources: {
    eyebrow: "管理员工作区",
    title: "数据源与资源",
    subtitle: "管理数据连接、表字段、关系和同步状态",
  },
  "monitoring-rules": {
    eyebrow: "管理员工作区",
    title: "监控规则",
    subtitle: "通过元数据绑定管理监控口径、调度与通知范围",
  },
  "model-governance": {
    eyebrow: "管理员工作区",
    title: "模型治理",
    subtitle: "4 个模型 · 1 个待复核",
  },
  memory: {
    eyebrow: "个人设置",
    title: "分析偏好与记忆",
    subtitle: "管理 ForgeSight 自动整理的偏好与调查经验",
  },
  "skill-governance": {
    eyebrow: "管理员工作区",
    title: "分析技能",
    subtitle: "审核由成功调查沉淀的程序性知识，并控制版本与生效范围",
  },
  "memory-governance": {
    eyebrow: "管理员工作区",
    title: "记忆治理",
    subtitle: "控制长期记忆的作用域、生命周期、召回实验与技能提升",
  },
  settings: {
    eyebrow: "管理员工作区",
    title: "系统设置",
    subtitle: "维护运行依赖、默认策略和平台参数",
  },
  security: {
    eyebrow: "管理员工作区",
    title: "权限与审计",
    subtitle: "检查访问权限、只读边界和执行审计记录",
  },
  announcements: {
    eyebrow: "管理员工作区",
    title: "公告管理",
    subtitle: "创建、发布和撤回站内公告",
  },
  deliveries: {
    eyebrow: "管理员工作区",
    title: "消息渠道与投递",
    subtitle: "管理邮件、飞书等外部渠道和失败重发",
  },
};
