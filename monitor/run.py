"""GitHub Security Monitor V4 — CLI 入口
用法:
    python monitor/run.py --daily       # 每天4次执行: CVE + 关键词 + 用户 + 工具
    python monitor/run.py --trending     # 每周1次: 热门飙升
    python monitor/run.py --all          # 全部执行（调试用）
"""
import sys
import os
import datetime
import argparse

# 确保项目根目录在 Python 路径中（兼容 GitHub Actions 和本地运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.config import load_executions, save_executions


def main():
    parser = argparse.ArgumentParser(description='GitHub Security Monitor V4')
    parser.add_argument('--daily', action='store_true', help='Daily monitor (CVE+Keyword+User+Tool)')
    parser.add_argument('--trending', action='store_true', help='Weekly trending repos')
    parser.add_argument('--all', action='store_true', help='Run all monitors (debug)')
    parser.add_argument('--cve', action='store_true', help='CVE only')
    parser.add_argument('--keyword', action='store_true', help='Keyword only')
    parser.add_argument('--user', action='store_true', help='User only')
    parser.add_argument('--tool', action='store_true', help='Tool only')
    parser.add_argument('--hours', type=int, default=7, help='Time window in hours (default: 7)')
    args = parser.parse_args()

    start_time = datetime.datetime.now()
    print(f"\n{'='*50}")
    print(f"GitHub Security Monitor V4")
    print(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'Daily' if args.daily else 'Trending' if args.trending else 'Custom'}")
    print(f"{'='*50}\n")

    results = {}
    executions = load_executions()

    if args.daily or args.all:
        results.update(_run_daily(args.hours))
    elif args.trending:
        results.update(_run_trending())
    else:
        if args.cve:
            from monitor.cve import run_cve
            results['cve'] = len(run_cve(args.hours))
        if args.keyword:
            from monitor.kw_monitor import run_keyword
            results['keyword'] = len(run_keyword(args.hours))
        if args.user:
            from monitor.user import run_user
            results['user'] = len(run_user())
        if args.tool:
            from monitor.tool import run_tool
            results['tool'] = len(run_tool())

    # 发送通知
    from monitor.notify import notify_summary
    notify_summary(results)

    # 记录执行历史
    end_time = datetime.datetime.now()
    total_new = sum(results.values())
    execution = {
        'id': len(executions) + 1,
        'type': 'daily' if args.daily else 'trending' if args.trending else 'custom',
        'status': 'success' if sum(results.values()) >= 0 else 'partial',
        'started_at': start_time.isoformat(),
        'finished_at': end_time.isoformat(),
        'duration_seconds': (end_time - start_time).total_seconds(),
        'results': results,
        'total_new': total_new
    }
    executions.append(execution)
    save_executions(executions)

    print(f"\n{'='*50}")
    print(f"COMPLETED in {execution['duration_seconds']:.1f}s")
    print(f"Results: {results}")
    print(f"Total new: {total_new}")
    print(f"{'='*50}\n")


def _run_daily(hours: int) -> dict:
    results = {}
    try:
        from monitor.cve import run_cve
        results['cve'] = len(run_cve(hours))
    except Exception as e:
        print(f"[ERROR] CVE: {e}")
        results['cve'] = -1

    try:
        from monitor.kw_monitor import run_keyword
        results['keyword'] = len(run_keyword(hours))
    except Exception as e:
        print(f"[ERROR] Keyword: {e}")
        results['keyword'] = -1

    try:
        from monitor.user import run_user
        results['user'] = len(run_user())
    except Exception as e:
        print(f"[ERROR] User: {e}")
        results['user'] = -1

    try:
        from monitor.tool import run_tool
        results['tool'] = len(run_tool())
    except Exception as e:
        print(f"[ERROR] Tool: {e}")
        results['tool'] = -1

    return results


def _run_trending() -> dict:
    try:
        from monitor.trending import run_trending
        results = {'trending': len(run_trending())}
    except Exception as e:
        print(f"[ERROR] Trending: {e}")
        results = {'trending': -1}
    return results


if __name__ == '__main__':
    main()
