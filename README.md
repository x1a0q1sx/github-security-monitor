# GitHub Security Monitor V5

> 🛡️ 信息安全 GitHub 项目自动监控 + Agent Skill 发现推荐 + 纯静态 Web 仪表盘
> 基于 **GitHub Actions** 定时抓取 + **GitHub Pages** 展示，0 服务器 0 费用

## V5 相对 V4 的变化

| 问题 (V4) | V5 方案 |
|-----------|---------|
| `c2`/`rce`/`0day` 子串误报爆炸 | 关键词分层 **S/A/B**，A 级禁止单独过线 + 假友词规则 |
| `rules.skip_*` 配置不生效 | 统一 `Scorer` + `filters` 质量门 |
| Tool 见过一次永不再报 | 持久化 `tool_state.json`，检测 `pushed_at` / release |
| User 监控几乎为 0 | 窗口扩到 7 天，兼顾新建仓 |
| 批量 OR 搜索饿死长尾词 | **S 词逐个单搜**；A/B 保留上下文 batch；Search 独立限速 ~2.2s/次 |
| 历史脏数据 | `--migrate-only` 重算分数并归档噪声 |
| 无能力发现 | 新增 **Skill 发现推荐**（SkillHub + GitHub + Seed） |

默认门槛（B 平衡档）：**`final_score >= 6.0`**

```
final = relevance * 0.55 + quality * 0.45
```

## 架构

```
GitHub Actions                     GitHub Pages
┌──────────────────────────┐       ┌──────────────────┐
│ daily / skills / trending│       │ docs/index.html  │
│ python -m monitor.run …  │ ──►   │ 静态 SPA         │
└───────────┬──────────────┘       └─────────▲────────┘
            │ commit JSON                     │
            ▼                                 │
┌──────────────────────────┐                  │
│ data/records.json        │ ─────────────────┘
│ data/skills.json         │
│ data/trending.json       │
│ data/archive/noise-*.json│
└──────────────────────────┘
```

## 目录

```
config/
  config.yaml          # 主配置
  keywords.yaml        # S/A/B 关键词 + 搜索查询
  noise_rules.yaml     # 误报/噪声规则
  skills.yaml          # skill 种子与分类
monitor/
  run.py               # CLI
  scoring.py / filters.py / github_client.py / storage.py
  sources/             # cve keyword user tool trending skills
scripts/migrate_v5.py  # 历史重算归档
docs/index.html        # 仪表盘（含 Skills 页）
data/                  # Actions 写入
```

## 部署

1. Fork 本仓库
2. Pages：`Settings → Pages → Deploy from branch → main /docs`
3. Secrets：

| Secret | 必需 | 说明 |
|--------|------|------|
| `GH_TOKEN` | ✅ | GitHub PAT（search API） |
| `DINGDING_WEBHOOK` | ❌ | 钉钉 |
| `DINGDING_SECRET` | ❌ | 钉钉加签 |
| `FEISHU_WEBHOOK` | ❌ | 飞书 |

4. Actions 手动跑一次：
   - `migrate`：历史重算（首次升级强烈建议）
   - `daily` / `skills` / `all`

5. 打开 `https://<user>.github.io/<repo>`

## 本地运行

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=ghp_xxx   # 或 GH_TOKEN

# 历史数据重算 + 噪声归档（首次）
python -m monitor.run --migrate-only --publish

# 每日监控
python -m monitor.run --daily --publish

# Skill 发现
python -m monitor.run --skills --publish

# 热门
python -m monitor.run --trending --publish
```

## 监控计划

| 触发 | Cron (UTC) | 内容 |
|------|------------|------|
| Daily ×4 | `0 2,8,14,20 * * *` | CVE + Keyword + User + Tool |
| Skills ×2 | `0 3,15 * * *` | Skill 发现推荐 |
| Weekly | `0 1 * * 1` | 高 Star 安全目录 |

## Skill 推荐模块

- 源：SkillHub 搜索 API、GitHub 仓库搜索、本地 seed
- 全量收录 + **安全向加权**（`prefer_security_weight`）
- 输出 `data/skills.json`，仪表盘 **Skill 推荐** 页可复制安装命令

## 评分字段（records）

每条记录新增：

- `final_score` / `relevance_score` / `quality_score`
- `confidence`: `high` | `medium` | `low` | `noise`
- `reasons[]` / `matched_keywords[]`

噪声与低分历史项写入 `data/archive/noise-*.json`，不再进入主仪表盘。

## 兼容说明

- 旧根目录 `config.yaml` 仍可作为 V4 回退参考；**V5 以 `config/` 为准**
- CLI 入口：`python -m monitor.run`（兼容 `python monitor/run.py`）
