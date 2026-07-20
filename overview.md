# V5 本地验证

## 迁移结果（2026-07-18）

| 指标 | 数值 |
|------|------|
| 历史总数 | 3973 |
| 保留 (final≥6) | **169 (4.25%)** |
| 噪声归档 | 3804 |
| high / medium | 18 / 151 |

主数据：`data/records.json`
原始备份：`data/archive/pre-v5-*-records.json`
噪声：`data/archive/noise-*.json`

## Skill 发现

| 指标 | 数值 |
|------|------|
| 采集 | 139 |
| 去重 | 138 |
| 保留 | 42 |
| 安全向 | 31 |

输出：`data/skills.json`

## 评分冒烟

| 样本 | 结果 |
|------|------|
| C2RoPE（ML 假友） | noise / 低分 |
| 0day personal readme | hard drop |
| malleable c2 profile | high ~8.8 |
| CVE poc | medium ~6.8 |
| 空描述 hash 名 | no_info drop |

## 命令

```bash
python -m monitor.run --migrate-only --publish
python -m monitor.run --daily --publish
python -m monitor.run --skills --publish
python -m monitor.run --trending --publish
```
