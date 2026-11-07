# -*- coding: utf-8 -*-
"""Token 实时看板 · 控制入口。

    python ctl.py ensure     # 供 WorkBuddy SessionStart hook 调用：保证服务在跑，输出 hook JSON
    python ctl.py start      # 后台启动
    python ctl.py stop       # 停止
    python ctl.py restart
    python ctl.py status     # 运行状态 + 关键数字
    python ctl.py open       # 保证服务在跑并打开浏览器
    python ctl.py once       # 只采集一次并落盘（不需要常驻服务）
    python ctl.py url        # 打印看板地址
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def bring_up(cfg: dict, wait: float = 3.0):
    """保证服务在运行；返回 ping 结果或 None。"""
    p = C.health(cfg)
    if p is not None:
        return p
    C.spawn_monitor()
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.25)
        p = C.health(cfg, timeout=0.4)
        if p is not None:
            return p
    return None


def fetch_stats(cfg: dict):
    try:
        with urllib.request.urlopen(C.base_url(cfg).rstrip("/") + "/api/stats", timeout=1.0) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def hook_payload(cfg: dict, stats, alive: bool) -> dict:
    url = C.base_url(cfg)
    if alive:
        today = (stats or {}).get("todayTokens", 0)
        cost = (stats or {}).get("estimatedCostCny", 0)
        msg = "📊 Token 实时看板 · 今日 %s tokens · 预估 ¥%.1f · 打开 %s" % (
            C.fmt_tokens(today), cost, url)
    else:
        msg = "⚠️ Token 实时看板未启动，可运行：python %s start" % (
            Path(__file__).resolve())
    return {
        "continue": True,
        "systemMessage": msg,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "[token-live-monitor] 本机常驻 Token 看板地址：%s 。"
                "用户说「看板」「token 用量」「消耗统计」时，直接用结果展示工具打开该本地地址，"
                "并读取 %s 汇报关键数字；不要重新生成看板、不要扫描日志。"
                % (url, C.STATS_PATH)
            ),
        },
    }


def cmd_ensure(args, cfg):
    p = bring_up(cfg)
    stats = fetch_stats(cfg) if p else None
    # 这一条 stdout 必须只有 JSON，供 hooks 解析
    print(json.dumps(hook_payload(cfg, stats, p is not None), ensure_ascii=False))
    return 0


def cmd_start(args, cfg):
    if C.health(cfg):
        print("already running at " + C.base_url(cfg))
        return 0
    C.spawn_monitor()
    p = bring_up(cfg)
    print("started at " + C.base_url(cfg) if p else "start failed, see " + str(C.LOG_PATH))
    return 0 if p else 1


def cmd_stop(args, cfg):
    pid = C.read_pid()
    if not pid:
        print("not running (no pid file)")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    for _ in range(12):
        if C.health(cfg, timeout=0.3) is None:
            print("stopped (pid %s)" % pid)
            return 0
        time.sleep(0.25)
    print("stop signal sent, but service still responds; see " + str(C.LOG_PATH))
    return 1


def cmd_status(args, cfg):
    p = C.health(cfg)
    out = {
        "running": p is not None,
        "url": C.base_url(cfg),
        "pid": C.read_pid(),
        "config": cfg,
        "stateDir": str(C.STATE_DIR),
        "log": str(C.LOG_PATH),
        "stats": fetch_stats(cfg) if p else None,
        "ping": p,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_open(args, cfg):
    bring_up(cfg)
    webbrowser.open(C.base_url(cfg))
    print(C.base_url(cfg))
    return 0


def cmd_once(args, cfg):
    from dashboard import Collector, render, summary
    data = Collector(cfg["projects"]).scan()
    stats = summary(data)
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    C.atomic_write(C.STATS_PATH, json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
    C.atomic_write(C.DASHBOARD_PATH, render(data, live=False))
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("snapshot: " + str(C.DASHBOARD_PATH))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="token-live-monitor 控制入口")
    ap.add_argument("command", nargs="?", default="status",
                    choices=["ensure", "start", "stop", "restart", "status", "open", "once", "url"])
    args = ap.parse_args()
    cfg = C.load_config()

    if args.command == "ensure":
        return cmd_ensure(args, cfg)
    if args.command == "start":
        return cmd_start(args, cfg)
    if args.command == "stop":
        return cmd_stop(args, cfg)
    if args.command == "restart":
        cmd_stop(args, cfg)
        time.sleep(0.4)
        return cmd_start(args, cfg)
    if args.command == "status":
        return cmd_status(args, cfg)
    if args.command == "open":
        return cmd_open(args, cfg)
    if args.command == "once":
        return cmd_once(args, cfg)
    print(C.base_url(cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
