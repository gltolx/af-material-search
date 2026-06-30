#!/usr/bin/env bash
# 起一个「专用采集 Chrome」实例:带远程调试端口 + 持久独立 profile,供 Codex 的 chrome-devtools MCP 用 --browserUrl attach。
# 仅 Codex 轨用(Claude Code 走 Claude-in-Chrome,不需要它)。**绝不碰你日常 Chrome 的 profile**(独立 user-data-dir)。
# 用法:
#   bash codex_chrome.sh           # 起采集 Chrome(默认端口 9222、profile ~/.broll-harvest-chrome);已起则复用
#   首次起来后,在该窗口里登录 小红书/抖音/B站 各一次(登录态持久存进这个 profile,后续复用)
#   可覆盖:CODEX_HARVEST_PORT / CODEX_HARVEST_PROFILE / CODEX_HARVEST_PROXY(指到落地大陆的代理,如 http://127.0.0.1:7890)
set -u
PORT="${CODEX_HARVEST_PORT:-9222}"
PROFILE="${CODEX_HARVEST_PROFILE:-$HOME/.broll-harvest-chrome}"
PROXY="${CODEX_HARVEST_PROXY:-}"
CHROME="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -x "$CHROME" ] || { echo "  [失败] 未找到 Chrome:$CHROME(用 CHROME_BIN 指定)"; exit 1; }
mkdir -p "$PROFILE"
DEBUG_URL="http://127.0.0.1:$PORT/json/version"

debug_port_up() {
  curl -s --max-time 2 "$DEBUG_URL" >/dev/null 2>&1
}

# 已在监听调试端口 → 采集 Chrome 已起,直接复用(幂等)
if debug_port_up; then
  echo "  [OK] 采集 Chrome 已在 127.0.0.1:$PORT(复用);健康检查 $DEBUG_URL;要重登录就在那个窗口里操作"
  exit 0
fi

LOCKS=("$PROFILE/SingletonLock" "$PROFILE/SingletonCookie" "$PROFILE/SingletonSocket")
has_lock=""
for lock in "${LOCKS[@]}"; do
  [ -e "$lock" ] && { has_lock=1; break; }
done
if [ -n "$has_lock" ]; then
  if command -v pgrep >/dev/null 2>&1 && pgrep -f "$PROFILE" >/dev/null 2>&1; then
    echo "  [警告] profile $PROFILE 仍被 Chrome 进程占用;若端口起不来,请先关闭那个采集 Chrome。"
  else
    rm -f "${LOCKS[@]}" 2>/dev/null || true
    echo "  [OK] 清理陈旧 Chrome profile 锁:$PROFILE"
  fi
fi

# 零自动化 flag(只加调试端口 + 独立 profile + 跳过首启向导)→ navigator.webdriver 保持 false,最隐蔽
ARGS=( --remote-debugging-port="$PORT" --remote-debugging-address=127.0.0.1
       --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check )
[ -n "$PROXY" ] && ARGS+=( --proxy-server="$PROXY" )
echo "  起采集 Chrome:端口 $PORT · profile $PROFILE${PROXY:+ · 代理 $PROXY}"
if [ "$(uname -s 2>/dev/null || echo unknown)" = "Darwin" ] && command -v open >/dev/null 2>&1; then
  if ! open -na "Google Chrome" --args "${ARGS[@]}" about:blank >/dev/null 2>&1; then
    echo "  [警告] macOS open 启动失败,改用 CHROME_BIN 直起。"
    "$CHROME" "${ARGS[@]}" about:blank >/dev/null 2>&1 &
  fi
else
  "$CHROME" "${ARGS[@]}" about:blank >/dev/null 2>&1 &
fi

# 轮询调试端口起没起来(不用裸 sleep)
up=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  debug_port_up && { up=1; break; }
  sleep 0.5
done
if [ -n "$up" ]; then
  echo "  [OK] 采集 Chrome 已起 → MCP 可 --browserUrl http://127.0.0.1:$PORT attach"
  echo "  健康检查:$DEBUG_URL;不要把裸 http://127.0.0.1:$PORT/ 当用户页面打开。"
  echo "  ① 首次:在该窗口登录 小红书/抖音/B站 各一次(登录态存 $PROFILE,持久复用)"
  echo "  ② 国内出口自检:收割前让 Codex evaluate \`fetch('https://myip.ipip.net').then(r=>r.text())\` 确认地域在大陆(别用 curl,curl 走韩国出口)"
else
  echo "  [警告] 调试端口没起来 → 先用 \`curl -s $DEBUG_URL\` 查健康状态;若不通,多半是同一 profile 被另一个 Chrome 进程占用。"
  exit 1
fi
