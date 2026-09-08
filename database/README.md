# ForgeSight 数据库初始化

ForgeSight 使用两个职责分离的数据库：

| 数据库 | 引擎 | 用途 | Agent 权限 |
| --- | --- | --- | --- |
| `forge_sight` | MySQL 8.0+（官方样例） | 制造业业务数据、官方比赛样例数据 | 只读 |
| `forge_sight_app` | PostgreSQL 14+ | 数据源、元数据、业务知识、会话、Agent 状态、报告版本与分发记录 | 读写 |

`bootstrap.py` 使用 MySQL 装载官方比赛样例；产品运行时的数据源连接器还支持 PostgreSQL、MariaDB、SQL Server 和 Oracle。它们属于企业外部只读数据源，不替代平台 PostgreSQL，也不会由初始化脚本创建。

初始化文件不包含真实密码，也不包含本地运行产生的会话、分析记录和审计日志。

## 推荐：一条命令初始化

先复制并填写配置：

```powershell
Copy-Item backend/.env.example backend/.env
```

至少配置以下四项：

```dotenv
BUSINESS_DB_URL=mysql+pymysql://forgesight_reader:你的密码@127.0.0.1:3306/forge_sight?charset=utf8mb4
ADMIN_DB_URL=mysql+pymysql://root:你的密码@127.0.0.1:3306/?charset=utf8mb4
PLATFORM_DB_URL=postgresql+psycopg2://postgres:你的密码@127.0.0.1:5432/forge_sight_app
POSTGRES_ADMIN_DB_URL=postgresql+psycopg2://postgres:你的密码@127.0.0.1:5432/postgres
```

在项目根目录执行：

```powershell
backend/.venv/Scripts/python.exe database/bootstrap.py
```

脚本会创建两个数据库和表，导入 8,019 条官方样例记录，创建 MySQL
只读账号，然后登记默认数据源、扫描元数据，并初始化业务语义、物理指标绑定和内置报告模板。

脚本非破坏性且可重复执行：官方数据按主键 Upsert；已有会话、分析记录和管理员维护的知识不会被清空。

常用选项：

```powershell
# 只创建结构
backend/.venv/Scripts/python.exe database/bootstrap.py --schema-only

# 只初始化 PostgreSQL（要求 MySQL 业务库已经可连接）
backend/.venv/Scripts/python.exe database/bootstrap.py --skip-mysql

# 只初始化 MySQL
backend/.venv/Scripts/python.exe database/bootstrap.py --skip-postgres
```

## 文件说明

```text
database/
├─ bootstrap.py
├─ bootstrap_synthetic_annual.py
├─ mysql/
│  ├─ 001_schema.sql
│  ├─ 002_seed_official_data.sql
│  ├─ 003_validate.sql
│  ├─ 004_synthetic_annual_schema.sql
│  └─ 005_synthetic_annual_data.sql
├─ postgresql/
│  ├─ 000_create_database.sql
│  ├─ 001_schema.sql
│  └─ 002...018_*.sql
└─ tools/
   ├─ generate_mysql_seed.py
   └─ generate_platform_schema.py
```

PostgreSQL 主题、对象、规则和指标以
`backend/app/knowledge/seed.py` 为唯一代码源，由 `bootstrap.py` 幂等写入。
这样能先扫描实际数据源，再建立按数据源隔离的物理绑定，也不会将数据源密码写进 SQL。

## 手动执行 SQL

```powershell
mysql -u root -p --execute="source database/mysql/001_schema.sql"
mysql -u root -p --execute="source database/mysql/002_seed_official_data.sql"
mysql -u root -p --execute="source database/mysql/003_validate.sql"

psql -U postgres -d postgres -f database/postgresql/000_create_database.sql
psql -U postgres -d forge_sight_app -f database/postgresql/001_schema.sql
```

手动执行 PostgreSQL SQL 只创建结构。默认数据源、元数据和知识仍应通过
`database/bootstrap.py --skip-mysql` 初始化。

## 初始化一年合成数据源

全新环境可以通过下面脚本“从零重建”一年合成数据库并登记为主平台的新数据源。它不会破坏官方样例（`forge_sight`），只新建 `forge_sight_synthetic_annual_v3r2` 库。

```powershell
backend/.venv/Scripts/python.exe database/bootstrap_synthetic_annual.py --env-file backend/.env
```

该脚本每次都会先删除并重建合成库（数据完全由本地生成脚本产出，不包含用户手动数据），因此可重复执行。执行后会：

- 创建 `forge_sight_synthetic_annual_v3r2` 库和 14 张表（10 张业务表 + 4 张 `synthetic_*` 溯源表）；
- 导入约 2025-07-01 至 2026-07-15 的一年合成数据（平均20 万条记录）；
- 为已具备的只读账号接予合成库的 SELECT 权限；
- 在平台 PostgreSQL 中登记`年度合成制造数据（2025-07 至 2026-07）`数据源并扫描元数据。

只创建结构、不导入数据：

```powershell
backend/.venv/Scripts/python.exe database/bootstrap_synthetic_annual.py --env-file backend/.env --schema-only
```

变量（可选）：`SYNTHETIC_DB_USERNAME` / `SYNTHETIC_DB_PASSWORD` 可以覆盖默认的只读账号。

## 重新生成

官方 CSV 更新后：

```powershell
backend/.venv/Scripts/python.exe database/tools/generate_mysql_seed.py `
  --data-dir ../data `
  --output database/mysql/002_seed_official_data.sql
```

平台 ORM 更新后：

```powershell
backend/.venv/Scripts/python.exe database/tools/generate_platform_schema.py
```

全新环境由 `001_schema.sql` 得到当前完整结构；已有环境由 `002...018` 幂等增量迁移。`018_multi_database_adapter.sql` 增加 Schema 物理身份、数据库专属连接配置和连接池修订号。新增迁移后必须同时重新生成 `001_schema.sql`，保证全新安装与升级安装最终结构一致。

提交 Git 时不要添加 `backend/.env`、密码或本机数据库 dump。
