# GitHub Security Monitor V4

> 🛡️ 信息安全 GitHub 项目自动监控 + 纯静态 Web 仪表盘  
> 基于 **GitHub Actions** 定时抓取 + **GitHub Pages** 展示，0 服务器 0 费用

## 🏗️ 架构

```
GitHub Actions (定时)          GitHub Pages (展示)
┌──────────────────────┐       ┌─────────────────┐
│ 每天 4 次 + 每周 1 次 │       │ docs/index.html │
│ python run.py --daily │ ──►   │ 纯静态 SPA       │
│ python run.py --trend │       │ fetch JSON 渲染  │
└─────────┬────────────┘       └─────────────────┘
          │ commit & push                    ▲
          ▼                                 │
┌──────────────────────┐                    │
│ data/records.json    │ ───────────────────┘
│ data/executions.json │
│ data/trending.json   │
└──────────────────────┘
```

## ⏱️ 监控计划

| 触发 | Cron | 监控内容 |
|------|------|---------|
| Daily ×4 | `0 2,8,14,20 * * *` UTC | CVE + 关键词(7h) + 用户 + 工具 |
| Weekly | `0 1 * * 1` UTC | 安全类高 Star 飙升项目 |

## 🚀 部署

### 1. Fork 这个仓库
### 2. 设置 GitHub Pages
`Settings → Pages → Source: Deploy from a branch → branch: main, folder: /docs → Save`

### 3. 配置 Secrets
`Settings → Secrets and variables → Actions → New repository secret`

| Secret | Required | Description |
|--------|----------|-------------|
| `GH_TOKEN` | ✅ | GitHub Personal Access Token |
| `DINGDING_WEBHOOK` | ❌ | 钉钉机器人 Webhook |
| `DINGDING_SECRET` | ❌ | 钉钉机器人加签密钥 |
| `FEISHU_WEBHOOK` | ❌ | 飞书机器人 Webhook |

### 4. 手动触发首次运行
`Actions → GitHub Security Monitor → Run workflow`

### 5. 访问仪表盘
`https://你的用户名.github.io/仓库名`

## 📁 项目结构

```
V4/
├── .github/workflows/monitor.yml  # Actions 配置
├── monitor/
│   ├── run.py          # CLI 入口
│   ├── cve.py          # CVE 监控
│   ├── keyword.py      # 关键词评分监控
│   ├── user.py         # 用户仓库监控
│   ├── tool.py         # 工具更新监控
│   ├── trending.py      # 热门飙升监控
│   ├── notify.py       # 钉钉/飞书通知
│   └── config.py       # 配置加载 + JSON 读写
├── config.yaml         # 监控配置 (关键词/用户/工具/阈值)
├── data/
│   ├── records.json    # 监控记录 (Actions 写入)
│   ├── executions.json # 执行历史
│   └── trending.json   # 热门项目
├── docs/
│   └── index.html      # 仪表盘 (纯静态 SPA)
├── requirements.txt    # PyYAML + requests
└── README.md
```

## 🔧 本地运行

```bash
pip install -r requirements.txt

# 每日监控
python monitor/run.py --daily

# 热门飙升
python monitor/run.py --trending

# 单独运行某项
python monitor/run.py --cve --hours 24
python monitor/run.py --keyword --hours 12
```

## 📊 仪表盘功能

- ✅ 统计数据 (总数/今日新增/本周新增/分类)
- ✅ 监控记录 (按类型筛选/搜索/分页)
- ✅ 热门飙升 Top 30
- ✅ 执行历史
- ✅ 配置总览
