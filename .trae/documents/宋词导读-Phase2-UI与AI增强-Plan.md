# 《宋词导读》Phase 2：响应式 UI 与 AI 增强（Plan）

## Summary
- 目标：基于 Streamlit 提供“辞海模式/挑战模式”的响应式阅读 UI，并展示 AI 深度解读与本地生僻字字典查询；实现课堂笔记持久化。
- 数据源：默认使用 `data/songci.sqlite`（用户确认）。
- 解析库与 PDF 处理不在本阶段范围；仅做 UI、读库、写库（笔记）、轻量展示逻辑。
- AI 深度解读采用“混合模式”：有数据即展示，无数据显示占位提示。

## Current State Analysis (Grounded)
- 仓库目前仅包含 PDF 文件 `宋词导读作品选（2026年春）.pdf`，尚无代码与数据库结构文件。
- 因此 Phase 2 计划将创建 Streamlit 应用结构与 SQLite 访问层，并在不破坏 Phase 1 的前提下扩展新表。

## Proposed Changes (Files & What/Why/How)
> 以下文件路径均为本仓库建议新增，实际执行阶段会创建。

### 1) Streamlit 应用入口
- **文件**：`app.py`
- **作用**：主 UI 路由与布局，包含侧边栏与主视图。
- **实现要点**：
  - 侧边栏：模式切换（辞海/挑战）、搜索框、必背筛选、生僻字检索组件。
  - 主视图：垂直沉浸式排版；正文大字；题记、作者简介、注释、附录放入 `st.expander`。
  - AI 深度解读：按钮或 `st.expander` 展示数据库内容或“待生成”。

### 2) 数据访问层
- **文件**：`src/songci_ui/db.py`
- **作用**：统一封装 SQLite 连接与查询。
- **实现要点**：
  - 连接 `data/songci.sqlite`，可通过环境变量或 CLI 参数覆盖。
  - 查询：按标题/作者/正文关键词模糊检索。
  - 判断表存在性（`sqlite_master`），用于 AI 解读混合模式。

### 3) 业务查询与模型
- **文件**：`src/songci_ui/repo.py`
- **作用**：对 UI 友好的仓储层 API。
- **实现要点**：
  - `search_poems(query, required_only)`
  - `get_poem_detail(poem_id)`
  - `get_author_bios(author_id)`（若 Phase1 已有 `author_bios`）
  - `get_ai_insight(poem_id)`（若表存在）
  - `get_note(poem_id)` / `save_note(poem_id, content)`

### 4) 生僻字字典数据与查询
- **文件**：`data/rare_chars.json`
- **作用**：本地字典库，供“生僻字检索”组件读取。
- **结构建议**（JSON）：
  - `{"字": {"pinyin": "...", "definition": "..."}}`
- **策略**：Phase 2 先内置少量样例并提供替换说明；后续可整体替换为更完整的字库。

### 5) 笔记持久化
- **文件**：`data/songci.sqlite`（新增表）
- **表**：`poem_notes`
- **策略**：每首诗一条当前笔记（upsert）。
- **为何**：课堂笔记以“当前版本”为主，不保留历史（可在 Phase 3 扩展版本表）。

## Data Model Additions (SQLite)
> 兼容 Phase 1 现有 `authors / poems / author_bios`。

### 1) 课堂笔记表
```sql
CREATE TABLE IF NOT EXISTS poem_notes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id    INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(poem_id)
);
```

### 2) AI 深度解读表（可选）
> 用于“混合模式”：存在则展示，不存在则 UI 占位。
```sql
CREATE TABLE IF NOT EXISTS ai_insights (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id    INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source     TEXT, -- 生成模型或版本
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(poem_id)
);
```

## UI Layout & Interaction Design
### 1) 侧边栏（Navigation & Search）
- 模式切换：`st.radio`（辞海模式 / 挑战模式）。
- 搜索框：`st.text_input`，支持标题/作者/正文关键词。
- 过滤器：`st.checkbox("仅必背")`。
- 生僻字检索：输入文字后，逐字查本地字库并列表展示拼音/释义。

### 2) 主视窗（Main Stage）
- 纵向沉浸排版：移动端优先，正文使用大字号 CSS。
- 折叠层级：
  - `st.expander("作者简介")`
  - `st.expander("题记/小序")`
  - `st.expander("注释")`
  - `st.expander("附录/赏析")`
  - `st.expander("AI 深度解读")`
- 课堂笔记：正文下方 `st.text_area` + 保存按钮。

### 3) 挑战模式（Phase 2 简化版）
- 本阶段不做算法，提供 UI 占位：
  - “随机抽取必背词”按钮（从必背集中随机挑选）
  - 显示选中词内容，提示“复习算法将在后续阶段启用”。

## FSM/解析模块的依赖关系说明
- Phase 2 只依赖 Phase 1 已构建的 SQLite 数据库。
- 若 Phase 1 尚未执行，可提供 UI 占位提示并允许加载 mock 数据（可选开关）。

## Step-by-Step Implementation Plan
### Step 0：初始化 UI 项目结构
- 新建 `app.py` 与 `src/songci_ui/` 目录。
- 加入 `requirements.txt`：包含 `streamlit`（最新稳定版）与 `sqlite3`（标准库无需安装）。

### Step 1：实现数据库访问层
- 编写 `db.py`：连接、执行查询、表存在检测。
- 编写 `repo.py`：封装 UI 所需查询与保存接口。

### Step 2：实现侧边栏与搜索过滤
- 根据输入构建 SQL `LIKE` 查询，支持标题/作者/正文关键词。
- 勾选“仅必背”时追加 `is_required=1` 条件。
- 搜索结果列表点击后渲染详情。

### Step 3：实现主视窗排版
- 正文大字样式（CSS 注入）。
- 作者简介/题记/注释/附录 使用 `st.expander`。
- AI 深度解读：若 `ai_insights` 存在且有内容，显示；否则显示占位提示。

### Step 4：生僻字检索组件
- 读取 `data/rare_chars.json`。
- 用户输入后逐字解析，展示拼音/释义；无则提示未收录。

### Step 5：课堂笔记持久化
- 初始化 `poem_notes` 表（如果不存在）。
- 加载/保存笔记（`UNIQUE(poem_id)` upsert）。

### Step 6：挑战模式 UI 占位
- 随机抽取必背词（SQL `ORDER BY RANDOM() LIMIT 1`）。
- 显示内容与“复习算法待 Phase 3”提示。

### Step 7：基础验证
- 启动 Streamlit：能正常加载数据库、搜索、筛选、显示详情。
- AI 解读表不存在时 UI 正常运行，不报错。
- 笔记保存后刷新仍能读取。

## Assumptions & Decisions
- 课堂笔记采用“每诗单条、可覆盖”的简化策略。
- 生僻字库采用本地 JSON 文件形式，便于快速替换完整字典。
- 挑战模式仅提供 UI 占位与随机展示，不实现记忆算法。
- 若 `ai_insights` 表不存在或内容为空，显示占位文本，不进行外部 AI 调用。

## Verification
- `app.py` 可在本地运行，侧边栏功能齐全。
- 搜索框可检索标题/作者/正文。
- 必背筛选生效。
- 生僻字检索可输出拼音/释义。
- AI 深度解读在有/无数据时均正确展示。
- 课堂笔记保存后持久化。

