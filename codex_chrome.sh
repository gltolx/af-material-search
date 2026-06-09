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

# 已在监听调试端口 → 采集 Chrome 已起,直接复用(幂等)
if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "  [OK] 采集 Chrome 已在 127.0.0.1:$PORT(复用);要重登录就在那个窗口里操作"
  exit 0
fi

# 零自动化 flag(只加调试端口 + 独立 profile + 跳过首启向导)→ navigator.webdriver 保持 false,最隐蔽
ARGS=( --remote-debugging-port="$PORT" --remote-debugging-address=127.0.0.1
       --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check )
[ -n "$PROXY" ] && ARGS+=( --proxy-server="$PROXY" )
echo "  起采集 Chrome:端口 $PORT · profile $PROFILE${PROXY:+ · 代理 $PROXY}"
"$CHROME" "${ARGS[@]}" about:blank >/dev/null 2>&1 &

# 轮询调试端口起没起来(不用裸 sleep)
up=""
for _ in 1 2 3 4 5 6 7 8; do
  curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && { up=1; break; }
done
if [ -n "$up" ]; then
  echo "  [OK] 采集 Chrome 已起 → MCP 可 --browserUrl http://127.0.0.1:$PORT attach"
  echo "  ① 首次:在该窗口登录 小红书/抖音/B站 各一次(登录态存 $PROFILE,持久复用)"
  echo "  ② 国内出口自检:收割前让 Codex evaluate \`fetch('https://myip.ipip.net').then(r=>r.text())\` 确认地域在大陆(别用 curl,curl 走韩国出口)"
else
  echo "  [警告] 调试端口没起来 → 多半是同一 profile 已被另一个 Chrome 进程占用。先把用该 profile 的 Chrome 全关掉再重跑。"
  exit 1
fi
