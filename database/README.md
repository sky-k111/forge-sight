# ForgeSight 数据库初始化

ForgeSight 使用两个职责分离的数据库：

| 数据库 | 引擎 | 用途 | Agent 权限 |
| --- | --- | --- | --- |
| `forge_sight` | MySQL 8.0+ | 制造业业务数据、官方比赛样例数据 | 只读 |
| `forge_sight_app` | PostgreSQL 14+ | 数据源、元数据、业务知识、会话、Agent 状态、报告版本与分发记录 | 读写 |

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
├─ mysql/
│  ├─ 001_schema.sql
│  ├─ 002_seed_official_data.sql
│  └─ 003_validate.sql
├─ postgresql/
│  ├─ 000_create_database.sql
│  └─ 001_schema.sql
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

提交 Git 时不要添加 `backend/.env`、密码或本机数据库 dump。
