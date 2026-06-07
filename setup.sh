#!/usr/bin/env bash
# af-material-search 一次性 bootstrap:换机 / 环境被清后,从零装齐 /broll 全部依赖。
# 设计原则:
#   1) 幂等 —— 已装好的组件直接跳过。本机(venv 都在、uv 已被清)跑它 = 只做校验 + 跑 preflight,绝不重装、绝不碰 uv。
#   2) uv 只是 bootstrap 工具 —— 仅当抖音/小红书 venv "缺失或坏" 时才临时用 uv 建 tool venv(系统 py3.9 建不了 py3.10+ venv,uv 能自带 python)。
#      装完即回到铁律:日常加运行时依赖用 `pip3 install --user`,别用 uv。
#   3) 不 sudo、不交互 —— 服务器上能无人值守跑;装不了的(如需 root 的)只给出明确指引,不擅自 sudo。
# 装完跑 preflight.sh 应当全绿。本会话只在 macOS(darwin)实测过;Linux 分支为 best-effort 并已标注。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"; ARCH="$(uname -m)"
DY_PY="$HOME/.local/share/uv/tools/douyin-mcp-server/bin/python"
XHS="$HOME/.local/share/uv/tools/xiaohongshu-cli/bin/xhs"
FFMPEG="$HOME/.local/bin/ffmpeg"
NEED_MANUAL=()

# 日志一律走 stderr,好让 ensure_uv 用 stdout 干净地回传路径
say(){ echo "  → $1" >&2; }
ok(){  echo "  [OK]  $1" >&2; }
warn(){ echo "  [警告] $1" >&2; }
die(){ echo "  [失败] $1" >&2; exit 1; }

echo "== af-material-search bootstrap ($OS / $ARCH) ==" >&2

# ── 0) Command Line Tools 守卫(全新 Mac:/usr/bin/python3 是桩,首个 python3/pip 调用会弹 GUI 装 CLT 卡死)──
if [ "$OS" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
  die "未装 Command Line Tools。先跑 \`xcode-select --install\`(装完点完弹窗),再重跑本脚本——否则 python3/pip/git 都是桩,后续必卡。"
fi

# pip(系统 python3 user 装):先确保 pip 在
py_pip(){ python3 -m pip "$@"; }
ensure_pip(){ python3 -m pip --version >/dev/null 2>&1 || python3 -m ensurepip --user >/dev/null 2>&1 || true; }

# 仅在抖音/小红书 venv 缺失时才需要 uv;返回可用的 uv 路径(stdout)
ensure_uv(){
  if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
  if [ -x "$HOME/.local/bin/uv" ]; then echo "$HOME/.local/bin/uv"; return; fi
  warn "uv 不在 → 一次性安装 uv(仅用于建 tool venv;装完日常仍用 pip3 --user)"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || die "uv 安装失败(无网?手动:curl -LsSf https://astral.sh/uv/install.sh | sh)"
  [ -x "$HOME/.local/bin/uv" ] && echo "$HOME/.local/bin/uv" || die "uv 装后仍不在 ~/.local/bin/uv"
}

# ── 1) 抖音解析 venv(douyin-mcp-server,提供 DouyinProcessor;附带 requests)──
if [ -x "$DY_PY" ] && "$DY_PY" -c "import requests; from douyin_mcp_server.server import DouyinProcessor" 2>/dev/null; then
  ok "抖音 venv 已就绪(跳过)"
else
  UV="$(ensure_uv)"
  say "uv tool install --with requests douyin-mcp-server"
  "$UV" tool install --with requests douyin-mcp-server >/dev/null 2>&1 || die "douyin-mcp-server 安装失败"
  "$DY_PY" -c "import requests; from douyin_mcp_server.server import DouyinProcessor" 2>/dev/null \
    && ok "抖音 venv 装好($DY_PY)" || die "抖音 venv 装后仍导入失败"
fi

# ── 2) 小红书 CLI(xiaohongshu-cli,console script = xhs)──
if [ -x "$XHS" ]; then
  ok "xhs 已就绪(跳过)"
else
  UV="$(ensure_uv)"
  say "uv tool install xiaohongshu-cli"
  "$UV" tool install xiaohongshu-cli >/dev/null 2>&1 || die "xiaohongshu-cli 安装失败"
  [ -x "$XHS" ] && ok "xhs 装好($XHS)" || die "xhs 装后不在 $XHS"
fi
# 登录态自动不了(靠 Chrome cookie),记成人工事项
if "$XHS" status 2>/dev/null | grep -q "authenticated: true"; then
  ok "小红书已登录"
else
  NEED_MANUAL+=("小红书登录:在 Chrome 里登录小红书(xhs 自动读 Chrome cookie),再跑 \`$XHS status\` 应见 authenticated: true")
fi

# ── 3) yt-dlp(系统 python3 user 装,不走 uv)──
ensure_pip
# 版本无关定位(system python 不一定是 3.9):PATH → 任意 ~/Library/Python/3.*/bin → ~/.local/bin
find_ytdlp(){
  command -v yt-dlp 2>/dev/null && return
  for c in "$HOME"/Library/Python/3.*/bin/yt-dlp "$HOME/.local/bin/yt-dlp"; do [ -x "$c" ] && { echo "$c"; return; }; done
  echo ""
}
YTDLP="$(find_ytdlp)"
if [ -n "$YTDLP" ] && [ -x "$YTDLP" ]; then
  ok "yt-dlp 已就绪($("$YTDLP" --version 2>/dev/null))"
else
  say "pip3 install --user yt-dlp"
  py_pip install --user -q yt-dlp >/dev/null 2>&1 || die "yt-dlp 安装失败"
  YTDLP="$(find_ytdlp)"
  [ -n "$YTDLP" ] && [ -x "$YTDLP" ] && ok "yt-dlp 装好($YTDLP)" || warn "yt-dlp 装了但找不到二进制,检查 \`python3 -m pip show yt-dlp\` 定位"
fi

# ── 4) 剔除 yt-dlp 毒配置行 --js-runtimes(agent-reach 写入,会破坏 yt-dlp 2025.10.14)──
for CFG in "$HOME/.config/yt-dlp/config" "$HOME/Library/Application Support/yt-dlp/config"; do
  if [ -f "$CFG" ] && grep -q "js-runtimes" "$CFG" 2>/dev/null; then
    if [ "$OS" = "Darwin" ]; then sed -i '' '/js-runtimes/d' "$CFG" 2>/dev/null; else sed -i '/js-runtimes/d' "$CFG" 2>/dev/null; fi
    warn "已剔除毒行 --js-runtimes($CFG)"
  fi
done
ok "yt-dlp 配置无毒行 --js-runtimes"

# ── 5) ffmpeg(B站全 DASH,合流必须)──
if { [ -x "$FFMPEG" ] && "$FFMPEG" -version >/dev/null 2>&1; } then
  ok "ffmpeg 已就绪(跳过)"
elif command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg 在 PATH(跳过):$(command -v ffmpeg)"
else
  mkdir -p "$HOME/.local/bin"
  if [ "$OS" = "Darwin" ]; then
    case "$ARCH" in
      arm64)  ASSET="ffmpeg-darwin-arm64" ;;
      x86_64) ASSET="ffmpeg-darwin-x64" ;;
      *) die "未知 macOS 架构 $ARCH;手动放一个静态 ffmpeg 到 $FFMPEG" ;;
    esac
    say "下载静态 ffmpeg($ASSET → $FFMPEG)"
    curl -fsSL "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/$ASSET" -o "$FFMPEG" \
      && chmod +x "$FFMPEG" || die "ffmpeg 下载失败"
  else
    # Linux:johnvansickle 静态构建(无 sudo);未在本会话实测
    case "$ARCH" in
      x86_64|amd64) LARCH="amd64" ;;
      aarch64|arm64) LARCH="arm64" ;;
      *) warn "Linux 未知架构 $ARCH:请手动装 ffmpeg(apt/dnf 或静态二进制到 $FFMPEG)"; LARCH="" ;;
    esac
    if [ -n "$LARCH" ]; then
      say "下载 Linux 静态 ffmpeg(johnvansickle $LARCH;未实测)"
      TMP="$(mktemp -d)"
      if curl -fsSL "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${LARCH}-static.tar.xz" -o "$TMP/f.tar.xz" \
         && tar -xJf "$TMP/f.tar.xz" -C "$TMP" 2>/dev/null; then
        BIN="$(find "$TMP" -name ffmpeg -type f | head -1)"
        [ -n "$BIN" ] && cp "$BIN" "$FFMPEG" && chmod +x "$FFMPEG"
      fi
      rm -rf "$TMP"
    fi
  fi
  if [ -x "$FFMPEG" ] && "$FFMPEG" -version >/dev/null 2>&1; then ok "ffmpeg 装好($FFMPEG)"
  else warn "ffmpeg 仍不可用 → B站 DASH 合流会失败;手动装(mac: brew/static;linux: apt-get install ffmpeg 或静态到 $FFMPEG)"; fi
fi

# ── 6) Pillow(pHash 感知去重,system python3;apply_verdicts.py 用它)──
if python3 -c "import PIL" 2>/dev/null; then
  ok "system python3 有 PIL(跳过)"
else
  say "pip3 install --user Pillow"
  py_pip install --user -q Pillow >/dev/null 2>&1 || die "Pillow 安装失败"
  python3 -c "import PIL" 2>/dev/null && ok "Pillow 装好" || die "Pillow 装后仍导入失败"
fi

echo "== bootstrap 结束 ==" >&2

# ── 收尾:跑 preflight 做权威校验 ──
if [ -x "$ROOT/preflight.sh" ] || [ -f "$ROOT/preflight.sh" ]; then
  echo "" >&2; echo "→ 跑 preflight.sh 做权威校验:" >&2
  bash "$ROOT/preflight.sh"
  PF=$?
else
  warn "没找到 preflight.sh,跳过收尾校验"; PF=0
fi

# ── 仍需人本人做的(自动化绕不开)──
echo "" >&2
echo "== 仍需你本人做(自动化绕不开)==" >&2
if [ "${#NEED_MANUAL[@]}" -gt 0 ]; then for m in "${NEED_MANUAL[@]}"; do echo "  • $m" >&2; done; fi
echo "  • 抖音(每次开工):Claude-in-Chrome 插件连着 + 一个登录好的专用采集小号;撞登录墙时由你扫码续。" >&2
echo "  • 选片:filtered.html 上勾选是唯一常规人工动作(顺带审黄赌毒/政治)。" >&2

exit ${PF:-0}
