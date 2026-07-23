# Windows 原生 Codex 兼容设计

日期：2026-07-23
状态：已获用户方向确认，待按本文实施

## 1. 背景

`af-material-search` 当前已在 macOS 上稳定运行，Linux 分支为 best-effort。主流程包含：

1. 自动同步代码与技能副本；
2. 安装并自检 Python、DouyinProcessor、xiaohongshu-cli、yt-dlp、ffmpeg、Pillow；
3. 启动带独立 profile 和远程调试端口的 Chrome；
4. 通过 Codex Chrome DevTools MCP 收割小红书与抖音；
5. 运行 Python 打分、筛选、出页、下载、清洗和入库脚本。

目前 Windows 原生 Codex 会在多个位置失败：

- 安装、自检、同步、更新和 Chrome 启动均为 Bash 脚本；
- 技能正文包含 Bash 环境变量、后台任务、`lsof`、`cat`、`curl` 等命令；
- uv tool、Python、yt-dlp、ffmpeg 和 Chrome 路径按 macOS/Linux 布局硬编码；
- Python 下载服务只支持 `open`、`xdg-open` 和 macOS `osascript` 文件夹选择器；
- 验证码强提醒依赖 Preview、AppleScript 和 macOS 声音；
- `AGENTS.md` 由符号链接生成，而 Windows 符号链接可能需要 Developer Mode 或管理员权限；
- `PATH` 拼接处存在写死 `:` 的 POSIX 假设。

目标是在 Windows 原生 PowerShell + Codex + Windows Chrome 下跑通同一套业务链，同时不改变现有 macOS/Linux Bash 入口的行为。

## 2. 目标

### 2.1 必须实现

- Windows 同事克隆仓库后，可通过单个 PowerShell 入口完成安装、技能同步、环境自检和 Codex 浏览器桥配置。
- Windows Codex 能按同一份 `/broll` 和 `/broll-auto` 技能执行完整流程。
- Windows 使用本机 Chrome 登录态、独立采集 profile、远程调试端口和 Chrome DevTools MCP。
- Python 业务脚本在 Windows 下正确发现可执行文件、默认下载目录、文件夹选择器和目录打开方式。
- macOS/Linux 原有 `.sh` 入口、Python 行为、数据契约和结果文件格式不变。
- Windows 与 POSIX 平台代码有明确边界，避免把 PowerShell 逻辑散落进业务脚本。
- 所有修改通过现有 macOS 回归测试和跨平台单元测试后才提交实现代码；真实 Windows 冒烟通过前不宣称 Windows 链路已生产可用。

### 2.2 非目标

- 不通过 WSL2 或 Git Bash 运行。
- 不重写现有搜索、打分、筛选、下载、清洗或入库业务。
- 不更换 Chrome DevTools MCP、不新增 Playwright 等浏览器框架。
- 不实现新的 Cookie 导出系统；继续使用 yt-dlp 的 `--cookies-from-browser chrome`。
- 不自动处理验证码。
- 不承诺解决企业策略导致的 Chrome Cookie 解密限制，只在自检中明确报告。
- 不借本次兼容改造重构无关业务代码。

## 3. 总体架构

采用“平台入口隔离、业务代码复用、平台能力门面化”的结构：

```text
macOS / Linux                         Windows
install.sh                            windows/install.ps1
setup.sh                              windows/broll.ps1 setup
preflight.sh                          windows/broll.ps1 preflight
selfupdate.sh                         windows/broll.ps1 selfupdate
codex_chrome.sh                       windows/broll.ps1 chrome
        \                                  /
         \                                /
          +------ broll_platform --------+
                  common.py
                  posix.py
                  windows.py
                        |
                现有 Python 业务脚本
```

设计原则：

1. **入口隔离**：Windows 管理代码全部放在 `windows/`；现有 `.sh` 文件不改成多平台大杂烩。
2. **平台能力隔离**：新增 `broll_platform` 小包，Windows 与 POSIX 的路径、文件夹和 Chrome 能力分别实现。
3. **业务代码窄改**：现有 Python 文件只把硬编码路径或 OS 分支替换为门面函数调用，不移动核心业务。
4. **数据契约不变**：`results/` 下所有 JSON、HTML、sidecar 和 manifest 字段保持不变。
5. **环境变量优先**：用户显式配置始终覆盖自动发现，便于特殊机器修复。

## 4. 文件与职责

### 4.1 Windows 管理入口

新增：

- `windows/install.ps1`
  - 最薄的一键入口；
  - 检查 `py -3` / `python`；
  - Python 缺失时优先用 `winget install --id Python.Python.3.12` 安装 Python 3.12，失败则给出明确人工命令；
  - 调用 `windows/broll.ps1 install`。

- `windows/broll.ps1`
  - 单一 PowerShell 管理入口；
  - 支持 `install`、`setup`、`preflight`、`sync`、`selfupdate`、`chrome`、`setup-browser`、`download-server`、`serve`、`check`；
  - 子命令之间复用函数，不为每个动作复制独立脚本；
  - `check` 仅做语法、路径和配置检查，不安装、不联网、不启动长期进程，供冒烟测试使用。

- `windows/smoke.ps1`
  - 在真实 Windows 上执行非破坏性冒烟；
  - 使用临时结果目录；
  - 验证安装幂等、中文/空格路径、技能同步、Chrome 健康检查、MCP 配置、server sidecar、HTML 生成、文件夹能力和二进制子进程；
  - 不下载真实大文件、不清理用户 Chrome、不 kill 任何已有进程。

### 4.2 平台能力包

新增：

- `broll_platform/__init__.py`
  - 按 `os.name` / `sys.platform` 选择 Windows 或 POSIX 实现；
  - 对业务脚本暴露稳定门面。

- `broll_platform/common.py`
  - 可执行文件候选排序；
  - 环境变量覆盖；
  - 安全路径标准化；
  - 可注入的纯函数，便于在 macOS 上模拟 Windows 路径测试。

- `broll_platform/posix.py`
  - 保留当前 macOS/Linux 路径和行为；
  - macOS `open`、`osascript`；
  - Linux `xdg-open`；
  - `~/Library/Python/3.*/bin`、`~/.local/bin` 和 uv POSIX tool 布局。

- `broll_platform/windows.py`
  - Windows 可执行文件扩展名和候选目录；
  - uv tool 的 `Scripts/python.exe` / `Scripts/xhs.exe`；
  - `%APPDATA%`、`%LOCALAPPDATA%`、Python 用户 Scripts 和 `~\.local\bin`；
  - Chrome 的 `%LOCALAPPDATA%`、`%ProgramFiles%`、`%ProgramFiles(x86)%` 候选；
  - Windows Known Folder Downloads，失败时回退 `~/Downloads`；
  - `os.startfile()` 打开目录；
  - PowerShell STA + `System.Windows.Forms.FolderBrowserDialog` 选择文件夹。

门面至少提供：

```python
find_python()
find_douyin_python()
find_xhs()
find_ytdlp()
find_ytdlp_new()
find_ffmpeg()
find_chrome()
default_downloads_dir()
open_folder(path)
pick_folder(prompt, initial_dir)
prepend_path(env, directory)
```

发现顺序统一为：

1. 对应环境变量；
2. 当前 `PATH`；
3. uv 实际或常见 tool 目录；
4. 平台候选路径；
5. 返回具名缺失状态，由 preflight 报告。

禁止无边界递归扫描整个用户目录。

### 4.3 现有 Python 脚本的窄改范围

通过全仓硬编码审计确认实际调用点后，只修改需要平台能力的文件。已知至少包括：

- `download_server.py`
  - yt-dlp、yt-dlp-new、ffmpeg 发现；
  - `os.pathsep`；
  - 默认下载目录；
  - Windows 文件夹选择与目录打开；
  - 保留现有 macOS/Linux 分支；
  - 不改下载、清洗、manifest、selection state 等业务逻辑。

- `harvest_net.py`
  - yt-dlp 发现。

- `harvest_xhs_patient.py`
  - xhs 发现。

- `backfill_duration.py`
  - yt-dlp 发现。

- `extract_early_frames.py`
  - ffmpeg 发现。

- 其他经 `rg` 审计确认存在运行时硬编码的实际链路脚本。

- `captcha_alert.py` / `captcha_notify.py`
  - Windows 打开截图、提示音、模态提示和 Chrome 激活；
  - POSIX 原实现保留；
  - Windows 无法精确聚焦标签页时退化为激活专用 Chrome 窗口，不影响人工处理。

不得把 PowerShell 命令字符串散落到多个业务脚本；Windows UI 能力集中在 `broll_platform/windows.py`。

## 5. Windows 安装与自检

### 5.1 安装

`windows/broll.ps1 install` 按以下顺序执行：

1. 定位仓库根目录。
2. 安全生成或更新本机 `AGENTS.md`：
   - 不创建符号链接；
   - 若不存在，创建一份带管理标记的短文件，要求 Codex 读取并遵循同目录 `CLAUDE.md`；
   - 若是本工具带管理标记的旧副本，可幂等更新；
   - 若已存在且不是本工具管理的副本，不覆盖。
3. 把盖好真实 `BROLL_HOME` 的技能模板同步到存在的：
   - `%USERPROFILE%\.codex\skills`
   - `%USERPROFILE%\.agents\skills`
   - `%USERPROFILE%\.claude\skills`（若存在）
4. 安装缺失环境：
   - DouyinProcessor tool venv；
   - xiaohongshu-cli tool venv；
   - yt-dlp；
   - ffmpeg；
   - Pillow。
5. 配置 Codex Chrome DevTools MCP。
6. 运行 Windows preflight。

安装保持幂等；已有可用组件不重装。uv 仅在 tool venv 缺失时作 bootstrap，日常运行不依赖 `uvx`。

### 5.2 ffmpeg

Windows 优先复用 `PATH` 中的 ffmpeg。缺失时通过当前 system Python 的 user site 安装 `imageio-ffmpeg`，使用 `imageio_ffmpeg.get_ffmpeg_exe()` 返回的静态 ffmpeg；运行时发现模块把该路径作为 Windows 的最后一个候选，不修改系统 PATH。

安装后必须对返回的二进制执行 `-version` 验证。不能要求管理员权限，也不能修改系统级 PATH。用户通过 `FFMPEG` 指定的二进制始终优先于 `imageio-ffmpeg`。

### 5.3 preflight

Windows preflight 对齐现有检查语义：

- 真 Python 与 pip；
- DouyinProcessor + requests；
- xhs 存在与登录态；
- yt-dlp；
- ffmpeg；
- system Python Pillow；
- Chrome 本体；
- Chrome Cookie 数据库候选；
- B站 API 网络；
- skill 副本漂移；
- 端口占用只告警、不 kill。

PowerShell 使用 `Get-NetTCPConnection`，不可用时退到 `Test-NetConnection` 或 socket 探测。端口占用不算红灯，因为 server 会自动顺延。

Cookie 数据库存在不代表一定可解密；preflight 必须区分“文件缺失”和“yt-dlp 实测读取失败”。

## 6. Windows Chrome 与 MCP 闭环

Windows Chrome 使用独立 profile，默认：

```text
%LOCALAPPDATA%\af-material-search\chrome-profile
```

启动参数与现有链路一致：

```text
--remote-debugging-port=9222
--remote-debugging-address=127.0.0.1
--user-data-dir=<独立 profile>
--no-first-run
--no-default-browser-check
```

要求：

- 先请求 `http://127.0.0.1:9222/json/version`，已健康则复用；
- 通过 `Start-Process` 启动真实 Chrome，不添加自动化 flag；
- 若 profile 仍被 Chrome 使用且端口不健康，只告警用户关闭该专用窗口，不强杀进程、不盲删锁；
- 支持 `CODEX_HARVEST_PORT`、`CODEX_HARVEST_PROFILE`、`CODEX_HARVEST_PROXY`、`CHROME_BIN`；
- 启动后轮询 `/json/version`，超时给具名错误；
- 提醒首次登录小红书、抖音、B站、YouTube；
- 提醒在浏览器内部验证国内出口。

MCP 配置保持当前目标参数：

```text
chrome-devtools-mcp@1.4.0
--slim
--browserUrl http://127.0.0.1:9222
--no-usage-statistics
--no-performance-crux
```

若已有错误配置：

1. 备份 `%USERPROFILE%\.codex\config.toml`；
2. 调用 `codex mcp remove` / `codex mcp add`；
3. 失败时恢复备份；
4. 不修改任何 Claude 配置。

## 7. 技能正文的 OS 分流

`skills/broll/SKILL.md` 和 `skills/broll-auto/SKILL.md` 不能只在安装段写 Windows 命令，而要覆盖整个流程：

- selfupdate；
- preflight 与 setup；
- Chrome 启动；
- 环境变量写法；
- Python 选择；
- server 后台启动；
- 端口健康确认；
- HTTP 静态页启动；
- 下载、清洗和 autorun；
- 多会话 `BROLL_RES`；
- 验证码提醒。

技能先判断宿主：

- Windows PowerShell：调用 `windows/broll.ps1`；
- macOS/Linux：继续调用现有 `.sh` 和现有命令。

Windows 不在正文中手拼 uv venv 绝对路径；由 PowerShell 管理入口和平台发现模块解析。两平台的数据文件名、端口 sidecar 和 HTTP 接口完全相同。

## 8. 错误处理

- Python 缺失且 winget 不可用：停止安装并给出官方 Python 安装指引。
- 某个依赖安装失败：报告准确组件、执行命令和日志尾部，不继续伪装全绿。
- Chrome 缺失：preflight 红灯；不自动安装浏览器。
- Chrome Cookie 不可读：登录态类告警或打断，保持现有 `/broll` 与 `/broll-auto` 各自纪律。
- MCP 修复失败：恢复备份，报告手动命令。
- 端口占用：允许自动顺延，不 kill 任何进程。
- 文件夹选择取消：返回空字符串，前端保留原目录。
- Windows Known Folder 解析失败：回退 `%USERPROFILE%\Downloads`。
- Windows 验证码窗口聚焦失败：保留队列、声音和弹窗，用户手动切回专用 Chrome。

## 9. 测试策略

### 9.1 跨平台单元测试

新增纯 Python 测试，使用临时目录和注入环境模拟 Windows：

- `.exe` / `.cmd` 发现；
- uv `Scripts` 布局；
- 环境变量优先级；
- 含空格和中文路径；
- Windows Known Folder 回退；
- `os.pathsep`；
- Chrome 路径候选；
- 文件夹打开/选择调用分支；
- POSIX 候选顺序保持现状。

### 9.2 PowerShell 测试

- `windows/broll.ps1 check` 在无副作用模式验证参数、目录、Python 和脚本可加载；
- 使用临时 `$HOME` / `$env:USERPROFILE` 验证 skill 同步与幂等；
- 模拟 `codex`、Chrome 和 HTTP 健康端点，验证 MCP 备份/恢复；
- 所有测试不得修改真实 Codex 配置。

### 9.3 macOS/Linux 回归

在当前 macOS 环境：

1. 运行全部现有 `tests/test_*.py`，按项目既有解释器分工选择 system Python 或 douyin venv；
2. 运行新增平台测试；
3. 运行 `bash ./preflight.sh`；
4. 验证现有 `.sh` 入口文本和行为未变；
5. 检查 `git diff`，确保没有覆盖用户现有未提交业务改动。

### 9.4 真实 Windows 冒烟

`windows/smoke.ps1` 至少验证：

- `install` 连续执行两次结果一致；
- 仓库和结果目录包含空格、中文；
- skill 副本已盖真实 Windows 路径；
- `AGENTS.md` 不依赖符号链接；
- Chrome `/json/version` 健康；
- MCP attach 参数正确；
- preflight 能识别 Chrome Cookie、Pillow、yt-dlp、ffmpeg、DouyinProcessor；
- writer/download server 启动并写 `.writerport` / `.dlport`；
- `filtered.html` 可通过本机 HTTP 打开；
- 文件夹选择和目录打开可用；
- `yt-dlp --version` 与 `ffmpeg -version` 子进程成功；
- 临时进程和临时目录可安全清理，绝不影响已有 Chrome 或其他会话。

首次真实 Windows 冒烟需要同事手工完成专用 Chrome 登录；验证码仍由本人处理。

当前开发环境是 macOS，不能把路径模拟等同于真实 Windows 验证。因此实现提交可以在 macOS 回归和跨平台单元测试全部通过后创建，但交付状态必须明确标记为“待真实 Windows 冒烟”；只有同事电脑上的 `windows/smoke.ps1` 通过后，才把 Windows 支持标记为生产可用。

## 10. 兼容与提交纪律

- 当前工作树已有未提交业务修改，实施必须使用窄补丁，逐文件检查上下文，不覆盖或格式化无关区域。
- Windows 专属实现放在 `windows/` 和 `broll_platform/windows.py`。
- POSIX 专属实现放在 `broll_platform/posix.py`，现有 `.sh` 保持原入口。
- 共享模块只放纯路径和能力门面，不吸收业务规则。
- 不改任何 JSON schema、HTML endpoint、端口 sidecar 名或 manifest 字段。
- 实现完成后先运行全部测试和 preflight，检查 diff；全部通过后才创建实现提交。
- 设计文档单独提交，便于审计；实现提交不夹带现有用户改动。

## 11. 验收标准

以下条件同时满足才算完成：

1. Windows 原生 Codex 可安装并加载两份技能。
2. Windows 专用 Chrome 与 MCP attach 闭环可用。
3. Windows 能运行搜割后的 Python 下游、出页、下载与清洗服务。
4. Windows 文件路径、文件夹选择、目录打开和可执行文件发现正确。
5. macOS 当前 preflight 和现有测试无回归。
6. 实现 diff 未覆盖当前工作树的既有改动。
7. 提供真实 Windows 冒烟入口和清晰使用说明。
