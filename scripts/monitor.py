# -*- coding: utf-8 -*-
"""Token 实时看板 · 常驻后台进程。

启动后做两件事：
1. 每 `interval` 秒增量扫描 WorkBuddy 会话日志，更新内存数据并把
   stats.json / dashboard.html（离线快照）落盘；
2. 在 127.0.0.1:<port> 提供单文件看板与 /api/stats，供随时查看。

单实例保证来自「绑定端口」：端口已被占用时说明已有实例，直接退出。
仅绑定回环地址，不接受局域网访问；仅依赖 Python 标准库（3.8+）。

    python monitor.py            # 前台常驻（调试用）
    正常由 ctl.py start / install.py 以脱离终端方式拉起。
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
from dashboard import Collector, render, summary  # noqa: E402


class State:
    def __init__(self, cfg):
        self.cfg = cfg
        self.collector = Collector(cfg["projects"])
        self.started = time.time()
        self.data = None
        self.stats = None
        self.last_error = None
        self.rev = 0
        self.fingerprint = None
        self.lock = threading.Lock()

    @staticmethod
    def _fingerprint(data):
        """只在真实用量变化时递增 rev，避免前端无意义地整页刷新。"""
        turns = sum(len(s["r"]) for s in data["sess"])
        total = sum(r[2] for s in data["sess"] for r in s["r"])
        last = max((s["r"][-1][0] for s in data["sess"] if s["r"]), default=0)
        return (len(data["sess"]), turns, total, last)

    def refresh(self) -> bool:
        try:
            data = self.collector.scan()
            stats = summary(data)
            fp = self._fingerprint(data)
            with self.lock:
                if fp != self.fingerprint:
                    self.fingerprint = fp
                    self.rev += 1
                self.data, self.stats, self.last_error = data, stats, None
            C.atomic_write(C.STATS_PATH, json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
            C.atomic_write(C.DASHBOARD_PATH, render(data, live=False))
            return True
        except Exception as exc:
            self.last_error = repr(exc)
            C.log("refresh failed: %s" % self.last_error)
            C.log(traceback.format_exc(limit=3))
            return False

    def ping(self) -> dict:
        with self.lock:
            gen_ms = self.data["genMs"] if self.data else 0
            gen = self.data["gen"] if self.data else None
        return {"ok": True, "genMs": gen_ms, "gen": gen, "rev": self.rev,
                "uptimeSec": int(time.time() - self.started),
                "lastError": self.last_error}


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenLive/1.0"

    def _send(self, code, ctype, body: bytes, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        state = self.server.state  # type: State
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if state.data is None:
                state.refresh()
            if state.data is None:
                msg = ("<meta charset='utf-8'><h2>暂无数据</h2><p>未找到会话日志：%s</p>"
                       "<p>确认 WorkBuddy 已产生会话，或检查 config.json 的 projects。</p>"
                       % state.cfg["projects"])
                self._send(200, "text/html; charset=utf-8", msg.encode("utf-8"))
                return
            html = render(state.data, live=True,
                          poll_ms=max(5000, int(state.cfg["interval"]) * 1000))
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
        elif path == "/api/stats":
            self._json(state.stats or summary({"sess": [], "models": [], "ws": [],
                                               "genMs": 0, "gen": ""}))
        elif path == "/api/ping":
            self._json(state.ping())
        elif path == "/api/data":
            self._json(state.data or {})
        elif path == "/health":
            self._send(200, "text/plain; charset=utf-8", b"ok")
        elif path == "/refresh":
            state.refresh()
            self._send(302, "text/plain; charset=utf-8", b"", {"Location": "/"})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def log_message(self, fmt, *args):
        # 前端轮询 /api/ping 很频繁，忽略它以免日志刷屏；其余请求仍留痕
        msg = fmt % args
        if "/api/ping" not in msg:
            C.log("http " + msg)


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    cfg = C.load_config()
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    C.log("monitor starting | port=%s interval=%ss projects=%s" %
          (cfg["port"], cfg["interval"], cfg["projects"]))

    try:
        httpd = Server((cfg["host"], cfg["port"]), Handler)
    except OSError as exc:
        C.log("port %s unavailable (another instance?): %s" % (cfg["port"], exc))
        print("port %s is already in use; assuming an instance is running" % cfg["port"])
        return 0

    state = State(cfg)
    httpd.state = state
    state.refresh()
    C.PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    stop_event = threading.Event()

    def collector_loop():
        while not stop_event.wait(max(5, int(cfg["interval"]))):
            state.refresh()

    def on_signal(signum, frame):
        C.log("signal %s received, shutting down" % signum)
        stop_event.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    threading.Thread(target=collector_loop, daemon=True).start()

    print("token-live-monitor ready at %s" % C.base_url(cfg))
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        stop_event.set()
        httpd.server_close()
        try:
            C.PID_PATH.unlink()
        except OSError:
            pass
        C.log("monitor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
