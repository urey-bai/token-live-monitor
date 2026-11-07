# 深入配置与排障（供 Agent 参考）

面向需要手工配置、验证或修改本 skill 的场景。用户向的安装步骤以 `README.md` 为准，此处记录内部机制与不重复的操作清单。

## 1. 数据流

```
WorkBuddy 写入 ~/.workbuddy/projects/**/*.jsonl
        │  （文件指纹 = size + mtime_ns；只重读变化过的文件）
        ▼
monitor.py / Collector.scan()  ──►  DATA{genMs, ws, models, sess[]}
        │                              │
        │ 每 interval 秒               ├─► render(DATA, live=True) ──► GET / 单文件 HTML
        │                              ├─► summary(DATA) ──► GET /api/stats, stats.json
        │                              └─► dashboard.html（离线快照）
        ▼
~/.workbuddy/token-live/  （config.json / stats.json / dashboard.html / monitor.log / monitor.pid）
```

`dashboard.py` 的 `DATA` 结构与原 `token-dashboard/gen_dashboard.py` 完全一致，因此 `assets/dashboard_template.html` 可以原样复用其 44 KB 模板；模板内唯一的 `__DATA__` 占位符由 `render()` 替换。计价表 `const PRICE={...}` 也在模板内，由 `_load_price()` 用 `ast.literal_eval` 解析，保持单一数据源。

**会话标题三级优先级**（`Collector.scan()` 缓存项为 `(sig, folder, sid, title, rank, recs)`）：

| 级别 | 来源 | 说明 |
| --- | --- | --- |
| 3 | `aiTitle` | WorkBuddy 日志顶层字段，取文件里**首个非空值**；它是任务创建时生成的标题，最贴近「这是哪个任务」 |
| 2 | `<user_query>` | `session_title()` 用 `_USER_QUERY` 正则从 `input_text` / `text` 段里抽取真实提问，再用 `_clean_text()` 剥掉 `<system-reminder>` / `<task-notification>` 等内部标签 |
| 1 | 兜底标识 | `fallback_title()`：`工作空间短名 · MM-DD HH:MM · #sid前6位` |

`rank` 用于 `_assemble()` 合并同一 `sid` 的主会话与 `subagents/` 子代理日志时，保证主会话的 `aiTitle` 不被子代理的提问摘要覆盖。看板会话卡片显示 `#sid前8位`，hover 显示工作空间全名。注意：上游开源脚本的取标题逻辑是「见段首 `<` 即返回 None」，本 skill 已独立修复；模板与逻辑均已内置，本 skill 不依赖任何外部 skill，也不需要安装上游原版。

## 2. SessionStart hook 规范

WorkBuddy 桌面版的用户级 hook 配置位于 `~/.workbuddy/settings.json`（CodeBuddy CLI 为 `~/.codebuddy/settings.json`）。安装脚本幂等合并的结构：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "\"C:/.../python.exe\" \"C:/.../token-live-monitor/scripts/ctl.py\" ensure",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

要点：

- `matcher` 必须为 `startup`；同一会话只触发一次，切换/新建会话会再触发。
- hook 命令必须使用**有控制台的 `python.exe`**，不能用 `pythonw.exe`——WorkBuddy 需要读取 stdout 的 JSON。
- `ctl.py ensure` 的 stdout **只能有 JSON**：`{"continue": true, "systemMessage": "...", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}`。调试信息一律写 `monitor.log`。
- `ensure` 是幂等的：先请求 `/api/ping`，通了直接返回；不通才 `spawn_monitor()` 并最多等 3 秒。端口已被占用时 `monitor.py` 会自行退出，不会产生第二个实例。
- `systemMessage` 提供带地址和当日数字的可点提示；`additionalContext` 告诉 Agent：用户说「看板」时直接打开本地 URL、读 `stats.json`，不要重复生成看板。
- `install.py` 合并前把原配置备份为 `settings.json.bak.token-live-monitor`；重复安装会刷新命令路径（skill 搬家后有用），不会重复添加。

**手动配置**（不跑安装脚本时）：把上述 `hooks` 节点合并进 settings.json，然后执行 `python ctl.py start`。修改配置后需新建会话才会生效。

## 3. 登录自启动（双保险）

| 平台 | 文件 | 启动方式 |
| --- | --- | --- |
| Windows | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TokenLiveMonitor.vbs` | `WScript.Shell.Run` 以隐藏窗口调用 `pythonw.exe ctl.py start` |
| macOS | `~/Library/LaunchAgents/com.workbuddy.token-live-monitor.plist` | `RunAtLoad=true`，日志指向 `monitor.log` |
| Linux | `~/.config/autostart/token-live-monitor.desktop` | XDG autostart，`Exec="python" "ctl.py" start` |

Windows 下 VBS / `.lnk` 用 `pythonw.exe` 避免闪黑框；hook 用 `python.exe`。两者分工不要搞反。桌面 `.lnk` 的 Arguments 是 `"<ctl.py 绝对路径>" open`，即先唤醒服务再开浏览器；若 COM 创建失败会降级写 `.url`。

## 4. HTTP 接口

| 路径 | 返回 |
| --- | --- |
| `/` | 单文件 HTML 看板（注入实时轮询角标），`Cache-Control: no-store` |
| `/api/stats` | KPI：totalTokens / todayTokens / dailyAverage / sessions / turns / cacheHitRate / estimatedCostCny / topModels |
| `/api/ping` | `{ok, genMs, gen, rev, uptimeSec, lastError}`；`rev` 仅在用量数据指纹变化时递增，前端据此判断是否需要整页刷新（避免无意义刷新打断下钻） |
| `/api/data` | 完整 DATA（调试用） |
| `/health` | 纯文本 `ok`，供 `health()` 做单实例探测 |
| `/refresh` | 强制重采后 302 回 `/` |

服务使用 `ThreadingHTTPServer`，`daemon_threads=True`；`SIGTERM`/`SIGINT` 触发 `shutdown()` 并清理 pid 文件。

**前端状态（全在 localStorage，不上传）**：`td-win`（时间窗口，默认 `1`）、`td-cs` / `td-ce`（自定义区间起止分钟）、`td-skin`（界面皮肤，默认 `deepblue`）。`initWindow()` 在 `render()` 之前恢复窗口，`applySkin()` 在 `render()` 之前设置图表配色与 `body[data-skin]`，因此 `location.reload()` 后视图形态不变。皮肤共 5 套：`light` / `dark` 沿用原 `:root` 与 `body.dark`；`deepblue` / `cyber` / `glass` 为新增科技皮肤，各自覆盖 CSS 变量并使用 `MCT`+`RMPT`+`CJT` 或 `MCC`+`CJN` 图表配色。桌面图标由 `install.py` 指向 `assets/token-live.ico`，替换同名文件后重跑安装即可。

## 5. 排障清单

按顺序执行，定位到哪一步失败就停在哪一步：

```bash
python ctl.py status                 # running / pid / config / lastError
curl http://127.0.0.1:8765/health   # 端口是否通
curl http://127.0.0.1:8765/api/ping # rev 是否随新请求递增（不变=没有新用量）
curl http://127.0.0.1:8765/api/stats
python ctl.py once                  # 绕开服务，直接验证采集链路
python install.py --dry-run         # 检查 hook / 自启动 / 按钮的路径
python uninstall.py --dry-run       # 检查卸载会删哪些文件
```

- `monitor.log` 记录启动、端口冲突、每次采集异常与 HTTP 访问；超过 512 KB 自动保留最近 300 行。
- `lastError` 非空通常是 `projects` 目录不存在；修正 `config.json` 后访问 `/refresh` 或 `ctl.py restart`。
- 数字不涨：确认 `~/.workbuddy/projects` 下有新的 `.jsonl` 且记录带 `providerData.rawUsage / usage`；`Collector` 依赖 size/mtime 变化，若外部工具以保留 mtime 的方式改写文件，需 `ctl.py restart` 清缓存。
- 端口冲突：改 `config.json` 的 `port`，`ctl.py restart`，同时更新桌面 `.url` 快捷方式与用户文档中的地址。
- hook 不触发：确认 settings.json 的 `hooks.SessionStart[].matcher == "startup"`、命令路径存在、且用 `python.exe`；WorkBuddy 会缓存配置，需新建会话。

## 6. 升级与迁移

- skill 目录搬家后重跑 `install.py`：hook 命令、VBS、快捷方式都会刷新到新路径。
- 模板升级（例如上游 token-dashboard 出新图表）时，只需替换 `assets/dashboard_template.html`，保证文件中仍有唯一 `__DATA__` 占位符且 `const PRICE={...};` 保持 Python 字面量兼容（单引号键、数值数组）；`python dashboard.py --out /tmp/check.html` 验证即可。
- 卸载用 `uninstall.py`，它按文件清单逐个删除，不对用户目录做递归清空；`--purge` 只删五个已知状态文件。

## 7. 验收清单

- [ ] `python install.py` 后 `/health` 返回 ok，状态目录有 `stats.json` 与 `dashboard.html`
- [ ] 重复运行 `install.py`，settings.json 中只出现一条本 skill 的 hook
- [ ] 手动 `ctl.py stop` 后执行 `ctl.py ensure`，输出纯 JSON 且服务被重新拉起
- [ ] 浏览器打开 `/`，右下角出现「● 实时看板」且图表有数据
- [ ] 桌面按钮能打开看板
- [ ] 打开看板默认停在「今天 · 1 天」，右下角出现「◉ 实时看板」，主题为「深空蓝 · 科技」
- [ ] 切到「近 7 天」后整页刷新，窗口仍是 7 天；切到「赛博霓虹」后刷新，主题仍保留
- [ ] 无头浏览器/Node 校验内联 JS 无语法与运行时错误（改模板后必做）
- [ ] 桌面快捷方式的 `IconLocation` 指向 `assets/token-live.ico`
- [ ] `uninstall.py --dry-run` 列出的删除项均为本 skill 创建的文件
