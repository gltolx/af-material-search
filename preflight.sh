#!/usr/bin/env bash
# af-material-search 启动自检 + 自愈。新会话动手前必跑:bash preflight.sh
# 用断言挡掉本项目所有"环境类"坑(uv没了/yt-dlp配置坏行/ffmpeg/封面依赖/登录态)。
# 退出码: 0=全绿可开工; 1=有红灯(按提示修或问用户)。能自动修的直接修(幂等)。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DY_PY="$HOME/.local/share/uv/tools/douyin-mcp-server/bin/python"   # 抖音解析/出页:必须用这个 venv(py3.12, 有 requests+douyin 模块, 无 PIL)
XHS="$HOME/.local/share/uv/tools/xiaohongshu-cli/bin/xhs"
# yt-dlp 版本无关定位(换机 system python ≠3.9 也别误判):env YTDLP → PATH → 任意 ~/Library/Python/3.*/bin → ~/.local/bin
if [ -n "${YTDLP:-}" ] && [ -x "${YTDLP:-}" ]; then :; else
  YTDLP="$(command -v yt-dlp 2>/dev/null || true)"
  if [ -z "$YTDLP" ]; then for c in "$HOME"/Library/Python/3.*/bin/yt-dlp "$HOME/.local/bin/yt-dlp"; do [ -x "$c" ] && { YTDLP="$c"; break; }; done; fi
fi
FFMPEG="$HOME/.local/bin/ffmpeg"; [ -x "$FFMPEG" ] || FFMPEG="$(command -v ffmpeg 2>/dev/null || echo "$FFMPEG")"
RED=0; ok(){ echo "  [OK]  $1"; }; warn(){ echo "  [警告] $1"; }; bad(){ echo "  [红灯] $1"; RED=1; }

echo "== af-material-search 启动自检 =="

# 0) Command Line Tools / 真 python3(全新 Mac 上 /usr/bin/python3 是桩,首次调用会弹 GUI 装 CLT → setup.sh 第一步就卡死)
if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
  bad "未装 Command Line Tools(全新 Mac)→ 先 \`xcode-select --install\`,装完重跑。否则 python3/pip/git 全是桩。"
fi

# 1) uv 本体已被清(预期):不要尝试 uv tool install / uvx,会失败。已装的 tool venv 仍可直接用其 bin/python。
if command -v uv >/dev/null 2>&1; then ok "uv 存在(可 uv tool install)"; else
  warn "uv 已被清除(预期)——禁止用 uv/uvx 装东西;复用已装 venv 的绝对路径 bin/python。新增依赖用 pip3 install --user。"; fi

# 2) 抖音解析 venv:解析无水印+封面、build_douyin_page.py 必须用它
if [ -x "$DY_PY" ] && "$DY_PY" -c "import requests; from douyin_mcp_server.server import DouyinProcessor" 2>/dev/null; then
  ok "抖音 venv 就绪($DY_PY,含 requests+DouyinProcessor)"
else bad "抖音 venv 缺失或依赖坏:$DY_PY —— 无它无法解析抖音无水印/封面。"; fi

# 3) yt-dlp + 毒配置行(agent-reach 会写入 --js-runtimes node,破坏 yt-dlp 2025.10.14)→ 自动剔除
if [ -x "$YTDLP" ]; then ok "yt-dlp 存在($("$YTDLP" --version 2>/dev/null))"; else bad "yt-dlp 缺失:$YTDLP(B站/YT 下载用)"; fi
for CFG in "$HOME/.config/yt-dlp/config" "$HOME/Library/Application Support/yt-dlp/config"; do
  if [ -f "$CFG" ] && grep -q "js-runtimes" "$CFG" 2>/dev/null; then
    sed -i '' '/js-runtimes/d' "$CFG" 2>/dev/null && warn "已自动剔除毒行 --js-runtimes($CFG)。若刚跑过 agent-reach,重跑本自检确认。"
  fi
done
ok "yt-dlp 配置无毒行 --js-runtimes"

# 4) ffmpeg(B站全 DASH,必须合流)
if [ -x "$FFMPEG" ] && "$FFMPEG" -version >/dev/null 2>&1; then ok "ffmpeg 就绪($FFMPEG)"; else bad "ffmpeg 缺失:$FFMPEG —— B站 DASH 无法合流。"; fi

# 5) PIL(pHash 感知去重)在 system python3,不在抖音 venv → apply_verdicts.py 必须用 system python3 跑!
if python3 -c "import PIL" 2>/dev/null; then ok "system python3 有 PIL(apply_verdicts.py 用 system python3 跑,不是抖音 venv!)"; else
  warn "system python3 缺 PIL → pHash 去重会被跳过(看着全是重复)。修:pip3 install --user Pillow"; fi

# 6) 小红书登录态(cookies 自 Chrome 读)
if [ -x "$XHS" ] && "$XHS" status 2>/dev/null | grep -q "authenticated: true"; then ok "小红书已登录(xhs)"; else
  warn "小红书未登录/xhs 异常 → 让用户在 Chrome 登录小红书后重试。"; fi

# 7) B站搜索 API(KR 出口直连,无需 cookie)。失败不阻塞(可能瞬断)。
if curl -s --max-time 8 "https://api.bilibili.com/x/web-interface/search/all/v2?keyword=test" 2>/dev/null | grep -q '"code":0'; then
  ok "B站搜索 API 直连可用(KR 出口 OK)"; else warn "B站搜索 API 本次未通(可能瞬断/风控,稍后重试)。"; fi

# 8) Chrome 本体(小红书cookie / B站1080P / 浏览器收割 / 验证码聚焦 全靠它)+ cookies DB
if [ -d "/Applications/Google Chrome.app" ] || command -v google-chrome >/dev/null 2>&1; then ok "Chrome 已装"; else
  warn "未装 Chrome → 小红书收割/抖音收割/B站1080P下载/验证码聚焦 全废。装 Chrome 并登录 小红书/B站/抖音采集号。"; fi
CHROME_COOKIES="$HOME/Library/Application Support/Google/Chrome/Default/Cookies"
if [ -f "$CHROME_COOKIES" ]; then ok "Chrome cookies DB 在(B站下载 --cookies-from-browser chrome 可用;带 cookie 解锁 1080P)"; else
  warn "没找到 Chrome Default cookies DB → B站下载(第8步)会 412/只 480P。让用户用 Chrome 登录 B站并保持开着。"; fi

# 9) 下载/落盘/选片端口(download_server 8788 / writer_server 8799 / 选片页 http.server 8765)。
#    多开为常态:三个 server 都已端口自适应(被占自动向上顺延),被占不阻塞、不需手动改端口。警告级提示而已。
for P in 8788 8799 8765; do
  if lsof -ti :"$P" >/dev/null 2>&1; then warn "端口 $P 已被占 → 多开为常态,本会话端口自动顺延(各 server 写 .dlport/.writerport 旁车,出页运行期读真实端口),**勿 kill 他人进程**(kill 正在边下边传的活进程会让 node2 那条永卡 loading);并行多会话各用独立 BROLL_RES。"; fi
done

echo "== 自检结束 =="
if [ "$RED" -eq 0 ]; then
  echo "[全绿] 可开工。提醒:抖音解析/出页用 \$DY_PY;pHash 去重(apply_verdicts.py)用 system python3;下载抖音封面/正片用 requests 带 Referer(curl 对字节 CDN 报 SSL)。"
  exit 0
else echo "[有红灯] 先修上面红灯项,或就缺失项问用户;不要带病硬跑。"; exit 1; fi
