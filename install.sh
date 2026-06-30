#!/usr/bin/env bash
# 一条命令把 broll / broll-auto 技能装进本机的 Claude / Codex 客户端 + 装环境 + 自检。
# 换机用法:git clone <repo> && cd af-material-search && bash install.sh
# 设计:幂等可重跑;薄壳零框架(铁律·轻);环境活全交给 setup.sh(它末尾自动跑 preflight),本脚本不重复造。
# 更新:repo 内脚本是绝对路径引用 → git pull 即刻生效;重跑本脚本只为「重新盖章 SKILL.md + 复检环境」。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # repo 真实落点(克隆到哪都自识别)

echo "== 安装 broll / broll-auto 技能(BROLL_HOME=$ROOT)=="

# 1) Codex 契约:AGENTS.md → CLAUDE.md 软链(Codex 不读 CLAUDE.md,只读 AGENTS.md;Claude 仍读 CLAUDE.md)
if [ -f "$ROOT/CLAUDE.md" ] && [ ! -e "$ROOT/AGENTS.md" ]; then
  ( cd "$ROOT" && ln -s CLAUDE.md AGENTS.md ) && echo "  建软链 AGENTS.md → CLAUDE.md"
else
  [ -e "$ROOT/AGENTS.md" ] && echo "  AGENTS.md 已在(跳过)"
fi

# 2) 把「盖好真实绝对路径」的 SKILL.md 拷进本机存在的客户端 skills 目录。
#    copy 而非 symlink:安装副本必须含真实绝对路径(软链会暴露 {{BROLL_HOME}} 原文 → 技能找不到 repo)。
bash "$ROOT/sync_skills.sh"
SYNC=$?
[ "$SYNC" -ne 0 ] && exit "$SYNC"

# 3) 装环境 + 自检(setup.sh 幂等:已装的跳过;它末尾自动跑 preflight.sh 做权威校验)
echo ""
echo "== 装环境 + 自检(setup.sh → preflight)=="
bash "$ROOT/setup.sh"
PF=$?

# 4) Codex 专属:注册 chrome-devtools 浏览器 MCP(收割小红书/抖音用)。仅当本机有 Codex 才做,完全不碰 ~/.claude → Claude Code 零影响。
if [ -d "$HOME/.codex" ]; then
  echo ""
  echo "== Codex 浏览器轨配置(仅 Codex;不影响 Claude Code)=="
  bash "$ROOT/setup_codex_browser.sh" || echo "  [警告] Codex 浏览器轨未配好(不影响 Claude 轨与环境绿灯)"
fi

echo ""
if [ "$PF" -eq 0 ]; then
  echo "✅ READY:broll/broll-auto 技能已装 + 环境绿灯。cd 进 repo,丢稿子说「用 /broll」或「用 /broll-auto」即可。"
else
  echo "⚠️  broll/broll-auto 技能已装;preflight 有红灯(多半是 CLT/登录态)→ 按上面提示修后重跑。"
fi
exit "$PF"
