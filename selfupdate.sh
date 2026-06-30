#!/usr/bin/env bash
# 每次用 /broll 前自动同步最新代码(技能第0步会先跑它)。ff-only 拉取 origin。
# 安全铁律:非 git 仓 / 无 origin / 离线 / 脏树无法快进 → 一律「警告但不阻塞」,用本地代码继续,绝不卡死流水线。
# 防自改崩溃:全部逻辑包进 main(),bash 先把整个函数读进内存再执行,故即使 git pull 中途替换了本脚本文件也能安全跑完。
set -u

main(){
  local ROOT BR LOCAL REMOTE SRC d
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  sync_local_skills(){
    if [ -f "$ROOT/sync_skills.sh" ]; then
      bash "$ROOT/sync_skills.sh" || echo "  [警告] skill 同步失败,继续使用当前副本"
    fi
  }
  cd "$ROOT" 2>/dev/null || return 0
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "  [跳过] 非 git 仓(tarball 部署),不自动更新"; sync_local_skills; return 0; }
  git remote get-url origin >/dev/null 2>&1 || { echo "  [跳过] 无 origin 远端,不自动更新"; sync_local_skills; return 0; }
  # SSH 连接超时保护:离线时最多卡 ~10s(macOS 无 timeout 命令,用 ssh ConnectTimeout);BatchMode 免交互挂起
  if ! GIT_SSH_COMMAND="ssh -o ConnectTimeout=10 -o BatchMode=yes" git fetch -q origin 2>/dev/null; then
    echo "  [跳过] git fetch 失败(离线/无权限?),用本地代码继续"; sync_local_skills; return 0
  fi
  BR="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"
  LOCAL="$(git rev-parse @ 2>/dev/null || echo x)"
  REMOTE="$(git rev-parse "origin/$BR" 2>/dev/null || echo "")"
  [ -z "$REMOTE" ] && { echo "  [跳过] 远端无 $BR 分支"; sync_local_skills; return 0; }
  [ "$LOCAL" = "$REMOTE" ] && { echo "  [OK] 代码已是最新($BR @ ${LOCAL:0:7})"; sync_local_skills; return 0; }
  if git merge --ff-only -q "origin/$BR" 2>/dev/null; then
    echo "  [已更新] 快进到 origin/$BR(${REMOTE:0:7})"
    # 代码靠绝对路径引用即时生效;顺带把技能文案重新盖章进各客户端,文案也跟上
    sync_local_skills
  else
    echo "  [警告] 本地有未提交改动或已分叉,无法自动快进 → 用本地代码继续(需手动:git stash && git pull --ff-only,或 git pull --rebase)"
    sync_local_skills
  fi
  return 0
}
main "$@"
exit 0
