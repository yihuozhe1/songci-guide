# 宋词导读数字辞典

Streamlit 应用入口是 `app.py`。本地默认使用 `data/songci.sqlite`，云端推荐使用 Supabase PostgreSQL，并通过 `DB_MODE` / `POSTGRES_DSN` 自动切换。

## 本地运行

```powershell
.\run_app.ps1
```

也可以手动运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## 云端部署方案

目标平台：

- 应用托管：Streamlit Community Cloud
- 云端数据库：Supabase PostgreSQL
- 代码托管：GitHub

不要把 `.streamlit/secrets.toml`、`data/songci.sqlite`、PDF 原文件提交到 GitHub。它们已经在 `.gitignore` 中排除。云端数据通过迁移脚本写入 Supabase。

## 1. 准备 Supabase 数据库

1. 在 Supabase 创建项目。
2. 打开项目的 **Connect** 页面，复制 PostgreSQL 连接串。
3. 推荐使用 **Session pooler** 连接串，通常端口是 `5432`，并带有 `pooler.supabase.com`。
4. 连接串建议追加 `?sslmode=require`。

示例格式：

```text
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

## 2. 迁移本地 SQLite 到 Supabase

方式一：临时环境变量。

```powershell
$env:POSTGRES_DSN="postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
.\.venv\Scripts\python.exe scripts\migrate_to_cloud.py
```

方式二：把真实连接串写入本机 `.streamlit/secrets.toml`，然后运行：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_to_cloud.py
```

迁移脚本会自动创建表并输出 SQLite 与 PostgreSQL 的核心表计数。重复执行是幂等的。

## 3. 推送到 GitHub

如果还没有初始化 Git：

```powershell
git init
git add .
git commit -m "Prepare Streamlit cloud deployment"
git branch -M main
git remote add origin https://github.com/<your-user>/<your-repo>.git
git push -u origin main
```

推送前确认这些文件不会进入仓库：

```powershell
git status --ignored
```

## 4. Streamlit Community Cloud 部署

1. 打开 Streamlit Community Cloud，选择 GitHub 仓库。
2. Branch 选择 `main`，Main file path 填 `app.py`。
3. Advanced settings 中 Python version 选择 `3.12`。
4. Secrets 填入以下内容，真实值来自 Supabase 和本机配置：

```toml
DB_MODE = "auto"
POSTGRES_DSN = "postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
PUBLIC_APP_URL = "https://<your-app>.streamlit.app"

# Optional
ZHIPU_API_KEY = ""
ZHIPU_MODEL = "glm-5.1"
```

5. 点击 Deploy，部署完成后会得到 `https://<subdomain>.streamlit.app/` 公网链接。

## 验收清单

- 公网 URL 在电脑和手机都能访问。
- 搜索、筛选、词作切换正常。
- `DB` 状态显示为 `postgres`。
- 同一个 `user_key` 在不同设备上能看到同一份课堂笔记。
- 不同 `user_key` 的笔记互不覆盖。
- 重新部署或重启后，笔记仍然存在。
