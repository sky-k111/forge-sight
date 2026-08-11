# ForgeSight 团队协作约定

> 5 人团队共用 `skyer12345678/forge-sight`。本约定是**团队共识**（GitHub 免费计划私有仓库无法开启强制分支保护，靠约定执行）。

## 总原则

- `main` 分支是「受保护」分支：**禁止任何人直接 push**（包括仓库管理员，自己动手也要走 PR，以身作则）
- 所有代码改动必须走 **PR（Pull Request）** 流程，至少 1 人 review 通过后由作者合并
- 提 PR 前必须本地跑通全部测试（见「测试红线」）

## 一、分支命名

```
feature/xxx    新功能，如 feature/feedback-loop
fix/xxx        缺陷修复，如 fix/report-split
docs/xxx       文档改动，如 docs/contributing
refactor/xxx   重构
```

一次改动一个分支，分支生命周期 = 功能开发周期，合并后删除。

## 二、标准流程

```bash
# 1. 从最新 main 开分支
git checkout main && git pull origin main
git checkout -b feature/xxx

# 2. 开发……提交（提交信息见下）
git add . && git commit -m "feat: 实现问答反馈闭环"

# 3. 推送分支
git push -u origin feature/xxx

# 4. 在 GitHub 提 PR：feature/xxx -> main
#    描述写清楚：改了什么 / 为什么 / 怎么验证

# 5. 等至少 1 人 review 通过（回复 Comments/Requested changes 也要处理完）

# 6. 作者合并（建议 Squash and merge，历史干净），然后删分支
```

## 三、提交信息规范

```
feat:    新功能
fix:     缺陷修复
docs:    文档
refactor:重构（行为不变）
chore:   构建/脚本/杂项
test:    测试相关
```

一行描述，不超过 50 字。示例：`feat: 历史会话支持按数据源筛选`。

## 四、测试红线（提 PR 的硬性要求）

```bash
# 后端（backend 目录）
.venv\Scripts\python -m pytest -q        # 当前 114 个，必须全绿

# 前端（frontend-new 目录）
npm test                                  # 当前 62 个，必须全绿
```

改动涉及页面/接口时，本地跑起来走一遍：`.\fs.bat` 一键启动，浏览器人工验证。

## 五、不可触碰的规则

- `backend/.env`（真实凭据：DeepSeek key、数据库密码）**永不提交、永不打印、永不外发**；入库的只有 `.env.example`
- 数据库**禁止删库重建**，schema 变更一律走迁移脚本
- 不要 `git push --force` 到 main；force push 只允许在**自己的**功能分支

## 六、冲突处理

合并 main 到自己的分支解决冲突，**不要直接 rebase 别人正在用的分支**：

```bash
git checkout feature/xxx
git merge main        # 解决冲突后提交
git push origin feature/xxx
```

## 七、疑问

- 权限：仓库 admins（sky-k111）是 `skyer12345678/forge-sight` 的 owner
- 提 PR 前读一下 [README.md](README.md) 和 [AGENTS.md](AGENTS.md)，了解项目结构与开发约束
