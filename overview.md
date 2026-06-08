# V4 本地测试结果

## 测试数据

| 数据文件 | 内容 | 大小 |
|---------|------|------|
| records.json | 229 条监控记录 | ~130KB |
| executions.json | 2 次执行记录 | ~300B |
| trending.json | 30 个热门项目 | ~12KB |

## 记录分布

| 类型 | 数量 |
|------|------|
| CVE | 20 |
| Keyword | 202 |
| Tool | 7 |

## 热门 Top 5

1. sherlock-project/sherlock - 84,712 ⭐
2. swisskyrepo/PayloadsAllTheThings - 78,256 ⭐
3. NationalSecurityAgency/ghidra - 69,345 ⭐
4. x64dbg/x64dbg - 48,593 ⭐
5. KeygraphHQ/shannon - 44,363 ⭐

## 执行耗时

| 模式 | 耗时 | 新增 |
|------|------|------|
| --cve --hours 24 | 5.1s | 20 |
| --daily --hours 24 | 447.9s | 209 |

- 本地关键词监控较慢（106 关键词，每词 1 个 API 调用）
- GitHub Actions 环境下速度类似，但由 cron 自动触发

## 文件结构验证

- ✅ config.yaml 加载正常
- ✅ CVE/Keyword/User/Tool/Trending 模块正常
- ✅ JSON 数据读写正常
- ✅ 执行历史记录正常
- ✅ docs/index.html 仪表盘可用
