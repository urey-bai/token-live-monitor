# -*- coding: utf-8 -*-
"""Token 实时看板 · 卸载 / 停用。

默认只移除「启动项」，保留统计快照与日志；加 --purge 才删除数据目录。

    python uninstall.py             # 停止服务 + 移除 hook / 自启动 / 桌面按钮
    python uninstall.py --purge     # 额外删除 ~/.workbuddy/token-live/
    python uninstall.py --dry-run   # 只打印将删除的内容
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def remove_hook(path: Path, dry: bool) -> str:
    if not path.is_file():
        return "不存在，跳过"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return "跳过（JSON 无法解析：%s）" % exc

    events = data.get("hooks", {}).get("SessionStart", [])
    removed = 0
    for group in events:
        if not isinstance(group, dict):
            continue
        before = len(group.get("hooks", []))
        group["hooks"] = [e for e in group.get("hooks", [])
                          if not (isinstance(e, dict) and C.MARKER in str(e.get("command", "")))]
        removed += before - len(group["hooks"])
    data["hooks"]["SessionStart"] = [g for g in events if g.get("hooks")]
    if not data["hooks"]["SessionStart"]:
        data["hooks"].pop("SessionStart", None)
    if not data["hooks"]:
        data.pop("hooks", None)

    if not removed:
        return "未发现本 skill 的 hook，跳过"
    if not dry:
        backup = path.with_suffix(path.suffix + ".bak.token-live-monitor")
        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "已移除 %s 条（原配置备份为 *.bak.token-live-monitor）" % removed


def autostart_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", C.HOME / "AppData" / "Roaming"))
        return root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "TokenLiveMonitor.vbs"
    if sys.platform == "darwin":
        return C.HOME / "Library" / "LaunchAgents" / "com.workbuddy.token-live-monitor.plist"
    return C.HOME / ".config" / "autostart" / "token-live-monitor.desktop"


def desktop_paths():
    if os.name == "nt":
        d = C.HOME / "Desktop"
        return [d / "Token Live Dashboard.lnk", d / "Token Live Dashboard.url"]
    return [C.HOME / "Desktop" / "token-live-dashboard.desktop"]


def main() -> int:
    ap = argparse.ArgumentParser(description="卸载 token-live-monitor")
    ap.add_argument("--purge", action="store_true", help="删除统计快照、配置与日志目录")
    ap.add_argument("--keep-running", action="store_true", help="不停止后台服务")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    a = ap.parse_args()

    if not a.keep_running and not a.dry_run:
        import ctl
        ctl.cmd_stop(None, C.load_config())

    print("卸载摘要 %s" % ("（dry-run）" if a.dry_run else ""))
    print("  WorkBuddy hook : %s" % remove_hook(C.WORKBUDDY_SETTINGS, a.dry_run))
    print("  CodeBuddy hook : %s" % remove_hook(C.CODEBUDDY_SETTINGS, a.dry_run))

    targets = [autostart_path()] + desktop_paths()
    for p in targets:
        if p.exists():
            print("  删除           : %s" % p)
            if not a.dry_run:
                try:
                    p.unlink()
                except OSError as exc:
                    print("    失败：%s" % exc)

    if a.purge:
        # 只逐个删除本 skill 自己创建的已知文件，绝不做递归清空
        known = [C.CONFIG_PATH, C.STATS_PATH, C.DASHBOARD_PATH, C.LOG_PATH, C.PID_PATH]
        print("  删除数据       : %s" % ", ".join(p.name for p in known))
        if not a.dry_run:
            for p in known:
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                C.STATE_DIR.rmdir()  # 目录里若还有别的文件就保留
            except OSError:
                pass
    else:
        print("  保留数据       : %s（--purge 可删除）" % C.STATE_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
