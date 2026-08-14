# T 数据库迁移演练记录

<!-- markdownlint-disable MD013 -->

## 演练范围

- PostgreSQL 16，隔离 schema，执行 `001` 至 `007` 全部脚本。
- 迁移前写入存量用户、周报、订阅、消息和接收人，其中保留历史 `feishu + after_review` 值。
- 连续执行两次迁移，验证幂等；迁移后验证历史读取、新表、约束、冻结版本和草稿删除。

## 结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 全量迁移可重复执行 | 通过 | `test_domain_migration_is_repeatable_and_preserves_legacy_subscription` |
| 旧订阅与历史消息保持可读 | 通过 | 同上 |
| 发布版本不可修改/删除，草稿可删除 | 通过 | 同上 |
| 旧 `report_file` 缺列可原地升级 | 通过 | `test_domain_migration_upgrades_legacy_report_file_before_creating_indexes` |
| 不删除或重建业务数据 | 通过 | 迁移均为增量 DDL，测试核对历史行 |

演练发现并修复：旧库已有 `report_file` 时，`003_domain_model.sql` 曾先创建依赖新列的索引，导致升级中断；现先用 `ADD COLUMN IF NOT EXISTS` 补齐租约、重试和校验字段再建索引。

## 生产执行与恢复

1. 停止调度和邮件 Worker，保留 Web 只读访问。
2. 对平台库做可恢复快照，记录关键表行数；不使用清库或重建方案。
3. 执行 `backend/.venv/Scripts/python.exe database/bootstrap.py`，核对日志无失败。
4. 抽查历史用户、报告、订阅、消息，并运行健康与运营指标接口。
5. 先恢复应用与文件/消息 Worker，再按灰度恢复调度、邮件。
6. 若应用版本需回退，保留新增表和字段，关闭新 Worker；禁止用 down migration 删除事件或版本。
