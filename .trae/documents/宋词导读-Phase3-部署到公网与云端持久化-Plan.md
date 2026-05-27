# 《宋词导读》Phase 3：部署到公网 + 云端持久化（Plan）

## Summary
- 目标：把本地 Streamlit 应用部署到 Streamlit Community Cloud，实现手机/PC 多终端通过公网 URL 访问；并解决云端环境无状态导致的 UGC（课堂笔记）持久化问题。
- 代码托管：GitHub。
- 部署平台：Streamlit Community Cloud。
- 云端数据库：Supabase（PostgreSQL）（按你的选择）。
- 持久化与隔离策略（按你的选择）：课堂笔记按 `user_key` 区分用户（避免他人覆盖）；同时支持“本地 SQLite / 云端 PostgreSQL”混合可切换。

## Current State Analysis (Grounded)
- 当前仓库仅发现 PDF 文件：`e:\生成式AI\宋词导读\宋词导读作品选（2026年春）.pdf`；未发现 `app.py`、`requirements.txt`、`src/` 等代码资产。
- Phase 3 的部署与迁移依赖 Phase 2 已产出可运行的 Streamlit 应用与本地数据库 `data/songci.sqlite`。

## Scope / Non-Goals
- In scope：依赖锁定、路径整理、SQLite→PostgreSQL 迁移脚本、DB 连接自动切换、GitHub 托管、Streamlit Cloud 部署、云端笔记写入验证。
- Out of scope：前端大规模重构、背诵算法、外部 AI 实时调用（仅展示已入库内容/占位）。

## Architecture Decisions (Decision Complete)
### 1) 环境切换策略（本地/云端）
- **默认策略**：本地开发使用 SQLite（`data/songci.sqlite`）；部署到 Streamlit Cloud 后使用 Supabase PostgreSQL。
- **切换方式**：`AUTO` 模式下，若检测到 `st.secrets` 含 PostgreSQL 连接信息则用 Postgres，否则回退 SQLite；同时支持显式强制：`DB_MODE=sqlite|postgres|auto`（放在 secrets）。

### 2) UGC 笔记隔离策略（多用户 Key）
- UI 侧边栏新增 `user_key` 输入框（默认空，提示用户填写一个自己专用的 key；手机/PC 填同一个 key 即共享笔记）。
- 数据表按 `(poem_id, user_key)` 唯一约束保存笔记，避免不同用户覆盖。

### 3) 迁移范围
- 支持“混合可切换”：迁移脚本提供全量迁移能力（词库 + AI + 审计 + 笔记表结构），但本地仍可继续以 SQLite 运行。
- 云端部署推荐只读写 Postgres（避免容器无状态的 SQLite 写入问题）。

## Proposed Changes (Files & What/Why/How)
> 下面列出建议新增/修改的文件路径；执行阶段将以 Phase 2 的实际代码结构为准微调。

### A) 依赖锁定与运行入口
- **新增/修改**：`requirements.txt`
  - 必含：`streamlit`（最新稳定版）、`pandas`（按你的要求）、`psycopg2-binary`（PostgreSQL 驱动）
  - 若 Phase 2 已有其他依赖（如 `pdfplumber`、`pytest` 等），保持兼容并最小化引入
- **新增**：`README.md`
  - 本地运行、配置 SQLite/PG、迁移步骤、Streamlit Cloud 部署步骤

### B) 路径与配置整理
- **修改**：`app.py`（或主入口文件）
  - 所有文件路径使用相对路径 + `pathlib.Path(__file__)` 或 `Path.cwd()` 组合，避免硬编码绝对路径
  - `data/songci.sqlite` 使用可配置路径（默认该路径）

### C) DB 访问层改造：SQLite / PostgreSQL 双栈
- **修改**：`src/songci_ui/db.py`
  - 增加 DB 连接工厂：根据 `DB_MODE` 与 `st.secrets` 自动选择 SQLite / Postgres
  - 连接信息来源：
    - 本地：`.streamlit/secrets.toml`（不入 git）
    - 云端：Streamlit Cloud Secrets（面板配置）
  - 使用 `st.cache_resource` 缓存连接（按模式分别缓存）
- **修改**：`src/songci_ui/repo.py`
  - SQL 方言差异处理：尽量写兼容 SQL（`LIKE`、参数占位符、分页）
  - 对 UGC 笔记实现统一接口：SQLite 与 PG 均提供同名方法

### D) 云端迁移脚本（SQLite → Supabase PostgreSQL）
- **新增**：`scripts/migrate_to_cloud.py`
  - 输入：本地 `data/songci.sqlite`
  - 输出：把核心表与数据写入 Supabase PG（连接信息从环境变量或 secrets）
  - 迁移表建议覆盖：
    - `authors`
    - `author_bios`（如果存在）
    - `poems`
    - `ai_insights`（如果存在）
    - `poem_notes`（如果存在；否则只迁 schema）
    - `extracted_lines`（如果 Phase 1 生成了审计表，可选迁移；数据量大时提供 `--skip-audit`）

### E) GitHub 托管与安全
- **新增**：`.gitignore`
  - 必须忽略：`data/*.sqlite`、`.streamlit/secrets.toml`、`.env`、`__pycache__/`、`.pytest_cache/`
- **新增**：`.streamlit/config.toml`（可选）
  - 设置主题与移动端友好布局（不包含 secrets）

## PostgreSQL Schema（云端侧，兼容 SQLite 字段）
> 原则：不引入复杂迁移框架，先用 DDL + upsert 确保可重复执行。

### 1) poems / authors / author_bios（与 Phase 1 对齐）
- 类型策略：保持字段意义一致；`annotations` 继续用 `TEXT` 存 JSON 文本（与 SQLite 完全同构，减少迁移风险）。

```sql
CREATE TABLE IF NOT EXISTS authors (
  id         BIGSERIAL PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint)
);

CREATE TABLE IF NOT EXISTS author_bios (
  id           BIGSERIAL PRIMARY KEY,
  author_id    BIGINT NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
  bio          TEXT NOT NULL,
  source_pdf   TEXT NOT NULL,
  source_page  INTEGER,
  source_order INTEGER,
  bio_hash     TEXT NOT NULL,
  created_at   BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(author_id, bio_hash)
);

CREATE TABLE IF NOT EXISTS poems (
  id                BIGSERIAL PRIMARY KEY,
  seq_no            INTEGER,
  title             TEXT NOT NULL,
  author_id         BIGINT NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,
  headnote          TEXT,
  content           TEXT NOT NULL,
  annotations       TEXT,
  appendix          TEXT,
  is_required       BOOLEAN NOT NULL DEFAULT FALSE,
  proficiency       INTEGER NOT NULL DEFAULT 0,
  next_review_time  BIGINT,
  source_pdf        TEXT NOT NULL,
  source_page_start INTEGER,
  source_page_end   INTEGER,
  created_at        BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at        BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(source_pdf, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_poems_author_id ON poems(author_id);
CREATE INDEX IF NOT EXISTS idx_poems_required ON poems(is_required);
CREATE INDEX IF NOT EXISTS idx_poems_next_review_time ON poems(next_review_time);
```

### 2) UGC 笔记（多用户 Key）
```sql
CREATE TABLE IF NOT EXISTS poem_notes (
  id         BIGSERIAL PRIMARY KEY,
  poem_id    BIGINT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  user_key   TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(poem_id, user_key)
);

CREATE INDEX IF NOT EXISTS idx_poem_notes_user_key ON poem_notes(user_key);
```

### 3) AI 深度解读（混合模式）
```sql
CREATE TABLE IF NOT EXISTS ai_insights (
  id         BIGSERIAL PRIMARY KEY,
  poem_id    BIGINT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source     TEXT,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(poem_id)
);
```

## Secrets / Configuration（Streamlit Cloud）
### 1) 本地（不入 git）
- 新建：`.streamlit/secrets.toml`（在 `.gitignore` 中）
- 配置建议（示例结构，执行阶段会在 README 里给出可复制版本）：
  - `DB_MODE = "auto"`
  - `POSTGRES_DSN = "postgresql://..."`

### 2) 云端（Streamlit Community Cloud 面板）
- 在 Secrets 区填入相同键：`DB_MODE`、`POSTGRES_DSN`
- 严禁把连接串写进代码或提交到 GitHub

## Step-by-Step 实施计划
### Step 1：本地环境整理与依赖锁定
- 扫描 Phase 2 的 `app.py` 与 `src/` 目录，统一所有文件路径获取方式为相对路径（`pathlib.Path` / `os.path.join`）。
- 生成 `requirements.txt`：
  - streamlit（最新稳定版）
  - pandas
  - psycopg2-binary
  - 其余依赖按实际代码最小补齐
- 本地验证：SQLite 模式可正常启动、搜索与展示正常、笔记写入正常。

### Step 2：建立 Supabase 项目与数据库
- Supabase 创建新项目，获取数据库连接串（建议使用 Transaction Pooler/Direct 视具体限制；执行阶段会按 Streamlit Cloud 可用性选择）。
- 在 Supabase SQL Editor 执行本计划的 DDL（或由迁移脚本自动执行）。
- 为后续排障准备：创建只读/读写角色（可选，若使用默认连接串也可先跑通）。

### Step 3：实现迁移脚本 `migrate_to_cloud.py`
- 功能点：
  - 连接 SQLite：读取 `authors/author_bios/poems/ai_insights/poem_notes` 等表（表不存在则跳过并报告）
  - 连接 Postgres：创建表（`CREATE TABLE IF NOT EXISTS`），再批量写入数据
  - 冲突处理：使用 `ON CONFLICT ... DO UPDATE/DO NOTHING` 实现可重复迁移
  - 提供参数：
    - `--sqlite data/songci.sqlite`
    - `--postgres-dsn ...`（或从环境变量/本地 secrets 读取）
    - `--skip-audit`（可选跳过 `extracted_lines` 大表）
    - `--reset`（可选：先 drop 再建，避免脏数据）
- 迁移后校验（脚本内置检查并输出统计）：
  - poems/author 数量一致
  - 必背数量一致
  - 随机抽样若干 poem_id 比对关键字段非空

### Step 4：改造 DB 层支持自动切换（SQLite / PG）
- `db.py`：实现 `get_connection()` 与 `is_postgres_available()`（基于 secrets 是否提供 DSN）。
- `repo.py`：
  - 查询统一：标题/作者/正文关键词搜索
  - 笔记 upsert：按 `(poem_id, user_key)` 更新
  - 方言差异最小化：参数占位符分别适配（SQLite `?` / PG `%s`）或统一封装执行器
- UI 增加 `user_key` 输入框，并把它传给读写笔记 API。

### Step 5：GitHub 托管
- 初始化 Git 仓库
- 编写 `.gitignore`（必须忽略 sqlite、secrets、env、缓存目录）
- 提交并推送到 GitHub

### Step 6：Streamlit Community Cloud 部署
- 关联 GitHub 仓库，选择分支与入口文件（默认 `app.py`）
- 在 Secrets 中配置：
  - `DB_MODE = "auto"`
  - `POSTGRES_DSN = "..."`
- 点击 Deploy

### Step 7：公网验证与回归
- 公网 URL 手机/PC 均可访问
- 核心验收：
  - 搜索/筛选正常
  - AI 解读：有则展示、无则占位（不报错）
  - 笔记：同一 `user_key` 下手机与 PC 互相可见；不同 `user_key` 不互相覆盖
  - 重启/重新部署后笔记仍在（验证云端持久化）

## Assumptions & Risks
- Phase 3 假设 Phase 2 已产出可运行的 Streamlit 应用与本地 SQLite 数据库；若未完成，需要先补齐 Phase 2 执行。
- 公网暴露后，任何人都能访问应用：采用 `user_key` 仅解决“互不覆盖”，不等同于强安全；若需要强保护可在后续引入口令或 Supabase Auth。
- Streamlit Cloud 的网络与 Supabase 连接方式可能受限（pooler/direct 差异）；计划将把 DSN 配置化并在 README 给出两种 DSN 选项。

## Verification（Done Definition）
- Streamlit Cloud 部署成功并可访问公网 URL
- Postgres 连接通过 secrets 注入，代码中无明文密钥
- 迁移脚本可重复执行且统计一致
- 笔记功能在云端可写入并跨容器重启保持
