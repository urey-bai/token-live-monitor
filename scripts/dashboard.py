# -*- coding: utf-8 -*-
"""Token 实时看板 · 采集器与渲染器（改编自 token-dashboard/gen_dashboard.py）。

相对原脚本的三处改编：
1. 采集从「一次性顶层执行」改为带文件指纹缓存的 Collector，供常驻进程反复调用；
2. 渲染从「直接写文件」改为返回 HTML 字符串，供本地 HTTP 服务按需输出；
3. HTML 模板拆到 assets/dashboard_template.html，代码与 44 KB 模板解耦。

数据源：~/.workbuddy/projects/**/*.jsonl 的 providerData.rawUsage / usage（请求级真实 usage）。
仅依赖 Python 标准库（3.8+）；数据只在本机磁盘与 127.0.0.1 之间流动，不外发。

独立使用：
    python dashboard.py --out 看板.html [--projects <日志目录>]
"""
from __future__ import annotations

import argparse
import ast
import calendar
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "assets" / "dashboard_template.html"
TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")

MODEL_MERGE = {"glm-5.2-x": "glm-5.2"}
DEFAULT_PROJECTS = Path.home() / ".workbuddy" / "projects"


def _load_price() -> dict:
    """从模板里的 `const PRICE={...};` 解析价格表，保持单一数据源。"""
    m = re.search(r"const PRICE=(\{[^}]+\});", TEMPLATE)
    if not m:
        return {}
    try:
        return ast.literal_eval(m.group(1))
    except Exception:
        return {}


PRICE = _load_price()


def price_of(name: str) -> list:
    n = (name or "").lower()
    for k, v in PRICE.items():
        if n.startswith(k):
            return v
    return [2, 8, 0.4]


def parse_ts(v):
    if isinstance(v, (int, float)):
        if v > 1e12:
            return v / 1000.0
        if v > 1e9:
            return float(v)
    if isinstance(v, str):
        s = v.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return calendar.timegm(datetime.datetime.strptime(s, fmt).timetuple())
            except Exception:
                pass
        try:
            return float(s) / 1000.0
        except Exception:
            return None
    return None


def grab(o: dict):
    """抽取一条请求级 usage；返回 None 表示这条记录没有用量数据。"""
    pd = o.get("providerData")
    if not isinstance(pd, dict):
        return None
    ru = pd.get("rawUsage") or {}
    u = pd.get("usage") or {}
    if not isinstance(ru, dict):
        ru = {}
    if not isinstance(u, dict):
        u = {}
    tt = ru.get("total_tokens") or u.get("totalTokens")
    if not tt:
        return None
    model = pd.get("model") or pd.get("requestModelId") or pd.get("requestModelName") or "unknown"
    ptd = ru.get("prompt_tokens_details") or {}
    if not isinstance(ptd, dict):
        ptd = {}
    ch = ru.get("prompt_cache_hit_tokens")
    if ch is None:
        ch = ptd.get("cached_tokens") or 0
    cw = ptd.get("cached_creation_tokens") or ptd.get("cache_write_tokens") or 0
    return dict(tt=int(tt), it=int(ru.get("prompt_tokens") or u.get("inputTokens") or 0),
                ot=int(ru.get("completion_tokens") or u.get("outputTokens") or 0),
                ch=int(ch or 0), cw=int(cw or 0), model=model)


_TAG_BLOCK = re.compile(
    r"<(system-reminder|task-notification|user-prompt-submit-hook|command-name|"
    r"additional_data|additional-data|user_info|identity_context|project_context|"
    r"connector-status|manually_attached_skills|wecom_wedrive_references)\b[^>]*>.*?</\1\s*>",
    re.S | re.I)
_USER_QUERY = re.compile(r"<user_query[^>]*>(.*?)</user_query\s*>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")


def _clean_text(t: str) -> str:
    """剥掉 WorkBuddy 注入的内部上下文标签，留下真正的用户文字。"""
    t = _TAG_BLOCK.sub(" ", t or "")
    t = _ANY_TAG.sub(" ", t)
    return " ".join(t.split())


def session_title(o: dict, fallback: str = ""):
    """从一条用户消息里抽取可读标题。

    WorkBuddy 把真实提问包在 <user_query> 里，前面还夹着大段
    <system-reminder> 内部上下文。旧实现见段首是 '<' 就直接返回 None，
    导致所有会话都显示「(无标题会话)」。这里优先取 <user_query>，
    再退到剥掉标签后的正文。
    """
    if o.get("role") != "user":
        return None
    c = o.get("content")
    if isinstance(c, str):
        texts = [c]
    elif isinstance(c, list):
        texts = [(s.get("text") or "") for s in c
                 if isinstance(s, dict) and s.get("type") in ("input_text", "text")]
    else:
        return None
    for t in texts:
        m = _USER_QUERY.search(t)
        if m:
            q = _clean_text(m.group(1))
            if q:
                return q[:42]
    for t in texts:
        q = _clean_text(t)
        if q:
            return q[:42]
    return None


def fallback_title(folder: str, sid: str, recs) -> str:
    """连 aiTitle 与 user_query 都取不到时，给出仍可区分的标识（工作空间 + 时间 + 会话短码）。"""
    short = short_ws(folder) or "会话"
    if len(short) > 20:
        short = short[:19] + "…"
    stamp = ""
    if recs:
        stamp = datetime.datetime.fromtimestamp(
            min(r[0] for r in recs) * 60).strftime("%m-%d %H:%M")
    return " · ".join(x for x in (short, stamp, "#" + (sid or "")[:6]) if x)


def short_ws(name: str) -> str:
    """工作空间目录是路径 slug，取 WorkBuddy 之后的部分作为短名。"""
    i = name.rfind("-WorkBuddy-")
    if i >= 0 and name[i + 11:]:
        return name[i + 11:]
    return name


class Collector:
    """带文件指纹缓存的增量采集器。

    scan() 只重读 size / mtime 变化过的 .jsonl，其余复用上一次解析结果，
    因此常驻进程每 20~30 秒扫一次几乎不产生磁盘与 CPU 负担。
    """

    def __init__(self, projects=None):
        self.projects = str(projects or DEFAULT_PROJECTS)
        self._cache = {}  # fp -> (signature, folder, sid, title, recs)

    def scan(self) -> dict:
        if not os.path.isdir(self.projects):
            raise FileNotFoundError("会话日志目录不存在: %s" % self.projects)

        files = sorted(glob.glob(os.path.join(self.projects, "**", "*.jsonl"), recursive=True))
        live = set(files)
        for dead in set(self._cache) - live:
            del self._cache[dead]

        for fp in files:
            try:
                st = os.stat(fp)
                sig = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
            cached = self._cache.get(fp)
            if cached and cached[0] == sig:
                continue

            rel = os.path.relpath(fp, self.projects)
            parts = rel.split(os.sep)
            folder = parts[0] if parts else ""
            sid = os.path.splitext(parts[1])[0] if len(parts) == 2 else (
                parts[1] if len(parts) > 1 else folder)
            recs = []
            ai_title, user_title = None, None
            try:
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        try:
                            o = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(o, dict):
                            continue
                        sid_r = o.get("sessionId")
                        if sid_r and len(parts) == 2:
                            sid = sid_r
                        # WorkBuddy 自动生成的会话标题；取首个非空值（任务创建时的标题最贴近任务名）
                        v = o.get("aiTitle")
                        if ai_title is None and isinstance(v, str) and v.strip():
                            ai_title = v.strip()[:42]
                        if user_title is None:
                            user_title = session_title(o, sid)
                        r = grab(o)
                        if not r:
                            continue
                        ts = parse_ts(o.get("timestamp"))
                        if not ts:
                            continue
                        recs.append((int(ts // 60), MODEL_MERGE.get(r["model"], r["model"]),
                                     r["tt"], r["it"], r["ot"], r["ch"], r["cw"]))
            except OSError:
                continue
            if ai_title:
                title, rank = ai_title, 3
            elif user_title:
                title, rank = user_title, 2
            else:
                title, rank = fallback_title(folder, sid, recs), 1
            self._cache[fp] = (sig, folder, sid, title, rank, recs)

        return self._assemble(files)

    def _assemble(self, files) -> dict:
        now = datetime.datetime.now()
        ws_names, ws_idx = {}, {}
        models, model_idx = [], {}
        sessions = {}

        for fp in files:
            item = self._cache.get(fp)
            if not item:
                continue
            _, folder, sid, title, rank, recs = item
            if not recs:
                continue
            if folder not in ws_idx:
                ws_names[folder] = short_ws(folder)
                ws_idx[folder] = len(ws_idx)
            rows = []
            for minute, model, tt, it, ot, ch, cw in recs:
                if model not in model_idx:
                    model_idx[model] = len(models)
                    models.append(model)
                rows.append((minute, model_idx[model], tt, it, ot, ch, cw))
            rows.sort(key=lambda x: x[0])
            d = sessions.setdefault(sid, {"w": ws_idx[folder], "id": sid,
                                          "t": title, "rank": rank, "r": []})
            d["r"].extend(rows)
            # 主会话的 aiTitle 优先于子代理日志里的提问摘要
            if title and rank > d["rank"]:
                d["t"], d["rank"] = title, rank

        sess_list = []
        for sid, d in sessions.items():
            if not d["r"]:
                continue
            sess_list.append({"w": d["w"], "id": sid[:8], "t": d["t"] or ("#" + sid[:6]),
                              "st": d["r"][0][0], "r": d["r"]})
        sess_list.sort(key=lambda s: s["st"])

        return {"genMs": int(now.timestamp() * 1000),
                "gen": now.strftime("%Y-%m-%d %H:%M:%S"),
                "ws": list(ws_names.values()), "models": models, "sess": sess_list}


def summary(data: dict) -> dict:
    """汇总 KPI，供 /api/stats 与 SessionStart hook 的提示语使用。"""
    tot = inp = out = cache_hit = turns = 0
    cost = 0.0
    by_model = {}
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    today_tokens = 0
    mp = [price_of(m) for m in data["models"]]

    for s in data["sess"]:
        for r in s["r"]:
            minute, mi, tt, it, ot, ch, cw = r
            tot += tt
            inp += it
            out += ot
            turns += 1
            cht = max(0, min(ch, it))
            cwt = max(0, min(cw or 0, it - cht))
            cache_hit += cht
            pp = mp[mi] if 0 <= mi < len(mp) else [2, 8, 0.4]
            cost += (cht * pp[2] + cwt * 1.25 * pp[0]
                     + (it - cht - cwt) * pp[0] + ot * pp[1]) / 1e6
            if datetime.datetime.fromtimestamp(minute * 60).strftime("%Y-%m-%d") == today:
                today_tokens += tt
            m = data["models"][mi] if 0 <= mi < len(data["models"]) else "unknown"
            by_model[m] = by_model.get(m, 0) + tt

    days = 1
    if data["sess"]:
        first = min(s["st"] for s in data["sess"])
        days = max(1, (int(datetime.datetime.now().timestamp() // 60) - first) // 1440 + 1)
    top = sorted(by_model.items(), key=lambda kv: -kv[1])[:5]
    return {
        "ok": True,
        "generated": data["gen"],
        "genMs": data["genMs"],
        "totalTokens": tot,
        "todayTokens": today_tokens,
        "dailyAverage": round(tot / days),
        "sessions": len(data["sess"]),
        "turns": turns,
        "inputTokens": inp,
        "outputTokens": out,
        "cacheHitRate": round(cache_hit / inp * 100, 1) if inp else 0.0,
        "estimatedCostCny": round(cost, 2),
        "models": len(data["models"]),
        "workspaces": len(data["ws"]),
        "topModels": [{"model": k, "tokens": v} for k, v in top],
    }


def render(data: dict, live: bool = False, poll_ms: int = 15000) -> str:
    """把数据注入单文件 HTML 模板。live=True 时追加实时轮询与角标。"""
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("__DATA__", js)
    if live:
        inject = """<script>
(function(){
  var EVERY=%d, REV=null;
  var badge=document.createElement('div');
  badge.style.cssText='position:fixed;right:16px;bottom:14px;z-index:99999;font:11px/1.5 -apple-system,"Segoe UI","PingFang SC",sans-serif;letter-spacing:.06em;padding:6px 13px;border-radius:999px;background:linear-gradient(100deg,rgba(11,28,52,.94),rgba(10,66,84,.94));color:#7ff0e2;border:1px solid rgba(94,234,212,.42);box-shadow:0 0 20px rgba(56,189,248,.26),0 6px 18px rgba(2,8,23,.42);backdrop-filter:blur(8px);cursor:pointer;transition:transform .15s,box-shadow .15s';
  badge.textContent='\\u25c9 实时看板';
  badge.title='点击立即刷新';
  badge.onmouseenter=function(){badge.style.transform='translateY(-2px)';badge.style.boxShadow='0 0 26px rgba(94,234,212,.5)';};
  badge.onmouseleave=function(){badge.style.transform='';badge.style.boxShadow='0 0 20px rgba(56,189,248,.26),0 6px 18px rgba(2,8,23,.42)';};
  badge.onclick=function(){location.reload();};
  document.body.appendChild(badge);
  setInterval(function(){
    fetch('/api/ping').then(function(r){return r.json();}).then(function(j){
      if(REV===null){REV=j.rev;badge.title='数据更新于 '+(j.gen||'-')+' · 点击立即刷新';return;}
      if(j.rev && j.rev!==REV){location.reload();return;}
      badge.title='数据更新于 '+(j.gen||'-')+' · 点击立即刷新';
    }).catch(function(){badge.textContent='\\u25cb 连接中断';badge.style.color='#fb7185';});
  },EVERY);
})();
</script>""" % poll_ms
        html = html.replace("</body>", inject + "</body>")
    return html


def main() -> int:
    ap = argparse.ArgumentParser(description="Token 实时看板 · 一次性生成")
    ap.add_argument("--projects", default=str(DEFAULT_PROJECTS), help="WorkBuddy 会话日志目录")
    ap.add_argument("--out", default="token-live-dashboard.html", help="输出 HTML 路径")
    ap.add_argument("--live", action="store_true", help="注入实时轮询脚本（需配合本地服务使用）")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    data = Collector(a.projects).scan()
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data, live=a.live), encoding="utf-8")
    s = summary(data)
    print("written: %s (%.1f KB)" % (out, out.stat().st_size / 1024))
    print("total: %s tokens | sessions: %s | models: %s | today: %s" %
          (s["totalTokens"], s["sessions"], s["models"], s["todayTokens"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
