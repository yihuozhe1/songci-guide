# 《宋词导读》全集数字辞典：核心数据解析与存储模块（Phase 1）开发 Plan

## Summary
- 目标：从纯文本 PDF（当前仓库文件：`宋词导读作品选（2026年春）.pdf`）中 100% 无遗漏提取结构化数据，并构建 SQLite 数据库。
- 本阶段范围：仅“解析 + 入库 + 校验/审计”。不做前端 UI、不做背诵/复习算法（但会在表结构中预留 `proficiency` / `next_review_time` 字段）。
- 解析库决策：只用 `pdfplumber`（按用户选择），以有限状态机（FSM）逐行扫描方式解析。
- 核心原则：任何 PDF 抽取出的文本行都必须被“归属”到某一条记录（poem/author/bio/注释/附录/杂项审计），严禁丢行、串位。

## Current State Analysis (Grounded)
- 仓库当前仅包含 1 个数据源 PDF：`e:\生成式AI\宋词导读\宋词导读作品选（2026年春）.pdf`。
- 目前没有现成代码、数据库或项目结构，需要从零初始化 Python 模块与 SQLite schema。

## Data Model / SQLite Schema（重点）
### 设计目标
- 可追溯：能从任意入库字段回溯到 PDF 页码与原始行文本，便于定位解析误差。
- 可扩展：后续可增加多 PDF、版本、前端检索、背诵算法而不推翻核心表。
- 不丢信息：注释用 JSON 数组（用户选择）；作者简介需要“多版本保留”（用户选择）。

### Schema DDL（建议作为 `schema.sql` 落地）
```sql
PRAGMA foreign_keys = ON;

-- 作者表：作者实体（不把简介直接放这里，便于多版本）
CREATE TABLE IF NOT EXISTS authors (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at    INTEGER NOT NULL DEFAULT (unixepoch())
);

-- 作者简介表：同一作者可多版本保留（来源不同、重复出现补充等）
CREATE TABLE IF NOT EXISTS author_bios (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id     INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
  bio           TEXT NOT NULL,
  -- 用于对齐“首次出现”的位置、以及不同版本对比
  source_pdf    TEXT NOT NULL,
  source_page   INTEGER,
  source_order  INTEGER,
  -- 简单去重：同一作者同一 bio 不重复插入
  bio_hash      TEXT NOT NULL,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(author_id, bio_hash)
);

-- 词作表：核心内容（字段按要求预留）
CREATE TABLE IF NOT EXISTS poems (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  -- PDF 中的序号：如 "22."；允许后续多 PDF 时用 (source_pdf, seq_no) 唯一
  seq_no           INTEGER,
  title            TEXT NOT NULL,
  author_id        INTEGER NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,
  headnote         TEXT,            -- 题记/小序
  content          TEXT NOT NULL,    -- 词作正文（按行合并，保留换行）
  annotations      TEXT,            -- JSON 数组文本（用户选择）
  appendix         TEXT,            -- 【附录】段落
  is_required      INTEGER NOT NULL DEFAULT 0, -- 带*的必背标记（0/1）
  proficiency      INTEGER NOT NULL DEFAULT 0,
  next_review_time INTEGER,          -- unix timestamp (秒)
  source_pdf       TEXT NOT NULL,
  source_page_start INTEGER,
  source_page_end   INTEGER,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  -- 防止同一 PDF 重复导入同一词
  UNIQUE(source_pdf, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_poems_author_id ON poems(author_id);
CREATE INDEX IF NOT EXISTS idx_poems_required ON poems(is_required);
CREATE INDEX IF NOT EXISTS idx_poems_next_review_time ON poems(next_review_time);

-- 审计表：保证“无遗漏”的关键。每一条抽取出的“行”都必须入此表并标注归属。
-- 这样即使解析策略变更，也能复核：是否存在 role='unassigned' 或跨 poem 串位。
CREATE TABLE IF NOT EXISTS extracted_lines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source_pdf   TEXT NOT NULL,
  page_no      INTEGER NOT NULL,
  line_no      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  -- 解析归属：title/author/bio/headnote/body/annotation/appendix/whitespace/noise/unassigned
  role         TEXT NOT NULL,
  poem_id      INTEGER REFERENCES poems(id) ON DELETE SET NULL,
  author_id    INTEGER REFERENCES authors(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(source_pdf, page_no, line_no)
);

CREATE INDEX IF NOT EXISTS idx_lines_poem_role ON extracted_lines(poem_id, role);
CREATE INDEX IF NOT EXISTS idx_lines_author_role ON extracted_lines(author_id, role);

-- 可选：解析运行记录（便于多次构建、差异对比、回归）
CREATE TABLE IF NOT EXISTS parse_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source_pdf   TEXT NOT NULL,
  parser_ver   TEXT NOT NULL,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch())
);
```

### 注释 JSON 数组建议结构
- 字段：`poems.annotations` 存 JSON 文本，结构为数组（保序、允许同名多条、利于续行合并）。
```json
[
  {
    "raw": "[词语]: 解释第一行",
    "term": "词语",
    "sep": ":",
    "definition": "解释第一行",
    "continuations": ["解释续行1", "解释续行2"],
    "page_no": 12
  }
]
```
- 最保真策略：永远保留 `raw`；结构化字段（term/definition/continuations）尽量填充，填不出来也不影响“无遗漏”。

## Parsing Architecture（pdfplumber + FSM）
### 1) PDF 文本抽取层（Line Stream）
目标：产出稳定的“按阅读顺序排序”的行流 `List[Line]`，每行包含页码、行号、文本。

建议实现要点（pdfplumber）：
- 每页用 `page.extract_words(...)` 抽取词块，按 `(top, x0)` 排序后再聚合为行，避免 `extract_text()` 在复杂版面时串行。
- 行聚合：用 `top`（y 轴）做聚类（阈值 `y_tolerance`），同一行内按 `x0` 拼接 words；拼接时根据 word 间距补空格（或直接无空格，按中文场景可不插）。
- 输出 `Line(page_no, line_no, text, top, x0, x1)`；并保存原始 words 以便调试（可选）。

产出规范：
- 不删除任何字符；只做去掉行首行尾空白（可配置）。
- 保留空行：以 `role='whitespace'` 入审计表（或统一过滤但必须记审计）。

### 2) FSM 解析层（逐行扫描 + 强审计）
#### 状态集合（建议）
- `S_SEEK_TITLE`：等待词条标题行（如 `1. 望江南` 或 `*22. 苏幕遮`）。
- `S_EXPECT_AUTHOR`：刚读到标题，等待作者行（紧跟标题后）。
- `S_MAYBE_BIO_OR_HEADNOTE_OR_BODY`：读到作者后，决定后续是作者简介 / 题记 / 正文。
- `S_HEADNOTE`：题记/小序（作者之后、正文之前的散文/背景交代）。
- `S_BODY`：词作正文（核心内容）。
- `S_ANNOTATION`：注释区（以 `[` 开头的行；支持续行）。
- `S_APPENDIX`：附录/赏析区（以 `【附录】` 开头的段落）。

#### 关键判定规则（初版可落地，后续可迭代）
行分类器（按优先级，从上到下匹配）：
1. `is_title(line)`：
   - Regex：`^\s*(\*)?\s*(\d+)\.\s*(.+?)\s*$`
   - `*` -> `is_required=1`
   - `seq_no` -> int
   - `title` -> group(3)
2. `is_annotation_start(line)`：`line.lstrip().startswith('[')`
3. `is_appendix_start(line)`：`line.lstrip().startswith('【附录】')`
4. `is_blank(line)`：去掉空白后为空
5. `looks_like_poetry(line)`（用于判定正文开始/持续）：
   - 长度较短（例如 <= 28 或 32，阈值可配置）
   - 含较高比例的中文标点（`，。？！；`）或顿号 `、`
   - 不是以 `[` / `【` 开头
6. `looks_like_prose(line)`：其余视作散文类（作者简介/题记/附录续行等）

注意：
- “100% 无遗漏”的落点不是一次就完美分类，而是：任何无法判定的行必须进入 `extracted_lines` 且 role 不为丢弃；必要时标记 `role='unassigned'` 并在构建结束时报错/输出报告，强制回归修正。

#### 状态流转逻辑（文本图）
```
S_SEEK_TITLE
  └─(TITLE)→ S_EXPECT_AUTHOR

S_EXPECT_AUTHOR
  ├─(blank)→ S_EXPECT_AUTHOR
  └─(nonblank as AUTHOR)→ S_MAYBE_BIO_OR_HEADNOTE_OR_BODY

S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
  ├─(TITLE)→ flush 当前 poem(允许 content 为空则报错) → S_EXPECT_AUTHOR
  ├─(APPENDIX_START)→ S_APPENDIX
  ├─(ANNOTATION_START)→ S_ANNOTATION
  ├─(looks_like_poetry)→ S_BODY
  └─(else prose)→
       if author 首次出现且 bio 未收集完成 → collect BIO (仍在本状态或子状态)
       else → S_HEADNOTE

S_HEADNOTE
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  ├─(ANNOTATION_START)→ S_ANNOTATION
  ├─(APPENDIX_START)→ S_APPENDIX
  ├─(looks_like_poetry)→ S_BODY
  └─(else) stay S_HEADNOTE (追加 headnote)

S_BODY
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  ├─(ANNOTATION_START)→ S_ANNOTATION
  ├─(APPENDIX_START)→ S_APPENDIX
  └─(else) stay S_BODY (追加 content，保留换行)

S_ANNOTATION
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  ├─(APPENDIX_START)→ S_APPENDIX
  ├─(ANNOTATION_START)→ start new annotation item
  ├─(blank)→ stay S_ANNOTATION (可记录为空白审计)
  └─(else)→ treat as continuation of last annotation item

S_APPENDIX
  ├─(TITLE)→ flush poem → S_EXPECT_AUTHOR
  └─(else) stay S_APPENDIX (追加 appendix)
```

#### FSM 伪代码（可直接转实现）
```python
state = S_SEEK_TITLE
current = None  # 当前 poem accumulator
current_author = None
seen_authors = set()

for line in lines:
    cls = classify(line.text)
    audit(line, role="whitespace" if cls.blank else "unassigned")  # 先审计，后覆盖

    if cls.title:
        if current:
            finalize_and_persist(current)
        current = new_poem(seq_no=cls.seq_no, title=cls.title, is_required=cls.is_required)
        audit(line, role="title", poem=current)
        state = S_EXPECT_AUTHOR
        continue

    if state == S_SEEK_TITLE:
        audit(line, role="noise")  # 标题前的页眉页脚等
        continue

    if state == S_EXPECT_AUTHOR:
        if cls.blank:
            audit(line, role="whitespace", poem=current)
            continue
        current_author = upsert_author(cls.raw_text=line.text)
        current.author_id = current_author.id
        audit(line, role="author", poem=current, author=current_author)
        state = S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
        continue

    if state == S_MAYBE_BIO_OR_HEADNOTE_OR_BODY:
        if cls.annotation_start:
            start_annotations(current)
            audit(line, role="annotation", poem=current)
            push_annotation(current, line.text)
            state = S_ANNOTATION
            continue
        if cls.appendix_start:
            audit(line, role="appendix", poem=current)
            append_appendix(current, line.text)
            state = S_APPENDIX
            continue
        if cls.looks_like_poetry:
            audit(line, role="body", poem=current)
            append_body(current, line.text)
            state = S_BODY
            continue
        # prose
        if current_author.name not in seen_authors and not current_author.bio_collected:
            audit(line, role="bio", author=current_author)
            collect_bio(current_author, line.text)
            continue
        audit(line, role="headnote", poem=current)
        append_headnote(current, line.text)
        state = S_HEADNOTE
        continue

    if state == S_HEADNOTE:
        if cls.annotation_start:
            audit(line, role="annotation", poem=current)
            push_annotation(current, line.text)
            state = S_ANNOTATION
            continue
        if cls.appendix_start:
            audit(line, role="appendix", poem=current)
            append_appendix(current, line.text)
            state = S_APPENDIX
            continue
        if cls.looks_like_poetry:
            audit(line, role="body", poem=current)
            append_body(current, line.text)
            state = S_BODY
            continue
        audit(line, role="headnote", poem=current)
        append_headnote(current, line.text)
        continue

    if state == S_BODY:
        if cls.annotation_start:
            audit(line, role="annotation", poem=current)
            push_annotation(current, line.text)
            state = S_ANNOTATION
            continue
        if cls.appendix_start:
            audit(line, role="appendix", poem=current)
            append_appendix(current, line.text)
            state = S_APPENDIX
            continue
        audit(line, role="body", poem=current)
        append_body(current, line.text)
        continue

    if state == S_ANNOTATION:
        if cls.appendix_start:
            audit(line, role="appendix", poem=current)
            append_appendix(current, line.text)
            state = S_APPENDIX
            continue
        if cls.annotation_start:
            audit(line, role="annotation", poem=current)
            push_annotation(current, line.text)
            continue
        if cls.blank:
            audit(line, role="whitespace", poem=current)
            continue
        audit(line, role="annotation", poem=current)
        append_annotation_continuation(current, line.text)
        continue

    if state == S_APPENDIX:
        audit(line, role="appendix", poem=current)
        append_appendix(current, line.text)
        continue
```

## Step-by-Step Implementation Plan（可执行步骤）
### Step 0：项目初始化（Python）
- 新建 Python 包结构（建议 `src/` 布局），并提供可重复运行的构建入口：
  - `src/songci_dict/`：核心模块
  - `scripts/build_db.py`：命令行入口（输入 PDF，输出 sqlite）
  - `schema.sql`：DDL
  - `requirements.txt`：至少包含 `pdfplumber`（以及其依赖）
- 约定所有路径均支持 Windows，并以仓库根目录为相对基准。

### Step 1：实现 PDF 抽取为“行流”
- 文件：`src/songci_dict/pdf_extract.py`
- 输出：`List[Line]` 或迭代器 `Iterator[Line]`
- 支持参数化：`y_tolerance`、是否保留空行、页范围（便于调试前几页）
- 增加“抽取自检”：每页抽取行数、空行比例、最长行长度等，输出到构建日志（不写入数据库）。

### Step 2：实现 SQLite 初始化与 DAO
- 文件：`src/songci_dict/db.py`
- 能力：
  - 创建/重建数据库（可选 `--overwrite`）
  - 应用 `schema.sql`
  - 提供 `upsert_author(name)`、`insert_author_bio(...)`、`insert_poem(...)`、`insert_extracted_line(...)`
- 注意：用事务批量写入，避免逐行 commit 性能问题。

### Step 3：实现 FSM 解析器（核心）
- 文件：`src/songci_dict/parser_fsm.py`
- 组件拆分建议：
  - `class LineClassifier`：封装 regex/阈值与可调参
  - `class PoemAccumulator`：临时聚合 title/author/headnote/body/annotations/appendix + source 页范围
  - `class FsmParser`：状态机执行器（只消费行流并产出结构化事件）
- 解析输出策略：
  - 先写 `extracted_lines` 审计表（每行必写）
  - 再在 poem finalize 时写 `poems`（并回填 `poem_id` 到对应审计行，可两遍或延迟更新）

### Step 4：作者简介多版本策略落地
- 文件：`src/songci_dict/author_bio.py`（或与 parser 合并）
- 规则：
  - 当 FSM 判定进入 bio 收集时，将连续的 bio 行合并成一个 bio 文本块
  - 计算 `bio_hash`（如 `sha256(normalized_bio)`）
  - 写入 `author_bios`，如果 `(author_id, bio_hash)` 已存在则跳过（去重）

### Step 5：构建入口（CLI）
- 文件：`scripts/build_db.py`
- 参数建议：
  - `--pdf path/to.pdf`
  - `--out data/songci.sqlite`
  - `--overwrite`
  - `--pages 1:20`（调试用）
  - `--dump-lines out.jsonl`（可选，把行流与分类结果输出，便于手工核对）
- 构建流程：extract → parse(FSM) → validate → 写入 sqlite → 输出统计报告。

### Step 6：无遗漏校验（Acceptance Criteria）
“无遗漏”需要可验证的客观标准，建议实现以下校验并作为构建失败条件：
- 审计覆盖：`extracted_lines` 中 `role='unassigned'` 的行数必须为 0（或允许极少并输出明细，默认严格为 0）。
- 词条完整性：
  - 每首 `poems.content` 非空
  - `title/author_id` 非空
- 结构分段一致性：
  - `annotations` 必须为可解析 JSON 数组（若非空）
- 可追溯性：
  - 每首 poem 至少能在 `extracted_lines` 中找到 title/author/body 对应行
- 统计输出：
  - poem 总数、必背数、作者数、作者简介版本数、注释条目数、附录出现次数

### Step 7：测试策略（不依赖 UI）
- 单元测试（pytest）：
  - `test_title_regex`：标题行识别（含带*、空格变体）
  - `test_annotation_continuation`：注释续行拼接
  - `test_fsm_transitions`：用人工构造的行流覆盖各状态转移
- 集成测试：
  - 选取 PDF 的前 N 页（如 1-5 页）跑构建，断言无 unassigned 且 poems>0（具体断言值在首次跑通后固化）。

## Assumptions & Decisions
- 数据源目前为单一 PDF，但 schema 通过 `source_pdf` 字段支持后续多 PDF。
- 不依赖版式字体信息来区分段落（因为“纯文本 PDF”），优先用行首特征与启发式长度/标点判定；若后续发现需要坐标特征，则在 `Line` 中已经预留 `top/x0/...` 以升级规则。
- “100% 无遗漏”以“审计表全覆盖 + unassigned=0”为硬指标；任何无法分类的文本必须可定位并阻断构建，驱动规则迭代直至全覆盖。

## Verification (How we know it’s done)
- 能从该 PDF 构建出 sqlite 文件，且：
  - `SELECT COUNT(*) FROM poems;` > 0
  - `SELECT COUNT(*) FROM extracted_lines WHERE role='unassigned';` = 0
  - `SELECT COUNT(*) FROM poems WHERE content IS NULL OR trim(content)='';` = 0
  - `annotations` 非空时均为合法 JSON 数组
- 随机抽样若干首词：核对 title/author/headnote/body/annotations/appendix 分段无串位。

