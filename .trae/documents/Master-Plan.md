# 《宋词导读》全集数字辞典：Master Plan（Phase 1–3）

## 0. 总览（项目目标与边界）
- **产品目标**：把《宋词导读》纯文本 PDF 解析为结构化数据库，并提供手机/PC 友好的阅读与检索体验；支持用户在阅读过程中沉淀 UGC 课堂笔记；最终部署到公网可访问。
- **三阶段范围**：
  - **Phase 1**：纯文本 PDF 深度解析 + SQLite 构建（100% 无遗漏提取，强审计）
  - **Phase 2**：Streamlit 响应式 UI + 本地工具（生僻字检索）+ AI 内容展示 + UGC 笔记（本地持久化）
  - **Phase 3**：部署到 Streamlit Community Cloud + Supabase(PostgreSQL) 云端持久化 + SQLite→PG 迁移脚本 + 本地/云端自动切换
- **明确不做**：本阶段不实现背诵算法/复习调度逻辑（但 DB 字段预留 `proficiency/next_review_time`）。

## 1. 全局架构（贯穿 Phase 1–3 的一致性设计）
### 1.1 数据管道（从 PDF 到可用产品）
1) **Extract（抽取）**：pdfplumber 从 PDF 产出“按阅读顺序”的行流（每行带页码/行号/位置）
2) **Parse（解析）**：FSM 逐行扫描，稳定分段（Title → Author → Bio/Headnote → Body → Annotation → Appendix）
3) **Audit（审计）**：每一条抽取行必须入库到 `extracted_lines` 并标注归属；严禁丢弃文本
4) **Store（存储）**：SQLite 作为本地词库主存储；云端用 Postgres（Supabase）承载线上与 UGC
5) **Serve（展示）**：Streamlit UI 读取数据库，提供阅读/检索/筛选/展开层级化内容

### 1.2 关键“不会返工”的决策
- **解析库**：只用 `pdfplumber`
- **注释存储**：`poems.annotations` 为 JSON 数组文本（保序、允许同名多条、支持续行）
- **作者简介**：多版本保留（`author_bios`），用 hash 去重
- **AI 深度解读**：不在线调用外部 AI；仅“展示数据库中已预存的内容”，缺失时占位
- **UGC 课堂笔记**：以 `user_key` 做隔离，避免公网多人互相覆盖；同一用户可在手机/PC 共享
- **环境切换**：本地默认 SQLite，云端默认 Postgres；代码支持 `DB_MODE=auto|sqlite|postgres`

## 2. 数据库设计（SQLite 主库 + 云端 PostgreSQL 对齐）
### 2.1 SQLite Schema（Phase 1 落地，Phase 2/3 复用）
> 下述 DDL 为“最终统一版本”，Phase 2 的笔记表直接采用 `user_key` 形式，保证 Phase 3 云端一致。

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS authors (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at    INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS author_bios (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id     INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
  bio           TEXT NOT NULL,
  source_pdf    TEXT NOT NULL,
  source_page   INTEGER,
  source_order  INTEGER,
  bio_hash      TEXT NOT NULL,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(author_id, bio_hash)
);

CREATE TABLE IF NOT EXISTS poems (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  seq_no           INTEGER,
  title            TEXT NOT NULL,
  author_id        INTEGER NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,
  headnote         TEXT,
  content          TEXT NOT NULL,
  annotations      TEXT,
  appendix         TEXT,
  is_required      INTEGER NOT NULL DEFAULT 0,
  proficiency      INTEGER NOT NULL DEFAULT 0,
  next_review_time INTEGER,
  source_pdf       TEXT NOT NULL,
  source_page_start INTEGER,
  source_page_end   INTEGER,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(source_pdf, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_poems_author_id ON poems(author_id);
CREATE INDEX IF NOT EXISTS idx_poems_required ON poems(is_required);
CREATE INDEX IF NOT EXISTS idx_poems_next_review_time ON poems(next_review_time);

CREATE TABLE IF NOT EXISTS extracted_lines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source_pdf   TEXT NOT NULL,
  page_no      INTEGER NOT NULL,
  line_no      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  role         TEXT NOT NULL,
  poem_id      INTEGER REFERENCES poems(id) ON DELETE SET NULL,
  author_id    INTEGER REFERENCES authors(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(source_pdf, page_no, line_no)
);

CREATE INDEX IF NOT EXISTS idx_lines_poem_role ON extracted_lines(poem_id, role);
CREATE INDEX IF NOT EXISTS idx_lines_author_role ON extracted_lines(author_id, role);

CREATE TABLE IF NOT EXISTS ai_insights (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id    INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source     TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(poem_id)
);

CREATE TABLE IF NOT EXISTS poem_notes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id    INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  user_key   TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(poem_id, user_key)
);

CREATE INDEX IF NOT EXISTS idx_poem_notes_user_key ON poem_notes(user_key);
```

### 2.2 注释 JSON（统一结构）
```json
[
  {
    "raw": "[词语]: 解释第一行",
    "term": "词语",
    "sep": ":",
    "definition": "解释第一行",
    "continuations": ["解释续行1"],
    "page_no": 12
  }
]
```

### 2.3 PostgreSQL Schema（Supabase，Phase 3）
> 云端表结构与 SQLite 字段意义保持一致；`annotations` 仍存 `TEXT`（JSON 文本），降低迁移复杂度。

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

CREATE TABLE IF NOT EXISTS ai_insights (
  id         BIGSERIAL PRIMARY KEY,
  poem_id    BIGINT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source     TEXT,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(poem_id)
);

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

## 3. Phase 1：核心数据解析与存储（PDF → SQLite）
### 3.1 提取目标（必须全覆盖）
- 标题与必背标记：如 `1. 望江南` / `*22. 苏幕遮`（`*` → `is_required=1`）
- 作者：紧跟标题行之后
- 作者简介：首次出现某作者时紧随其名出现的生平介绍（入 `author_bios`，可多版本）
- 题记/小序：作者之后、正文之前
- 正文：词作主体
- 注释：正文后，以 `[` 开头的行（支持续行）
- 附录/赏析：注释后，以 `【附录】` 开头的段落

### 3.2 FSM（有限状态机）状态与流转（核心保证“不丢行”）
状态集合：
- `S_SEEK_TITLE` → 等标题
- `S_EXPECT_AUTHOR` → 标题后等作者
- `S_MAYBE_BIO_OR_HEADNOTE_OR_BODY` → 作者后判定简介/题记/正文
- `S_HEADNOTE` → 题记/小序
- `S_BODY` → 正文
- `S_ANNOTATION` → 注释
- `S_APPENDIX` → 附录/赏析

行分类规则（优先级）：
1. 标题：`^\s*(\*)?\s*(\d+)\.\s*(.+?)\s*$`
2. 注释起始：行首 `[`（忽略前导空白）
3. 附录起始：行首 `【附录】`
4. 空行：trim 后为空
5. 诗体启发式：短行 + 中文标点特征（阈值可配置）
6. 其余视作散文（简介/题记/附录续行等）

状态流转图（文本版）：
```
S_SEEK_TITLE
  └─(TITLE)→ S_EXPECT_AUTHOR

S_EXPECT_AUTHOR
  ├─(blank)→ S_EXPECT_AUTHOR
  └─(author)→ S_MAYBE_BIO_OR_HEADNOTE_OR_BODY

S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  ├─(ANNOTATION_START)→ S_ANNOTATION
  ├─(APPENDIX_START)→ S_APPENDIX
  ├─(looks_like_poetry)→ S_BODY
  └─(prose)→ if 作者首次且简介未收集 → BIO else → S_HEADNOTE

S_HEADNOTE
  ├─(looks_like_poetry)→ S_BODY
  ├─(ANNOTATION_START)→ S_ANNOTATION
  ├─(APPENDIX_START)→ S_APPENDIX
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  └─(else) stay

S_BODY
  ├─(ANNOTATION_START)→ S_ANNOTATION
  ├─(APPENDIX_START)→ S_APPENDIX
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  └─(else) stay

S_ANNOTATION
  ├─(ANNOTATION_START)→ new item
  ├─(APPENDIX_START)→ S_APPENDIX
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  └─(else) continuation

S_APPENDIX
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  └─(else) stay
```

### 3.3 “100% 无遗漏”验收标准（硬指标）
- `extracted_lines` 覆盖：所有抽取行均写入；构建结束 `role='unassigned'` 行数必须为 0
- `poems` 完整性：`title/author_id/content` 必须非空
- `annotations`：非空时必须为合法 JSON 数组
- 可追溯：每首 poem 在 `extracted_lines` 至少能定位到 title/author/body 对应行

## 4. Phase 2：Streamlit UI + AI 展示 + 本地工具 + UGC
### 4.1 UI 核心布局（移动端优先）
- **侧边栏**：
  - 模式切换：`st.radio`（辞海模式 / 挑战模式）
  - 搜索：`st.text_input`（标题/作者/正文关键词）
  - 过滤：`st.checkbox("仅必背")`
  - `user_key`：`st.text_input`（用于笔记隔离；同 key 在多端共享）
  - 生僻字检索：输入文字→逐字查本地字典→展示拼音/释义
- **主视窗**：
  - 正文大字、纵向沉浸排版
  - 题记/作者简介/注释/附录/AI 解读 全部使用 `st.expander`，首屏只突出正文
  - 课堂笔记：正文下方 `st.text_area` + 保存按钮（写入 `poem_notes`）

### 4.2 AI 深度解读（混合模式）
- 若存在 `ai_insights` 且当前 poem 有记录 → 展示
- 否则 → 显示占位文本（不发起外部 AI 调用）

### 4.3 挑战模式（Phase 2 仅 UI 占位）
- 不做背诵算法
- 可提供“随机抽取必背词”按钮（用于演示与 UI 验证）

## 5. Phase 3：公网部署 + 云端持久化（Supabase + Streamlit Cloud）
### 5.1 部署目标
- GitHub 托管代码
- Streamlit Community Cloud 自动部署，生成公网 URL
- Supabase(Postgres) 承载线上读写（特别是 UGC 笔记），容器重启不丢数据

### 5.2 数据迁移与双环境切换
- 迁移脚本：`scripts/migrate_to_cloud.py`
  - 从 `data/songci.sqlite` 读取数据
  - 在 Postgres 创建同构表并写入数据
  - 通过 `ON CONFLICT` 支持可重复迁移（幂等）
  - 可选 `--skip-audit` 跳过 `extracted_lines` 大表（若需要）
- DB 自动切换：
  - `DB_MODE=auto` 且 `st.secrets` 提供 `POSTGRES_DSN` → 使用 Postgres
  - 否则回退 SQLite（本地开发体验不变）

### 5.3 Secrets 与安全
- `.streamlit/secrets.toml` 本地使用但必须在 `.gitignore` 中
- 云端在 Streamlit Cloud 面板配置 Secrets：
  - `DB_MODE="auto"`
  - `POSTGRES_DSN="..."`（严禁写进代码或提交）

### 5.4 公网验收（重点）
- 手机/PC 均可访问同一公网 URL
- 搜索/筛选正常
- AI 解读：有则展示、无则占位（不报错）
- 笔记：同一 `user_key` 多端同步；不同 `user_key` 不互相覆盖
- 重启/重新部署后笔记仍存在（验证云端持久化）

## 6. 代码与目录建议（执行阶段按实际落地）
- `src/songci_dict/`：Phase 1 解析与 SQLite 构建
- `scripts/build_db.py`：Phase 1 构建入口
- `schema.sql`：SQLite DDL（或内置在 db 初始化）
- `app.py`：Phase 2 Streamlit 入口
- `src/songci_ui/`：UI 查询与 DB 访问封装
- `scripts/migrate_to_cloud.py`：Phase 3 迁移脚本
- `data/rare_chars.json`：本地生僻字字典
- `data/songci.sqlite`：本地数据库（必须 gitignore）

## 7. 整体里程碑（按阶段验收）
### Phase 1 Done
- 从 PDF 构建出 `data/songci.sqlite`
- `extracted_lines.role='unassigned'` = 0
- poems/title/author/content 均完整且可追溯

### Phase 2 Done
- 本地 Streamlit 启动成功：辞海模式可检索与阅读
- expander 层级化内容显示正常
- 生僻字检索可用
- 笔记可写入并刷新后仍存在（本地 SQLite）

### Phase 3 Done
- GitHub 托管 + Streamlit Cloud 部署成功
- Supabase Postgres 写入笔记成功且可跨重启保持
- 本地/云端 DB 自动切换生效（同一代码库）

