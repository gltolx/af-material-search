#!/usr/bin/env bash
# Stamp repo skills into local Claude Code / Codex skill directories.
# Safe to run repeatedly; use --check to report drift without writing.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-sync}"

stamp(){
  local src="$1"
  python3 - "$ROOT" "$src" <<'PY'
import sys

root, src = sys.argv[1], sys.argv[2]
with open(src, encoding="utf-8") as f:
    text = f.read()
sys.stdout.write(text.replace("{{BROLL_HOME}}", root))
PY
}

required=("$ROOT/skills/broll/SKILL.md" "$ROOT/skills/broll-auto/SKILL.md")
for f in "${required[@]}"; do
  [ -f "$f" ] || { echo "  [失败] 缺技能模板 $f"; exit 1; }
done

targets=()
for base in "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.agents/skills"; do
  parent="$(dirname "$base")"
  [ -d "$parent" ] && targets+=("$base")
done

if [ "${#targets[@]}" -eq 0 ]; then
  echo "  [警告] 没找到 ~/.claude 或 ~/.codex —— 先装 Claude Code/Codex 客户端,再重跑。"
  exit 0
fi

drift=0
for base in "${targets[@]}"; do
  for sk in "$ROOT"/skills/*/SKILL.md; do
    [ -f "$sk" ] || continue
    name="$(basename "$(dirname "$sk")")"
    dest="$base/$name/SKILL.md"
    tmp="$(mktemp)"
    stamp "$sk" > "$tmp"
    if [ "$MODE" = "--check" ]; then
      if [ ! -f "$dest" ] || ! cmp -s "$tmp" "$dest"; then
        echo "  [警告] skill 漂移: $dest"
        drift=1
      fi
      rm -f "$tmp"
    else
      mkdir -p "$base/$name"
      if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
        echo "  [OK] $dest 已最新"
        rm -f "$tmp"
      else
        mv "$tmp" "$dest"
        echo "  [已更新] $dest"
      fi
    fi
  done
done

[ "$MODE" = "--check" ] && [ "$drift" -ne 0 ] && exit 2
exit 0
