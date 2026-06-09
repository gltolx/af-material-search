#!/usr/bin/env bash
# Codex 专属:给 Codex 注册 chrome-devtools MCP(收割小红书/抖音的浏览器桥),由 install.sh 在「检测到 ~/.codex」时调用。
# **只碰 Codex(~/.codex/config.toml),完全不碰任何 ~/.claude 路径** → Claude Code 零影响。
# 幂等:已注册就跳过,绝不覆盖用户手调过的参数。失败不阻塞主安装。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -d "$HOME/.codex" ] || { echo "  [跳过] 无 ~/.codex(本机没有 Codex 客户端)"; exit 0; }

# node/npx 是 MCP server 的运行时(chrome-devtools-mcp 是 npm 包)
if ! command -v npx >/dev/null 2>&1; then
  echo "  [警告] 没有 node/npx → chrome-devtools MCP 起不来。装 node(如 nvm)后重跑本脚本。"; exit 0
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "  [警告] 没有 codex CLI → 跳过 MCP 注册。"; exit 0
fi

CFG="$HOME/.codex/config.toml"
if grep -q '^\[mcp_servers\.chrome-devtools\]' "$CFG" 2>/dev/null; then
  echo "  [OK] chrome-devtools MCP 已注册(跳过;要改参数先删该段再重跑)"
else
  # 生产配置:--slim(navigate/evaluate/screenshot 三件套)+ --browserUrl attach「带调试端口的真 Chrome」
  # 不让 MCP 自己 launch 浏览器(那样 navigator.webdriver=true 易被判机器人);Chrome 由 codex_chrome.sh 正常启动
  if codex mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest --slim --browserUrl http://127.0.0.1:9222 >/dev/null 2>&1; then
    echo "  [已注册] chrome-devtools MCP(--slim --browserUrl http://127.0.0.1:9222)"
  else
    echo "  [警告] codex mcp add 失败,手动注册见 browser_harvest_codex.md"
  fi
fi

echo "  采集 Chrome 用法:收割前先 \`bash $ROOT/codex_chrome.sh\`(起带调试端口的专用 Chrome,首次登录三平台)"
echo "  收割 recipe:$ROOT/browser_harvest_codex.md(Codex 用 navigate+evaluate 驱动,JS 与 Claude 轨同源)"
exit 0
