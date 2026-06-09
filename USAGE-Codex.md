# /broll 使用说明 · Codex 用户

> Codex 是本机 CLI,能跑这个技能。它没有 Claude-in-Chrome,小红书/抖音收割改用 **Chrome DevTools MCP** 挂一个专用采集 Chrome(install.sh 自动配好)。

## 前置
- macOS + Google Chrome
- Codex CLI(`codex --version`)+ 你的 Codex 订阅
- **node / npx**(MCP 运行时;`npx --version` 有即可)

## 安装(一次)
```bash
git clone https://github.com/gltolx/af-material-search.git
cd af-material-search
xcode-select --install      # 仅全新 Mac 需要
bash install.sh             # 装技能到 ~/.codex/skills + 建 AGENTS.md + 装环境 + 注册 chrome-devtools MCP + 自检
```
看到 `✅ READY` 即可。

## 每次开工(收割要用)
```bash
bash codex_chrome.sh        # 起专用采集 Chrome(独立窗口,带调试端口;不动你日常 Chrome)
```
- **首次**:在弹出的窗口登录 **小红书 / B站 / 抖音**(登录态持久存,后续复用)。
- **确认走国内节点**:收割前让 Codex 跑一句 `evaluate fetch('https://myip.ipip.net')`,地域要在中国大陆;不在就设 `CODEX_HARVEST_PROXY` 指到落地大陆的代理后重跑 `codex_chrome.sh`。

## 用
1. `cd` 进 `af-material-search`,打开 Codex(`codex`)。
2. 丢稿子,说「**用 /broll**」(或 `$broll`)。收割走采集 Chrome 的 MCP,其余(打分/出页/下载)与 Claude 端一致。
3. **人工**:选片勾选;撞验证码时**在那个采集 Chrome 窗口里手动过**,过完告诉 Codex 继续(它绝不自动解)。

## 缺什么 / 注意
- 收割节奏 ≥8s/词(抖音 ≥10s),避验证码。
- 采集 Chrome 必须先 `codex_chrome.sh` 起着,Codex 才连得上(MCP 配的是 `--browserUrl http://127.0.0.1:9222`)。
- 下载/打分/出页全平台可用(含小红书/抖音,只要收割时拿到了带 token 的页)。

## 更新
每次用 `/broll` 自动 `git pull`。要手动:`git pull && bash install.sh`。
