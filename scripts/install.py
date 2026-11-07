# -*- coding: utf-8 -*-
"""Token 实时看板 · 一键安装。

完成四件事（每一步都幂等，可重复运行）：
1. 写运行时配置 ~/.workbuddy/token-live/config.json
2. 在 ~/.workbuddy/settings.json 合并 SessionStart hook —— 打开 WorkBuddy 新会话即唤醒后台统计
3. 注册登录自启动（Windows 启动文件夹 / macOS LaunchAgent / Linux autostart）—— 双保险
4. 创建桌面「Token Live Dashboard」按钮 —— 随时一键查看

    python install.py                       # 标准安装并立即启动
    python install.py --port 9000 --interval 20
    python install.py --no-autostart        # 只随 WorkBuddy 启动，不随系统登录启动
    python install.py --dry-run             # 只打印将做的改动
    python uninstall.py                     # 完整卸载

仅依赖 Python 标准库（3.8+）。修改 settings.json 前会自动备份。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def hook_command() -> str:
    py = C.python_exe(False).replace("\\", "/")
    ctl = (C.SCRIPTS_DIR / "ctl.py").as_posix()
    return '"%s" "%s" ensure' % (py, ctl)


def merge_hook(settings_path: Path, dry: bool) -> str:
    """把 SessionStart hook 幂等合并进 settings.json，不覆盖已有 hooks。"""
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return "跳过（配置不是合法 JSON：%s）" % exc
    else:
        data = {}
        settings_path.parent.mkdir(parents=True, exist_ok=True)

    hooks = data.setdefault("hooks", {})
    events = hooks.setdefault("SessionStart", [])
    group = None
    for item in events:
        if isinstance(item, dict) and item.get("matcher", "") in ("startup", "", "*"):
            group = item
            break
    if group is None:
        group = {"matcher": "startup", "hooks": []}
        events.append(group)
    entries = group.setdefault("hooks", [])

    for e in entries:
        if isinstance(e, dict) and C.MARKER in str(e.get("command", "")):
            # 已存在：刷新命令路径（skill 可能搬过家），但不重复添加
            old = e.get("command", "")
            if old != hook_command():
                e["command"] = hook_command()
                e["timeout"] = 10
                if not dry:
                    backup = settings_path.with_suffix(settings_path.suffix + ".bak.token-live-monitor")
                    if not backup.exists():
                        shutil.copy2(settings_path, backup)
                    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
                return "已存在，命令路径已刷新"
            return "已存在，无需改动"

    entries.append({"type": "command", "command": hook_command(), "timeout": 10})
    if not dry:
        backup = settings_path.with_suffix(settings_path.suffix + ".bak.token-live-monitor")
        if not backup.exists():
            shutil.copy2(settings_path, backup)
        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    return "已写入（原配置备份为 *.bak.token-live-monitor）"


def windows_startup(dry: bool) -> str:
    root = Path(os.environ.get("APPDATA", C.HOME / "AppData" / "Roaming"))
    startup = root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    target = startup / "TokenLiveMonitor.vbs"
    pyw = C.python_exe(True).replace("\\", "/")
    ctl = (C.SCRIPTS_DIR / "ctl.py").as_posix()
    content = ('Set sh = CreateObject("WScript.Shell")\r\n'
               'sh.Run """%s"" ""%s"" start", 0, False\r\n' % (pyw, ctl))
    if not dry:
        startup.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return str(target)


def macos_startup(dry: bool) -> str:
    target = C.HOME / "Library" / "LaunchAgents" / "com.workbuddy.token-live-monitor.plist"
    py = C.python_exe(True)
    ctl = str(C.SCRIPTS_DIR / "ctl.py")
    content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.workbuddy.token-live-monitor</string>
<key>ProgramArguments</key><array><string>%s</string><string>%s</string><string>start</string></array>
<key>RunAtLoad</key><true/>
<key>StandardOutPath</key><string>%s</string>
<key>StandardErrorPath</key><string>%s</string>
</dict></plist>
""" % (py, ctl, C.LOG_PATH, C.LOG_PATH)
    if not dry:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return str(target)


def linux_startup(dry: bool) -> str:
    target = C.HOME / ".config" / "autostart" / "token-live-monitor.desktop"
    content = ("[Desktop Entry]\nType=Application\nName=Token Live Monitor\n"
               'Exec="%s" "%s" start\nTerminal=false\nX-GNOME-Autostart-enabled=true\n'
               % (C.python_exe(True), C.SCRIPTS_DIR / "ctl.py"))
    if not dry:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return str(target)


def desktop_shortcut(cfg: dict, dry: bool) -> str:
    """创建桌面一键按钮。Windows 用 .lnk（先唤醒服务再开浏览器），失败降级为 .url。"""
    desktop = C.HOME / "Desktop"
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Environment]::GetFolderPath('Desktop')"],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                desktop = Path(out.stdout.strip())
        except Exception:
            pass
        lnk = desktop / "Token Live Dashboard.lnk"
        pyw = C.python_exe(True)
        ctl = str(C.SCRIPTS_DIR / "ctl.py")
        ps = (
            "$w=New-Object -ComObject WScript.Shell;"
            "$s=$w.CreateShortcut(%s);"
            "$s.TargetPath=%s;"
            "$s.Arguments=%s;"
            "$s.WorkingDirectory=%s;"
            "$s.Description='WorkBuddy Token Live Dashboard';"
            "$s.IconLocation=%s;"
            "$s.Save();"
        )
        def q(v):
            return "'" + str(v).replace("'", "''") + "'"
        icon = C.ASSETS_DIR / "token-live.ico"
        icon_value = str(icon) if icon.is_file() else "shell32.dll,21"
        script = ps % (q(str(lnk)), q(pyw), q('"%s" open' % ctl),
                       q(str(C.SCRIPTS_DIR)), q(icon_value))
        if not dry:
            try:
                r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                   capture_output=True, text=True, timeout=20)
                if r.returncode == 0 and lnk.exists():
                    # 刷新 Explorer 图标缓存，让新图标立即生效（失败不影响安装）
                    try:
                        subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=8)
                    except Exception:
                        pass
                    return str(lnk) + (" (专属图标)" if icon.is_file() else "")
            except Exception:
                pass
            # 降级：直接放一个 .url（浏览器快捷方式）
            fallback = desktop / "Token Live Dashboard.url"
            try:
                desktop.mkdir(parents=True, exist_ok=True)
                fallback.write_text("[InternetShortcut]\r\nURL=%s\r\n" % C.base_url(cfg),
                                    encoding="ascii")
                return str(fallback) + " (降级为 URL 快捷方式)"
            except Exception:
                return "创建失败（可手动运行 ctl.py open）"
        return str(lnk)
    else:
        target = desktop / "token-live-dashboard.desktop"
        content = ("[Desktop Entry]\nType=Application\nName=Token Live Dashboard\n"
                   'Exec="%s" "%s" open\nTerminal=false\n'
                   % (C.python_exe(True), C.SCRIPTS_DIR / "ctl.py"))
        if not dry:
            try:
                desktop.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                target.chmod(0o755)
            except Exception:
                return "创建失败（桌面目录不可写）"
        return str(target)


def main() -> int:
    ap = argparse.ArgumentParser(description="安装 token-live-monitor 常驻看板")
    ap.add_argument("--port", type=int, default=None, help="本地端口（默认 8765）")
    ap.add_argument("--interval", type=int, default=None, help="采集间隔秒数（默认 30）")
    ap.add_argument("--projects", default=None, help="会话日志目录")
    ap.add_argument("--no-hook", action="store_true", help="不写 WorkBuddy SessionStart hook")
    ap.add_argument("--no-autostart", action="store_true", help="不注册系统登录自启动")
    ap.add_argument("--no-shortcut", action="store_true", help="不创建桌面按钮")
    ap.add_argument("--no-start", action="store_true", help="安装后不立即启动服务")
    ap.add_argument("--cli-hook", action="store_true", help="同时把 hook 写进 ~/.codebuddy/settings.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写任何文件")
    a = ap.parse_args()

    cfg = C.load_config()
    if a.port is not None:
        cfg["port"] = a.port
    if a.interval is not None:
        cfg["interval"] = max(5, a.interval)
    if a.projects:
        cfg["projects"] = str(Path(a.projects).expanduser())
    if a.dry_run:
        print("[dry-run] config.json -> %s" % json.dumps(cfg, ensure_ascii=False))
    else:
        C.save_config(cfg)

    print("安装摘要 %s" % ("（dry-run）" if a.dry_run else ""))
    print("  看板地址   : %s" % C.base_url(cfg))
    print("  状态目录   : %s" % C.STATE_DIR)
    print("  会话日志   : %s" % cfg["projects"])

    if not a.no_hook:
        result = merge_hook(C.WORKBUDDY_SETTINGS, a.dry_run)
        print("  WorkBuddy  : SessionStart hook %s" % result)
        if a.cli_hook:
            print("  CodeBuddy  : SessionStart hook %s" %
                  merge_hook(C.CODEBUDDY_SETTINGS, a.dry_run))
    else:
        print("  WorkBuddy  : 跳过 hook（--no-hook）")

    if not a.no_autostart:
        fn = windows_startup if os.name == "nt" else (macos_startup if sys.platform == "darwin" else linux_startup)
        print("  登录自启动 : %s" % fn(a.dry_run))
    else:
        print("  登录自启动 : 跳过（--no-autostart）")

    if not a.no_shortcut:
        print("  桌面按钮   : %s" % desktop_shortcut(cfg, a.dry_run))
    else:
        print("  桌面按钮   : 跳过（--no-shortcut）")

    if not a.no_start and not a.dry_run:
        import ctl
        alive = ctl.bring_up(cfg, wait=4.0)
        print("  后台服务   : %s" % ("已运行" if alive else "启动失败，见 " + str(C.LOG_PATH)))
        if alive:
            print("  关键数字   : %s" % json.dumps(ctl.fetch_stats(cfg), ensure_ascii=False)[:220])
    else:
        print("  后台服务   : 未启动（--no-start / --dry-run）")

    print("  在 WorkBuddy 里：新开会话会自动显示看板链接；说「看板」可直接打开。")
    print("  卸载：python %s" % (C.SCRIPTS_DIR / "uninstall.py"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
