#!/usr/bin/env bash
# Codex 专属:给 Codex 注册 chrome-devtools MCP(收割小红书/抖音的浏览器桥),由 install.sh 在「检测到 ~/.codex」时调用。
# **只碰 Codex(~/.codex/config.toml),完全不碰任何 ~/.claude 路径** → Claude Code 零影响。
# 幂等:正确配置就跳过;错误配置给出修复路径。失败不阻塞主安装。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -d "$HOME/.codex" ] || { echo "  [跳过] 无 ~/.codex(本机没有 Codex 客户端)"; exit 0; }
TARGET_PKG="chrome-devtools-mcp@1.4.0"
TARGET_URL="http://127.0.0.1:9222"
TARGET_ARGS=(npx -y "$TARGET_PKG" --slim --browserUrl "$TARGET_URL" --no-usage-statistics --no-performance-crux)

chrome_devtools_section() {
  awk '
    /^\[mcp_servers\.chrome-devtools\]$/ { in_section=1; print; next }
    /^\[/ && in_section { exit }
    in_section { print }
  ' "$CFG" 2>/dev/null || true
}

has_required_config() {
  local section="$1"
  local needle
  for needle in "$TARGET_PKG" "--slim" "--browserUrl" "$TARGET_URL" "--no-usage-statistics" "--no-performance-crux"; do
    case "$section" in
      *"$needle"*) ;;
      *) return 1 ;;
    esac
  done
  return 0
}

# node/npx 是 MCP server 的运行时(chrome-devtools-mcp 是 npm 包)
if ! command -v npx >/dev/null 2>&1; then
  echo "  [警告] 没有 node/npx → chrome-devtools MCP 起不来。装 node(如 nvm)后重跑本脚本。"; exit 0
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "  [警告] 没有 codex CLI → 跳过 MCP 注册。"; exit 0
fi

CFG="$HOME/.codex/config.toml"
SECTION="$(chrome_devtools_section)"
if [ -n "$SECTION" ]; then
  if has_required_config "$SECTION"; then
    echo "  [OK] chrome-devtools MCP 已符合 Codex 采集轨配置"
  else
    echo "  [警告] chrome-devtools MCP 已存在但未 attach 到 $TARGET_URL"
    BAK="$CFG.bak.$(date +%Y%m%d%H%M%S)"
    if ! cp "$CFG" "$BAK" 2>/dev/null; then
      echo "  [警告] 无法备份 $CFG,为避免破坏用户配置,跳过自动修复。"
      echo "      请手动备份后运行: codex mcp remove chrome-devtools && codex mcp add chrome-devtools -- ${TARGET_ARGS[*]}"
    else
      echo "  [备份] $BAK"
      if codex mcp remove chrome-devtools >/dev/null 2>&1; then
        if codex mcp add chrome-devtools -- "${TARGET_ARGS[@]}" >/dev/null 2>&1; then
          echo "  [已修复] chrome-devtools MCP(--slim --browserUrl $TARGET_URL)"
          echo "  [提示] 当前 Codex 会话可能仍加载旧 MCP,请重启 Codex 会话后使用。"
        else
          cp "$BAK" "$CFG" 2>/dev/null || true
          echo "  [警告] 自动重加失败,已尽量从备份恢复原配置。请手动运行: codex mcp remove chrome-devtools && codex mcp add chrome-devtools -- ${TARGET_ARGS[*]}"
        fi
      else
        echo "  [警告] 无法自动移除旧配置,请手动删除 [mcp_servers.chrome-devtools] 后运行:"
        echo "      codex mcp add chrome-devtools -- ${TARGET_ARGS[*]}"
      fi
    fi
  fi
else
  # 生产配置:--slim(navigate/evaluate/screenshot 三件套)+ --browserUrl attach「带调试端口的真 Chrome」
  # 不让 MCP 自己 launch 浏览器(那样 navigator.webdriver=true 易被判机器人);Chrome 由 codex_chrome.sh 正常启动
  if codex mcp add chrome-devtools -- "${TARGET_ARGS[@]}" >/dev/null 2>&1; then
    echo "  [已注册] chrome-devtools MCP(--slim --browserUrl $TARGET_URL)"
  else
    echo "  [警告] codex mcp add 失败,手动注册见 browser_harvest_codex.md"
  fi
fi

echo "  采集 Chrome 用法:收割前先 \`bash $ROOT/codex_chrome.sh\`(起带调试端口的专用 Chrome,首次登录三平台)"
echo "  收割 recipe:$ROOT/browser_harvest_codex.md(Codex 用 navigate+evaluate 驱动,JS 与 Claude 轨同源)"
exit 0
