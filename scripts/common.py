# -*- coding: utf-8 -*-
"""token-live-monitor 共享配置、路径与进程工具（仅标准库）。

所有运行时状态集中在 ~/.workbuddy/token-live/，卸载时只需删除该目录，
不会在 WorkBuddy 的会话日志或用户工程目录里留任何东西。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
ASSETS_DIR = SKILL_DIR / "assets"
HOME = Path.home()
STATE_DIR = Path(os.environ.get("TOKEN_LIVE_HOME") or (HOME / ".workbuddy" / "token-live"))

CONFIG_PATH = STATE_DIR / "config.json"
PID_PATH = STATE_DIR / "monitor.pid"
LOG_PATH = STATE_DIR / "monitor.log"
STATS_PATH = STATE_DIR / "stats.json"
DASHBOARD_PATH = STATE_DIR / "dashboard.html"

WORKBUDDY_SETTINGS = HOME / ".workbuddy" / "settings.json"
CODEBUDDY_SETTINGS = HOME / ".codebuddy" / "settings.json"
MARKER = "token-live-monitor"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8765,
    "interval": 30,
    "projects": str(HOME / ".workbuddy" / "projects"),
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    for key, env in (("port", "TOKEN_LIVE_PORT"), ("interval", "TOKEN_LIVE_INTERVAL"),
                     ("projects", "TOKEN_LIVE_PROJECTS"), ("host", "TOKEN_LIVE_HOST")):
        if os.environ.get(env):
            cfg[key] = int(os.environ[env]) if key in ("port", "interval") else os.environ[env]
    return cfg


def save_config(cfg: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def base_url(cfg: dict = None) -> str:
    cfg = cfg or load_config()
    return "http://%s:%s/" % (cfg["host"], cfg["port"])


def python_exe(windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and os.name == "nt":
        pw = exe.with_name("pythonw.exe")
        if pw.is_file():
            return str(pw)
    return str(exe)


def health(cfg: dict = None, timeout: float = 0.6):
    """返回 /api/ping 的 JSON；服务未运行时返回 None。"""
    cfg = cfg or load_config()
    try:
        with urllib.request.urlopen(base_url(cfg).rstrip("/") + "/api/ping", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def spawn_monitor() -> int:
    """以脱离终端的方式启动 monitor.py，立即返回（不等待）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [python_exe(True), str(SCRIPTS_DIR / "monitor.py")]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw).pid


def log(msg: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.is_file() and LOG_PATH.stat().st_size > 512 * 1024:
            lines = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
            LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (stamp, msg))
    except Exception:
        pass


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def fmt_tokens(n: int) -> str:
    if n >= 1e8:
        return "%.2f 亿" % (n / 1e8)
    if n >= 1e4:
        return "%.1f 万" % (n / 1e4)
    return str(n)


def read_pid():
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return None
