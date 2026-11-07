---
name: token-live-monitor
description: 常驻后台统计 WorkBuddy 的 Token 消耗，并提供随时可打开的单文件离线实时看板。当用户想查看 token 用量、消耗统计、成本审计，或要求安装、配置、启动、停用开机自启的实时看板时使用，例如「看看我的 token 用量」「打开看板」「开启实时统计」「装一下 token 看板」。与「问一次才生成一张静态快照」的看板工具不同，本 skill 随 WorkBuddy 启动并在后台持续更新。
agent_created: true
---

# Token 实时看板（常驻）

## 用途

把 WorkBuddy 本机的请求级真实 usage 变成一份**持续更新的**单文件离线看板。图表模板改编自上游开源看板脚本（已内置在 `assets/`，不依赖外部 skill）；上游是「问一次才生成一次快照」，本 skill 随 WorkBuddy 启动、常驻后台增量采集，随时可看。

## 核心机制

1. **随启动**：`~/.workbuddy/settings.json` 的 `SessionStart` hook 在每次新会话启动时调用 `scripts/ctl.py ensure`。该命令幂等且极快——服务已在跑就直接返回，否则脱离终端拉起 `scripts/monitor.py`。登录自启动作为双保险（WorkBuddy 未打开时也统计）。
2. **后台统计**：`monitor.py` 每 `interval` 秒用文件指纹缓存增量扫描 `~/.workbuddy/projects/**/*.jsonl`，只重读变化过的文件；同时把 `stats.json` 与离线快照 `dashboard.html` 落盘。端口绑定天然保证单实例。
3. **随时查看**：本地 `127.0.0.1:<port>` 提供看板；新会话顶部会显示带地址和当日数字的提示；桌面有「Token Live Dashboard」按钮；在对话里说「看板」也可直接打开。

## 首次安装

执行 `scripts/install.py`（完整参数与手动配置见 `references/setup.md`，用户向的安装使用配置说明见 `README.md`）：

```
python <skill目录>/scripts/install.py
```

安装会写配置、合并 SessionStart hook（自动备份 settings.json）、注册登录自启动、创建桌面按钮并立即启动服务。重复执行安全，不会重复添加 hook。用 `--no-autostart` 可只随 WorkBuddy 启动；用 `--dry-run` 只预览改动。

## 日常使用

- **查看看板**：优先用结果展示工具打开 `http://127.0.0.1:8765/`（本地 URL 可直接预览）。不要重新运行生成器、不要重新扫描日志——数据由常驻进程维护。
- **汇报数字**：直接读 `~/.workbuddy/token-live/stats.json`，或请求 `/api/stats`。字段含 totalTokens、todayTokens、dailyAverage、sessions、turns、cacheHitRate、estimatedCostCny、topModels。
- **判断服务状态**：`python <skill目录>/scripts/ctl.py status`。
- **常用命令**：`start` / `stop` / `restart` / `open`（唤醒并打开浏览器）/ `once`（只采集一次、不开服务）/ `url` / `ensure`（hook 专用，输出纯 JSON）。
- **用户要「看板」但服务未启动**：执行 `ctl.py start`，再打开本地地址。

## 配置

运行时配置在 `~/.workbuddy/token-live/config.json`：`host`（默认 127.0.0.1）、`port`（默认 8765）、`interval`（默认 30 秒）、`projects`（默认 `~/.workbuddy/projects`）。改端口后执行 `ctl.py restart`，并同步更新桌面按钮与文档中的地址。环境变量 `TOKEN_LIVE_PORT` / `TOKEN_LIVE_INTERVAL` / `TOKEN_LIVE_PROJECTS` / `TOKEN_LIVE_HOME` 可临时覆盖。

价格表 `PRICE` 位于 `assets/dashboard_template.html` 的 `const PRICE={...}`，`dashboard.py` 会解析同一份数据计价；调整套餐价格时改这一处即可。汇报金额时说明这是按牌价的粗略预估。

## 卸载与停用

执行 `scripts/uninstall.py` 停止服务、移除 hook / 自启动 / 桌面按钮（保留数据与日志）；加 `--purge` 删除本 skill 自己创建的状态文件。配置备份保留为 `settings.json.bak.token-live-monitor`。

## 约束

- 仅绑定回环地址，仅用 Python 标准库；数据只在本机磁盘与 127.0.0.1 之间流动，不外发、不上传。
- 会话日志目录不存在时返回「暂无数据」页面，不报错崩溃。
- 端口被其他程序占用时改 `config.json` 的 `port` 后重启，不要强行结束占用进程。
- `ctl.py ensure` 的 stdout 必须是且仅是 hook JSON；调试信息写 `monitor.log`。
