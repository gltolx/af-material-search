# Claude Code + Codex /broll 兼容设计

## 背景

`af-material-search` 的核心流水线已经能复用:口播稿解析、四平台收割、语义判分、三色筛选、自动选片、下载、清洗、入库都由 repo 内 Bash/Python 脚本承担。当前缺口不在业务流水线,而在运行时入口:

- 已安装到 `~/.codex/skills` 的 `broll` / `broll-auto` skill 副本已经落后于 repo 模板。
- Codex 的 `chrome-devtools` MCP 配置可能自己启动自动化 Chrome,而不是 attach 到 `codex_chrome.sh` 启动的专用采集 Chrome。
- `SKILL.md`、`CLAUDE.md`、`USAGE-*` 仍有 Claude 专有表述和过期细节,容易误导 Codex。
- 兼容层缺少轻量自动化测试,后续改动容易回归。

目标是让用户在 **Claude Code 本机版** 和 **Codex 本机版** 中都能用同样的命令 `broll` / `broll-auto` 启动素材搜索,并保持“轻方案”:不重写爬虫,不引入重框架,不做云端 runner。

## 目标

1. `broll` 和 `broll-auto` 的 skill 模板以 repo 内 `skills/*/SKILL.md` 为单一来源,安装副本不会静默漂移。
2. Claude Code 与 Codex 只在浏览器驱动层分叉,下游产物文件名和 schema 完全一致。
3. Codex 浏览器轨必须 attach 到 `127.0.0.1:9222` 的专用真人 Chrome profile,不能退回 MCP 默认自动化 Chrome。
4. 项目契约改成 agent-neutral:Claude Code 和 Codex 都读得懂,运行时差异放在明确的适配层里。
5. 新增最小可信测试矩阵,覆盖安装、同步、MCP 配置、Chrome 启动和 schema 同构,不跑真实重爬。

## 非目标

- 不支持 claude.ai 网页、Claude 桌面云端、或任何碰不到本机 Chrome/下载目录的远程运行时。
- 不重写 `harvest_net.py`、`merge_scored.py`、`score_candidates.py`、`apply_verdicts.py`、`download_server.py`、`autorun_kb.py`。
- 不把小红书/抖音收割迁回 CLI 主路。
- 不引入 Playwright 常驻服务、后台守护、签名爬虫或新运行时框架。

## 方案概览

采用“共享流水线 + 运行时适配层”。

| 层 | Claude Code | Codex | 共同契约 |
|---|---|---|---|
| skill 命令 | `broll`, `broll-auto` | `broll`, `broll-auto` | repo 模板盖章安装到客户端 skill 目录 |
| 项目契约 | 读 `CLAUDE.md` | 读 `AGENTS.md -> CLAUDE.md` | 内容 agent-neutral |
| 浏览器驱动 | Claude-in-Chrome | Chrome DevTools MCP | 只负责小红书/抖音 DOM 收割 |
| Chrome profile | 日常 Chrome/插件连接态 | `~/.broll-harvest-chrome` 专用采集 Chrome | 人工登录,不自动过验证码 |
| 落盘方式 | `writer_server.py` loopback | `evaluate` 返回后写文件 | `dy_raw.json`, `xhs_raw.json`, `xhs_imgs.json` schema 一致 |
| 下游脚本 | 共享 | 共享 | `BROLL_RES` 隔离、动态端口、Python 选择铁律一致 |

## 设计细节

### 1. Skill 同步

新增单一同步入口 `sync_skills.sh`。

职责:

- 校验 repo 内必须存在 `skills/broll/SKILL.md` 和 `skills/broll-auto/SKILL.md`。
- 将 `{{BROLL_HOME}}` 替换为 repo 绝对路径,写入存在的 `~/.claude/skills/<name>/SKILL.md` 和 `~/.codex/skills/<name>/SKILL.md`。
- 替换路径时处理空格、`&`、反斜杠等 shell/sed 特殊字符。
- 输出每个目标的 installed / updated / skipped 状态。
- 若没有任何客户端目录,黄灯提示安装 Claude Code 或 Codex 后重跑,但不破坏 repo。

`install.sh` 改为调用 `sync_skills.sh`,并把成功文案改为 `broll/broll-auto` 双命令已安装。

`selfupdate.sh` 改为无论代码是否快进都调用 `sync_skills.sh`:

- 代码已最新:刷新 skill 副本。
- ff-only 成功:刷新 skill 副本。
- 离线、无 origin、非 git、dirty 无法快进:不阻塞流水线,仍用本地 repo 模板刷新 skill 副本。

`preflight.sh` 增加轻量漂移检查:

- 对 repo 模板盖章后与已安装副本做 hash 比较。
- 不一致时报黄灯,提示运行 `bash ./sync_skills.sh` 或 `bash ./install.sh`。

### 2. Codex Chrome 轨

`codex_chrome.sh` 负责启动和复用真人采集 Chrome:

- 默认端口 `9222`,默认 profile `~/.broll-harvest-chrome`。
- macOS 上优先使用 `open -na "Google Chrome" --args ...`,避免 shell 退出后 Chrome 被带走。
- 启动前若发现 profile 里有陈旧 `SingletonCookie` / `SingletonLock` / `SingletonSocket`,且没有对应 `.broll-harvest-chrome` Chrome 进程,清理后再启动。
- 健康检查只认 `http://127.0.0.1:9222/json/version`,根路径 `/` 不作为用户可见页面。
- 若端口已通,进一步提示这是 DevTools endpoint,用户应看弹出的 Google Chrome 窗口,不要用 Codex 内置浏览器打开 `9222`。

`setup_codex_browser.sh` 负责校验 Codex MCP:

- 没有 `~/.codex`、没有 `npx`、没有 `codex` 时只提示,不影响 Claude Code。
- 没有 `[mcp_servers.chrome-devtools]` 时注册:
  `codex mcp add chrome-devtools -- npx -y chrome-devtools-mcp@1.4.0 --slim --browserUrl http://127.0.0.1:9222 --no-usage-statistics --no-performance-crux`
- 已有配置时检查 `--browserUrl`、`http://127.0.0.1:9222`、`--slim` 是否存在。
- 如果已有配置不符合目标,给出明确修复提示或用安全的配置更新路径修正,不能静默跳过。

Codex 验收以 MCP evaluate 为准:

- `navigator.webdriver` 应为 false 或至少不表现为 MCP 默认自动化 profile。
- `fetch('https://myip.ipip.net').then(r => r.text())` 在采集 Chrome 内确认出口为中国大陆。

### 3. Skill 文案和项目契约

`skills/broll/SKILL.md` 保留核心流程,新增“运行时适配层”小节:

- Claude Code:使用 Claude-in-Chrome,小红书/抖音 DOM 收割通过 `writer_server.py` 落盘。
- Codex:使用 `codex_chrome.sh` + Chrome DevTools MCP,小红书/抖音 DOM 收割通过 `evaluate` 返回后写入同名 raw 文件。
- 两边必须产出同名同 schema 文件,之后 `merge_scored.py`、`score_candidates.py`、`apply_verdicts.py`、下载、清洗、入库完全共享。

文案调整:

- 将 `AskUserQuestion` 改为“暂停并向用户提问/等待用户确认”。
- 将“Claude 绝不自动解验证码”改为“agent 绝不自动解验证码”;`broll` 停下交用户,`broll-auto` 时间盒降级/跳过并记日志。
- 将“多开 Claude”改为“多会话/多运行时”。
- Codex 相关说明从旁注升级为正式适配层,不再让 Codex 读起来像二等路径。

`skills/broll-auto/SKILL.md` 只保留继承和覆盖:

- 阶段一继承 `/broll`。
- 覆盖打断策略:不等待用户登录/过码,能自动恢复则继续,不能恢复则降级/跳过并记日志。
- 阶段二追加 `autorun_selected.json`、`kb_routing.json`、`autorun_kb.py`。

`CLAUDE.md` 继续作为 Claude Code 入口文件,同时通过 `AGENTS.md` 软链给 Codex 读取。内容改成项目契约而非 Claude 专用说明:

- 保留同步/自检、Python 选择、uv 禁令、数据契约索引。
- 删除或弱化动态端口、下载命名、悬浮播放等易漂移细节,改为指向 `skills/broll/SKILL.md`。
- 修正“7 步流水线”等过期表述。

`USAGE-Claude.md` 和 `USAGE-Codex.md` 只放用户准备步骤:

- Claude Code:本机 Claude Code、Claude-in-Chrome、登录小红书/B站/抖音/YouTube。
- Codex:Codex CLI、node/npx、`codex_chrome.sh`、Chrome DevTools MCP、登录小红书/B站/抖音/YouTube。

### 4. 测试和验收

新增轻量测试,不跑真实平台:

- `tests/test_sync_skills.py`:临时 HOME 下验证双客户端 skill 盖章、路径替换、缺模板失败、只装单客户端时行为正确。
- `tests/test_selfupdate_safe.py`:本地临时 git/bare origin 验证 selfupdate 不阻塞且刷新 skill。
- `tests/test_setup_codex_browser.py`:fake `codex`/`npx` 验证 MCP 注册和已有错误配置检测。
- `tests/test_codex_chrome.py`:fake Chrome/curl/open 验证启动参数、proxy 参数、端口复用、陈旧 Singleton 处理。
- `tests/test_browser_schema_compat.py`:用模拟 `dy_raw.json`/`xhs_raw.json` 验证 Codex/Claude raw 产物下游 schema 同构。

手工 smoke:

1. `bash ./install.sh`
2. `bash ./selfupdate.sh`
3. `bash ./preflight.sh`
4. `bash ./codex_chrome.sh`
5. `curl http://127.0.0.1:9222/json/version`
6. Codex MCP evaluate `navigator.webdriver`
7. Codex MCP evaluate `fetch('https://myip.ipip.net').then(r=>r.text())`
8. 打开小红书/抖音搜索页做一次 render 探活和验证码检测,不做全量重爬。

## 风险和防线

- 高风险:Codex MCP 配置漂移。防线:安装时检查 `--browserUrl`/`--slim`,测试覆盖错误配置。
- 高风险:skill 副本漂移。防线:`sync_skills.sh` 单一入口,`selfupdate.sh` 每次刷新,`preflight.sh` 黄灯。
- 高风险:浏览器 raw schema 分叉。防线:Codex/Claude adapter 后的文件名和字段契约写进 skill,并用 fixture 测试。
- 中风险:Chrome profile/端口复用错误。防线:`codex_chrome.sh` 检查 profile、清理陈旧 Singleton、只认 `/json/version`。
- 中风险:Python 环境混用。防线:保留现有铁律,测试入口按 system python 与 douyin venv python 分组。

## 验收标准

- 同一 repo 下 Claude Code 和 Codex 都能发现 `broll` / `broll-auto` skill。
- 已安装 skill 副本与 repo 模板盖章结果一致。
- Codex `chrome-devtools` MCP attach 到 `127.0.0.1:9222`,不再默认启动自动化 Chrome profile。
- `codex_chrome.sh` 启动后 `curl http://127.0.0.1:9222/json/version` 稳定返回。
- `broll` / `broll-auto` 文案无 Claude 专有工具名误导 Codex。
- 新增轻测试通过,现有业务测试不回归。
