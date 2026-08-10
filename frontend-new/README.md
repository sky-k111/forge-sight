# ForgeSight Frontend

ForgeSight 是面向制造企业数据底座的智能问析与建模工作台。前端包含业务人员工作区和管理员工作区，两类页面通过独立 URL 路由访问。建模采用“Agent 自动建模 + 业务人员人工控制 + 管理员模型治理”：Agent 生成可复核任务草案，训练由受控 Python 引擎执行，业务端负责确认、评估与推理，管理端负责版本、发布和运行边界。

## 启动

```bash
npm install
npm run dev
```

生产构建与测试：

```bash
npm run build
npm test
```

## 页面路由

- `/workspace/overview`：分析总览
- `/workspace/analysis`：智能问析与分析证据链
- `/workspace/modeling`：智能建模任务台（Agent 创建、人工确认、候选模型比较、评估与推理）
- `/workspace/reports`：分析报告
- `/admin/overview`：治理总览
- `/admin/knowledge`：业务知识与指标口径
- `/admin/resources`：数据连接、元数据和数据预览
- `/admin/models`：模型治理（版本、复核、发布、下线和运行策略）
- `/admin/settings`：模型、执行引擎与安全策略
- `/admin/security`：安全审计

页面采用路由级懒加载。数据源管理、数据资源、业务知识、安全审计和智能问析已经接入 FastAPI 真实接口；智能问析支持 SSE 实时状态、刷新恢复、证据链、合并澄清、Checkpoint 分支和 Token 用量展示。分析总览仍包含演示聚合数据，报告与建模页面保留为后续阶段的产品原型。

## 建模职责边界

- LLM/Agent：理解业务目标、识别任务类型、定位数据、形成建模草案和解释结果，不直接训练模型。
- 业务人员：确认目标、标签、特征和候选模型，可选择智能推荐或人工指定，查看评估并发起推理。
- Python 建模引擎：完成数据准备、训练、评估、候选模型比较和推理。
- 管理员：复核模型版本，控制发布/下线、权限、资源策略和审计。
