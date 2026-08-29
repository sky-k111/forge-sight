# W02 PostgreSQL 适配补强

> 状态：代码整改完成，真实 PostgreSQL 多 Schema 集成验收待完成
>
> 目标：解决当前 PostgreSQL “能够连接但适配范围有限”的问题，使其达到与 MySQL 相同的正式支持标准。

## 一、现有限制清单

| 编号 | 当前限制 | 影响 | 解决方案 |
| --- | --- | --- | --- |
| PG-01 | `PG_SCHEMA = "public"` 硬编码 | 企业常用 `mes`、`qms`、`erp` 等 Schema 无法扫描 | 接入时列出非系统 Schema，由管理员选择一个或多个业务 Schema |
| PG-02 | `DataTable` 仅以 `source_id + name` 唯一 | `sales.orders` 和 `archive.orders` 会冲突 | 增加 `schema_name`，唯一键改为 `source_id + schema_name + name` |
| PG-03 | 表和字段注释固定返回空字符串 | 中文业务语义召回质量下降 | 使用 `obj_description`、`col_description` 和 `pg_catalog` 读取注释 |
| PG-04 | PK/FK 查询未完整按 Schema 关联 | 同名约束、跨 Schema 外键可能误配 | 连接 `constraint_schema/table_schema`，关系两端都保存 Schema |
| PG-05 | 采样、计数、更新时间推断只引用表名 | 非 public 表失败或命中错误对象 | 所有 SQL 使用 Adapter 的全限定标识符 |
| PG-06 | 数据源配置没有 Schema/SSL/search path 等选项 | 无法适配企业安全连接和多 Schema | 增加 `selected_schemas`、`sslmode`、`application_name`、证书引用等配置 |
| PG-07 | SQL AST 始终按 MySQL 解析 | PostgreSQL 合法语法可能误判，MySQL 语法可能漏过 | `sqlglot.parse(..., read="postgres")`，并按 PG 方言校验 |
| PG-08 | 白名单只比较裸表名 | 可产生跨 Schema 越权或歧义 | 白名单比较规范化 `schema.table`，未限定同名表触发澄清 |
| PG-09 | 方言提示只是一段二选一字符串 | 日期、JSON、类型转换等复杂 SQL 不稳定 | Adapter 提供结构化 dialect profile 和示例 |
| PG-10 | 引擎缓存只按 `source.id` | 修改连接配置后可能使用旧池 | 使用租户、数据源和连接修订号作为缓存键 |
| PG-11 | 密码位于普通数据源字段 | 不符合生产凭据边界 | 数据源只保存 `credential_ref`，加密密钥存储与业务记录分离 |
| PG-12 | EXPLAIN 只有摘要解析测试 | 缺少真实 PG 方言、权限、超时和计划门控验收 | 增加 PostgreSQL 真实实例集成测试和慢查询样例 |

## 二、Schema 发现与选择

默认排除系统 Schema：

```text
pg_catalog
information_schema
pg_toast
以 pg_temp_ / pg_toast_temp_ 开头的 Schema
```

查询候选：

```sql
SELECT nspname
FROM pg_namespace
WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND nspname NOT LIKE 'pg_temp_%'
  AND nspname NOT LIKE 'pg_toast_temp_%'
ORDER BY nspname;
```

接入流程：

1. 使用连接账号测试连接；
2. 调用 `list_schemas`；
3. 管理员选择允许扫描的 Schema；
4. 保存 `selected_schemas`；
5. 扫描和查询只能访问该集合；
6. 修改集合视为连接资源范围变更，触发重新扫描和全量语义索引。

不依赖 `search_path` 作为安全边界。运行 SQL 和预览表应尽量使用全限定名，`search_path` 只作为兼容配置。

## 三、表、字段和注释扫描

表扫描应覆盖普通表和视图，并明确对象类型：

```sql
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    c.relkind AS object_kind,
    obj_description(c.oid, 'pg_class') AS table_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(:schemas)
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY n.nspname, c.relname;
```

字段扫描应返回：

- Schema、表名、字段名；
- PostgreSQL 原生类型和规范化类型；
- nullable、默认值、生成列；
- ordinal position；
- 字段注释；
- 是否主键；
- 可选的枚举或域类型信息。

字段注释使用 `col_description(table_oid, attnum)` 获取，而不是继续写空字符串。

## 四、主外键关系

关系必须携带：

```text
source_id
from_schema
from_table
from_column
to_schema
to_table
to_column
constraint_name
relation_source = scanned
```

查询连接条件必须包含约束 Schema 和表 Schema，禁止只按 `constraint_name` 或 `table_name` 连接。跨 Schema 外键是合法关系，但只有关系两端都位于允许 Schema 集合时才进入 Agent 上下文。

## 五、SQL 方言与安全

使用：

```python
sqlglot.parse(sql, read="postgres")
```

至少覆盖：

- 双引号标识符；
- `ILIKE`；
- `DATE_TRUNC`；
- `FILTER (WHERE ...)`；
- `::type` 与 `CAST`；
- `INTERVAL`；
- JSON/JSONB 读取表达式；
- `FULL OUTER JOIN`；
- CTE 和窗口函数。

继续禁止：

- INSERT、UPDATE、DELETE、MERGE；
- CREATE、ALTER、DROP、TRUNCATE；
- COPY；
- `SELECT ... FOR UPDATE/SHARE`；
- 数据修改 CTE；
- 多语句；
- 未登记 Schema 和表；
- 可调用外部程序或高风险扩展函数的语句。

只读连接使用 SELECT-only 数据库账号，并设置：

```sql
SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;
SET statement_timeout = '<configured_ms>';
SET lock_timeout = '<configured_ms>';
SET idle_in_transaction_session_timeout = '<configured_ms>';
```

应用校验是第二道防线，数据库权限是第一道防线。

## 六、成本门控

使用：

```sql
EXPLAIN (FORMAT JSON) <query>
```

严禁在成本预检中使用 `ANALYZE`，避免真实执行候选 SQL。解析至少包含：

- `Plan Rows`；
- `Total Cost`；
- `Seq Scan` 表；
- Join 类型；
- 节点深度；
- 是否命中分区裁剪；
- 是否出现高成本排序或笛卡尔积风险。

门控结果写入证据链，包含方言、阈值、预计行数、成本、全表扫描和放行/阻断原因。

## 七、迁移与兼容

新增迁移建议编号为 `018_multi_database_adapter.sql`，主要包含：

- `data_source.schema_names_json` 或等价配置字段；
- `data_source.config_json`；
- `data_source.credential_ref`；
- `data_source.connection_revision`；
- `data_table.schema_name`；
- `table_field.schema_name`；
- `table_relation` 两端 Schema；
- 相关唯一约束和索引；
- 历史 PostgreSQL 表回填 `public`，历史 MySQL 表回填数据库名或空 Schema 的明确规范值。

迁移必须是幂等增量迁移，不允许删除后重建平台库。旧唯一约束只有在数据回填和重复检查通过后才能替换。

## 八、PostgreSQL 专项验收

- 可以接入非 `public` Schema；
- 可以同时选择两个 Schema；
- 两个 Schema 存在同名表时资源目录均可保存；
- 未限定同名表的模糊 SQL 会被澄清或阻断；
- 表注释和字段注释正确进入知识与语义索引；
- 跨 Schema 外键关系不会错误串联；
- 合法 PostgreSQL 日期、类型转换和窗口函数 SQL 可通过；
- MySQL 反引号和 `DATE_FORMAT` 会在执行前被指出；
- `EXPLAIN (FORMAT JSON)` 门控在真实 PostgreSQL 上通过测试；
- 修改 Schema 或密码后旧连接池失效；
- 证据链能显示准确的 `source/schema/table` 来源。
