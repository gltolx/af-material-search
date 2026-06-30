# Claude Code + Codex /broll Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `broll` and `broll-auto` work from both Claude Code and Codex using the same repo-owned skills and shared B-roll pipeline.

**Architecture:** Keep the existing Bash/Python business pipeline unchanged. Add a thin compatibility layer around skill synchronization, Codex Chrome attachment, runtime-neutral skill text, and regression tests. Claude Code and Codex diverge only at the browser adapter that collects Douyin/XHS raw data.

**Tech Stack:** Bash scripts, system `python3` tests using `unittest`/plain `assert`, existing repo scripts, Chrome DevTools MCP, Claude-in-Chrome, no new runtime framework.

## Global Constraints

- 始终使用中文回复用户;代码、命令、技术标识符保持原样。
- 轻方案:AI 自己当运行时(浏览器 + 已装工具);不上 Playwright/重框架/后台守护,不写签名爬虫,零运维。
- 复用现有脚本,不重写采集、判分、下载、清洗逻辑。
- 稳态禁 `uv tool install` / `uvx`;新依赖用 `pip3 install --user`。
- `score_candidates.py` / `apply_verdicts.py` / `merge_scored.py` 用 system `python3`;`download_server.py` / 抖音解析 / `autorun_kb.py` 用 douyin venv python。
- Codex 浏览器轨必须 attach 到 `127.0.0.1:9222` 的专用真人 Chrome profile,不能退回 MCP 默认自动化 Chrome。
- `broll-auto` 的阶段二契约保持完整:自动选中、分库路由、清洗、入库、跨 run 去重、降级/跳过、审查排除都不能因兼容改造退化。
- 不支持 claude.ai 网页、Claude 桌面云端、或任何碰不到本机 Chrome/下载目录的远程运行时。

---

## File Structure

- Create `sync_skills.sh`: single source for stamping repo skill templates into Claude/Codex skill directories.
- Modify `install.sh`: delegate skill stamping to `sync_skills.sh`, keep setup/preflight orchestration, install both `broll` and `broll-auto`.
- Modify `selfupdate.sh`: always call `sync_skills.sh` with local repo templates, even when git fetch/merge is skipped.
- Modify `preflight.sh`: add non-blocking skill drift warning and keep environment checks authoritative.
- Modify `setup_codex_browser.sh`: detect missing/wrong `chrome-devtools` MCP args and safely repair or report.
- Modify `codex_chrome.sh`: use stable macOS launch path, wait correctly, clear stale Singleton locks, clarify `/json/version`.
- Modify `skills/broll/SKILL.md`, `skills/broll-auto/SKILL.md`, `CLAUDE.md`, `USAGE-Claude.md`, `USAGE-Codex.md`, `browser_harvest_codex.md`: make runtime adapter explicit and remove Claude-only wording.
- Create `tests/test_sync_skills.py`, `tests/test_selfupdate_safe.py`, `tests/test_setup_codex_browser.py`, `tests/test_codex_chrome.py`, `tests/test_browser_schema_compat.py`, `tests/test_broll_auto_compat.py`, `tests/test_text_contracts.py`.

## Task 1: Skill Sync Entry Point

**Files:**
- Create: `sync_skills.sh`
- Modify: `install.sh`
- Test: `tests/test_sync_skills.py`

**Interfaces:**
- Produces: `bash ./sync_skills.sh [--check]`
- Produces: stamped files at `$HOME/.claude/skills/{broll,broll-auto}/SKILL.md` and `$HOME/.codex/skills/{broll,broll-auto}/SKILL.md` when the corresponding client parent directory exists.
- `--check` exits `0` when installed copies match stamped repo templates or no client dirs exist; exits `2` when drift is detected.

- [ ] **Step 1: Write failing tests for skill stamping**

Create `tests/test_sync_skills.py` with these concrete tests:

```python
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def copy_repo(dst: Path) -> None:
    for name in ["sync_skills.sh", "skills"]:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dst / name)
        elif src.exists():
            shutil.copy2(src, dst / name)

def run(cmd, cwd, home):
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def test_sync_installs_both_skills_to_both_clients():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        repo = base / "repo with & chars"
        home = base / "home"
        repo.mkdir()
        (home / ".claude").mkdir(parents=True)
        (home / ".codex").mkdir(parents=True)
        copy_repo(repo)

        result = run(["bash", "./sync_skills.sh"], repo, home)

        assert result.returncode == 0, result.stdout
        for client in [".claude", ".codex"]:
            for skill in ["broll", "broll-auto"]:
                installed = home / client / "skills" / skill / "SKILL.md"
                assert installed.exists()
                text = installed.read_text()
                assert "{{BROLL_HOME}}" not in text
                assert str(repo) in text

def test_sync_check_reports_drift():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        repo = base / "repo"
        home = base / "home"
        repo.mkdir()
        (home / ".codex").mkdir(parents=True)
        copy_repo(repo)

        first = run(["bash", "./sync_skills.sh"], repo, home)
        assert first.returncode == 0, first.stdout
        installed = home / ".codex" / "skills" / "broll" / "SKILL.md"
        installed.write_text(installed.read_text() + "\nDRIFT\n")

        result = run(["bash", "./sync_skills.sh", "--check"], repo, home)

        assert result.returncode == 2
        assert "漂移" in result.stdout or "drift" in result.stdout.lower()

def test_sync_warns_without_clients_but_exits_zero():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        repo = base / "repo"
        home = base / "home"
        repo.mkdir()
        home.mkdir()
        copy_repo(repo)

        result = run(["bash", "./sync_skills.sh"], repo, home)

        assert result.returncode == 0
        assert "没找到" in result.stdout or "无" in result.stdout
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
python3 tests/test_sync_skills.py
```

Expected: FAIL because `sync_skills.sh` does not exist.

- [ ] **Step 3: Implement `sync_skills.sh`**

Create `sync_skills.sh`:

```bash
#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-sync}"

stamp(){
  local src="$1"
  python3 - "$ROOT" "$src" <<'PY'
import sys
root, src = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
sys.stdout.write(text.replace("{{BROLL_HOME}}", root))
PY
}

required=("$ROOT/skills/broll/SKILL.md" "$ROOT/skills/broll-auto/SKILL.md")
for f in "${required[@]}"; do
  [ -f "$f" ] || { echo "  [失败] 缺技能模板 $f"; exit 1; }
done

targets=()
for base in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
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
```

- [ ] **Step 4: Make script executable and rerun test**

Run:

```bash
chmod +x sync_skills.sh
python3 tests/test_sync_skills.py
```

Expected: PASS.

- [ ] **Step 5: Refactor `install.sh` to use `sync_skills.sh`**

Replace the inline skill-copy loop in `install.sh` with:

```bash
echo "== 安装 broll / broll-auto 技能(BROLL_HOME=$ROOT)=="
bash "$ROOT/sync_skills.sh"
SYNC=$?
[ "$SYNC" -ne 0 ] && exit "$SYNC"
```

Keep the existing `AGENTS.md -> CLAUDE.md` logic and setup/preflight logic.

Change final success text to:

```bash
echo "✅ READY:broll/broll-auto 技能已装 + 环境绿灯。cd 进 repo,丢稿子说「用 /broll」或「用 /broll-auto」即可。"
```

- [ ] **Step 6: Commit**

```bash
git add sync_skills.sh install.sh tests/test_sync_skills.py
git commit -m "feat(compat): add shared skill sync for claude code and codex"
```

## Task 2: Selfupdate and Preflight Drift Guard

**Files:**
- Modify: `selfupdate.sh`
- Modify: `preflight.sh`
- Test: `tests/test_selfupdate_safe.py`

**Interfaces:**
- Consumes: `sync_skills.sh`
- Produces: `selfupdate.sh` always exits `0` for non-git/no-origin/fetch-fail/dirty cases and attempts local skill sync.
- Produces: `preflight.sh` warning when installed skills drift.

- [ ] **Step 1: Write failing tests for selfupdate sync behavior**

Create `tests/test_selfupdate_safe.py`:

```python
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def copy_min_repo(dst: Path) -> None:
    for name in ["selfupdate.sh", "sync_skills.sh", "skills"]:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dst / name)
        elif src.exists():
            shutil.copy2(src, dst / name)

def run_selfupdate(repo: Path, home: Path):
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(["bash", "./selfupdate.sh"], cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def test_selfupdate_non_git_still_syncs_skills():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        repo = base / "repo"
        home = base / "home"
        repo.mkdir()
        (home / ".codex").mkdir(parents=True)
        copy_min_repo(repo)

        result = run_selfupdate(repo, home)

        assert result.returncode == 0, result.stdout
        assert (home / ".codex" / "skills" / "broll" / "SKILL.md").exists()

def test_selfupdate_already_latest_refreshes_drifted_skill():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        repo = base / "repo"
        home = base / "home"
        repo.mkdir()
        (home / ".claude").mkdir(parents=True)
        copy_min_repo(repo)
        subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "remote", "add", "origin", str(repo)], cwd=repo, check=True)

        sync = subprocess.run(["bash", "./sync_skills.sh"], cwd=repo, env={**os.environ, "HOME": str(home)}, text=True, stdout=subprocess.PIPE)
        assert sync.returncode == 0
        installed = home / ".claude" / "skills" / "broll" / "SKILL.md"
        installed.write_text("old")

        result = run_selfupdate(repo, home)

        assert result.returncode == 0, result.stdout
        assert "old" not in installed.read_text()
```

- [ ] **Step 2: Run tests and verify at least one fails**

Run:

```bash
python3 tests/test_selfupdate_safe.py
```

Expected: FAIL because `selfupdate.sh` returns before syncing in non-git/already-latest paths.

- [ ] **Step 3: Refactor `selfupdate.sh`**

Add helper near the top of `main()`:

```bash
sync_local_skills(){
  if [ -x "$ROOT/sync_skills.sh" ] || [ -f "$ROOT/sync_skills.sh" ]; then
    bash "$ROOT/sync_skills.sh" || echo "  [警告] skill 同步失败,继续使用当前副本"
  fi
}
```

Before every early `return 0`, call `sync_local_skills`. For example:

```bash
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "  [跳过] 非 git 仓(tarball 部署),不自动更新"; sync_local_skills; return 0; }
```

Also call it after successful ff-only merge and in dirty/diverged failure branch.

- [ ] **Step 4: Add preflight drift warning**

In `preflight.sh`, near the end before `== 自检结束 ==`, add:

```bash
if [ -f "$ROOT/sync_skills.sh" ]; then
  if bash "$ROOT/sync_skills.sh" --check >/tmp/broll_skill_check.$$ 2>&1; then
    ok "broll/broll-auto skill 副本与 repo 模板一致"
  else
    warn "broll/broll-auto skill 副本漂移 → 运行 bash ./sync_skills.sh 刷新"
    sed 's/^/    /' /tmp/broll_skill_check.$$
  fi
  rm -f /tmp/broll_skill_check.$$
fi
```

- [ ] **Step 5: Rerun tests and preflight**

Run:

```bash
python3 tests/test_selfupdate_safe.py
bash ./preflight.sh
```

Expected: tests PASS; preflight remains non-blocking and may warn only if local installed skills drift.

- [ ] **Step 6: Commit**

```bash
git add selfupdate.sh preflight.sh tests/test_selfupdate_safe.py
git commit -m "fix(compat): refresh installed skills during selfupdate and preflight"
```

## Task 3: Codex MCP Configuration Guard

**Files:**
- Modify: `setup_codex_browser.sh`
- Test: `tests/test_setup_codex_browser.py`

**Interfaces:**
- Produces: `bash ./setup_codex_browser.sh`
- Ensures target args contain `chrome-devtools-mcp@1.4.0`, `--slim`, `--browserUrl`, `http://127.0.0.1:9222`, `--no-usage-statistics`, `--no-performance-crux`.
- If existing config is wrong and `codex mcp remove` is unavailable, exits `0` with a warning and manual command.

- [ ] **Step 1: Write failing MCP config tests**

Create `tests/test_setup_codex_browser.py`:

```python
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def make_bin(dir: Path, name: str, body: str) -> None:
    p = dir / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)

def run_script(home: Path, bindir: Path):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return subprocess.run(["bash", str(ROOT / "setup_codex_browser.sh")], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def test_registers_when_missing():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = base / "home"
        bindir = base / "bin"
        (home / ".codex").mkdir(parents=True)
        bindir.mkdir()
        log = base / "codex.log"
        make_bin(bindir, "npx", "exit 0\n")
        make_bin(bindir, "codex", f"echo \"$@\" >> {log!s}; exit 0\n")

        result = run_script(home, bindir)

        assert result.returncode == 0, result.stdout
        text = log.read_text()
        assert "mcp add chrome-devtools" in text
        assert "chrome-devtools-mcp@1.4.0" in text
        assert "--slim" in text
        assert "--browserUrl http://127.0.0.1:9222" in text

def test_wrong_existing_config_warns_or_repairs_not_silent():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = base / "home"
        bindir = base / "bin"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex" / "config.toml").write_text('[mcp_servers.chrome-devtools]\ncommand="npx"\nargs=["chrome-devtools-mcp@1.4.0"]\n')
        bindir.mkdir()
        make_bin(bindir, "npx", "exit 0\n")
        make_bin(bindir, "codex", "if [ \"$1 $2 $3\" = \"mcp remove chrome-devtools\" ]; then exit 1; fi; exit 0\n")

        result = run_script(home, bindir)

        assert result.returncode == 0
        assert "browserUrl" in result.stdout
        assert "静默" not in result.stdout

def test_correct_existing_config_is_ok():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = base / "home"
        bindir = base / "bin"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex" / "config.toml").write_text('[mcp_servers.chrome-devtools]\nargs=["chrome-devtools-mcp@1.4.0","--slim","--browserUrl","http://127.0.0.1:9222"]\n')
        bindir.mkdir()
        make_bin(bindir, "npx", "exit 0\n")
        make_bin(bindir, "codex", "exit 0\n")

        result = run_script(home, bindir)

        assert result.returncode == 0
        assert "已符合" in result.stdout or "OK" in result.stdout
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
python3 tests/test_setup_codex_browser.py
```

Expected: FAIL because existing script skips wrong config and uses `@latest`.

- [ ] **Step 3: Implement config guard**

In `setup_codex_browser.sh`, define:

```bash
TARGET_PKG="chrome-devtools-mcp@1.4.0"
TARGET_URL="http://127.0.0.1:9222"
TARGET_ARGS=(npx -y "$TARGET_PKG" --slim --browserUrl "$TARGET_URL" --no-usage-statistics --no-performance-crux)
```

Replace the existing-config branch with:

```bash
if grep -q '^\[mcp_servers\.chrome-devtools\]' "$CFG" 2>/dev/null; then
  if grep -q -- '--browserUrl' "$CFG" && grep -q "$TARGET_URL" "$CFG" && grep -q -- '--slim' "$CFG"; then
    echo "  [OK] chrome-devtools MCP 已符合 Codex 采集轨配置"
  else
    echo "  [警告] chrome-devtools MCP 已存在但未 attach 到 $TARGET_URL"
    if codex mcp remove chrome-devtools >/dev/null 2>&1; then
      cp "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
      if codex mcp add chrome-devtools -- "${TARGET_ARGS[@]}" >/dev/null 2>&1; then
        echo "  [已修复] chrome-devtools MCP(--slim --browserUrl $TARGET_URL)"
        echo "  [提示] 当前 Codex 会话可能仍加载旧 MCP,请重启 Codex 会话后使用。"
      else
        echo "  [警告] 自动重加失败,请手动运行: codex mcp add chrome-devtools -- ${TARGET_ARGS[*]}"
      fi
    else
      echo "  [警告] 无法自动移除旧配置,请手动删除 [mcp_servers.chrome-devtools] 后运行:"
      echo "      codex mcp add chrome-devtools -- ${TARGET_ARGS[*]}"
    fi
  fi
else
  if codex mcp add chrome-devtools -- "${TARGET_ARGS[@]}" >/dev/null 2>&1; then
    echo "  [已注册] chrome-devtools MCP(--slim --browserUrl $TARGET_URL)"
  else
    echo "  [警告] codex mcp add 失败,手动注册见 browser_harvest_codex.md"
  fi
fi
```

- [ ] **Step 4: Rerun tests**

Run:

```bash
python3 tests/test_setup_codex_browser.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add setup_codex_browser.sh tests/test_setup_codex_browser.py
git commit -m "fix(codex): enforce chrome-devtools browserUrl configuration"
```

## Task 4: Codex Chrome Launcher

**Files:**
- Modify: `codex_chrome.sh`
- Test: `tests/test_codex_chrome.py`

**Interfaces:**
- Consumes env: `CODEX_HARVEST_PORT`, `CODEX_HARVEST_PROFILE`, `CODEX_HARVEST_PROXY`, `CHROME_BIN`.
- Produces: listening DevTools endpoint at `http://127.0.0.1:<port>/json/version`.

- [ ] **Step 1: Write failing launcher tests**

Create `tests/test_codex_chrome.py`:

```python
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def write_exe(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)

def run_chrome(home: Path, bindir: Path, extra_env=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(ROOT / "codex_chrome.sh")], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def test_reuses_existing_port_without_launch():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = base / "home"
        bindir = base / "bin"
        home.mkdir()
        bindir.mkdir()
        log = base / "open.log"
        write_exe(bindir / "curl", "exit 0\n")
        write_exe(bindir / "open", f"echo open >> {log}; exit 0\n")
        chrome = base / "Chrome"
        write_exe(chrome, "exit 0\n")

        result = run_chrome(home, bindir, {"CHROME_BIN": str(chrome)})

        assert result.returncode == 0, result.stdout
        assert "已在" in result.stdout
        assert not log.exists()

def test_launches_with_open_and_proxy_then_waits():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = base / "home"
        bindir = base / "bin"
        profile = home / ".broll-harvest-chrome"
        home.mkdir()
        bindir.mkdir()
        log = base / "open.log"
        state = base / "curl-state"
        write_exe(bindir / "curl", f"if [ -f {state} ]; then exit 0; fi; touch {state}; exit 7\n")
        write_exe(bindir / "open", f"echo \"$@\" > {log}; exit 0\n")
        chrome = base / "Google Chrome"
        write_exe(chrome, "exit 0\n")

        result = run_chrome(home, bindir, {
            "CHROME_BIN": str(chrome),
            "CODEX_HARVEST_PROXY": "http://127.0.0.1:7890",
        })

        assert result.returncode == 0, result.stdout
        text = log.read_text()
        assert "--remote-debugging-port=9222" in text
        assert f"--user-data-dir={profile}" in text
        assert "--proxy-server=http://127.0.0.1:7890" in text

def test_missing_chrome_fails():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = base / "home"
        bindir = base / "bin"
        home.mkdir()
        bindir.mkdir()
        write_exe(bindir / "curl", "exit 7\n")

        result = run_chrome(home, bindir, {"CHROME_BIN": str(base / "missing")})

        assert result.returncode == 1
        assert "未找到 Chrome" in result.stdout
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 tests/test_codex_chrome.py
```

Expected: FAIL because current script calls Chrome binary directly and does not sleep between curls.

- [ ] **Step 3: Implement launcher changes**

In `codex_chrome.sh`:

- Add stale lock cleanup:

```bash
if [ -L "$PROFILE/SingletonLock" ] && ! ps aux | grep -F "$PROFILE" | grep -F "Google Chrome" >/dev/null 2>&1; then
  rm -f "$PROFILE"/SingletonCookie "$PROFILE"/SingletonLock "$PROFILE"/SingletonSocket 2>/dev/null || true
  echo "  [已清理] 陈旧 Chrome profile 锁:$PROFILE"
fi
```

- Use `open -na` on macOS when available:

```bash
if command -v open >/dev/null 2>&1 && [ "$(uname 2>/dev/null)" = "Darwin" ]; then
  open -na "Google Chrome" --args "${ARGS[@]}" about:blank
else
  "$CHROME" "${ARGS[@]}" about:blank >/dev/null 2>&1 &
fi
```

- Add sleep in loop:

```bash
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && { up=1; break; }
  sleep 0.5
done
```

- Update user-facing text to say `http://127.0.0.1:$PORT/json/version` is the health endpoint and `http://127.0.0.1:$PORT/` is not a user page.

- [ ] **Step 4: Rerun tests and live smoke**

Run:

```bash
python3 tests/test_codex_chrome.py
bash ./codex_chrome.sh
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool | sed -n '1,20p'
```

Expected: tests PASS; live smoke returns Chrome version JSON.

- [ ] **Step 5: Commit**

```bash
git add codex_chrome.sh tests/test_codex_chrome.py
git commit -m "fix(codex): harden harvest chrome launcher"
```

## Task 5: Runtime-Neutral Skill and Usage Text

**Files:**
- Modify: `skills/broll/SKILL.md`
- Modify: `skills/broll-auto/SKILL.md`
- Modify: `CLAUDE.md`
- Modify: `USAGE-Claude.md`
- Modify: `USAGE-Codex.md`
- Modify: `browser_harvest_codex.md`
- Test: `tests/test_text_contracts.py`

**Interfaces:**
- Produces: agent-neutral docs with an explicit runtime adapter table.
- Keeps `AGENTS.md -> CLAUDE.md` contract unchanged.

- [ ] **Step 1: Write text contract tests**

Create `tests/test_text_contracts.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(name):
    return (ROOT / name).read_text(encoding="utf-8")

def test_no_claude_only_tool_names_in_skills():
    text = read("skills/broll/SKILL.md") + "\n" + read("skills/broll-auto/SKILL.md")
    forbidden = ["AskUserQuestion", "Codex 旁注", "只有 Codex 看", "Claude 绝不自动"]
    for token in forbidden:
        assert token not in text

def test_broll_has_runtime_adapter_contract():
    text = read("skills/broll/SKILL.md")
    assert "运行时适配层" in text
    assert "Claude Code" in text
    assert "Codex" in text
    assert "writer_server.py" in text
    assert "chrome-devtools" in text or "Chrome DevTools" in text
    assert "同名同 schema" in text

def test_usage_mentions_youtube_login_and_codex_9222_rule():
    assert "YouTube" in read("USAGE-Claude.md")
    codex = read("USAGE-Codex.md")
    assert "YouTube" in codex
    assert "127.0.0.1:9222/json/version" in codex
    assert "内置浏览器" in codex

def test_browser_harvest_codex_mentions_sid_keys():
    text = read("browser_harvest_codex.md")
    assert "SID" in text
    assert "xhsAll_<SID>" in text
    assert "dyAll_<SID>" in text
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 tests/test_text_contracts.py
```

Expected: FAIL due to current Claude-only phrases and missing adapter table.

- [ ] **Step 3: Update `skills/broll/SKILL.md`**

Add a section near the top after project root:

```markdown
## 运行时适配层(Claude Code / Codex)

本技能在 Claude Code 和 Codex 中使用同一命令、同一数据契约。差异只在小红书/抖音前台浏览器收割:

| 运行时 | 浏览器驱动 | 落盘方式 | 前置 |
|---|---|---|---|
| Claude Code | Claude-in-Chrome | `writer_server.py` loopback 写 `xhs_raw.json` / `dy_raw.json` | Chrome 已登录平台账号 |
| Codex | `codex_chrome.sh` + Chrome DevTools MCP | `evaluate` 返回后写同名 raw 文件 | `bash codex_chrome.sh`, MCP attach 到 `127.0.0.1:9222` |

两条路径必须产出同名同 schema 文件。`merge_scored.py` 之后的预过滤、语义判分、出页、下载、清洗、入库完全共享。
```

Replace `AskUserQuestion` with:

```text
暂停并向用户提问/等待用户确认
```

Replace `Claude 绝不` with:

```text
agent 绝不
```

Replace `Codex 旁注` branch with a reference to the runtime adapter.

- [ ] **Step 4: Update `skills/broll-auto/SKILL.md`**

Clarify:

```markdown
`broll-auto` 继承 `/broll` 的运行时适配层。Claude Code 和 Codex 的阶段一收割差异仍只发生在浏览器适配层;阶段二的 `build_autorun_selected.py` / `autorun_kb.py` 完全共享。
```

Clarify unattended behavior:

```markdown
无人值守不等待用户过码。若时间盒内可恢复则继续搜全;仍不可恢复则跳过该平台或降级清晰度,写入日志并继续其它平台/链接。
```

- [ ] **Step 5: Update project and usage docs**

Update `CLAUDE.md`:

- Change title to indicate project contract for Claude Code/Codex.
- Change “7 步流水线” to “按 `/broll` skill 当前流水线执行”。
- Replace detailed dynamic-port/download naming copies with “详见 `skills/broll/SKILL.md` 单一事实源”。

Update `USAGE-Claude.md`:

- Add YouTube to login list.

Update `USAGE-Codex.md`:

- Add YouTube to login list.
- Add: “不要用 Codex 内置浏览器打开 `http://127.0.0.1:9222/`;健康检查用 `curl http://127.0.0.1:9222/json/version`,真正操作在弹出的专用 Google Chrome 窗口。”

Update `browser_harvest_codex.md`:

- Replace fixed `xhsAll`/`dyAll` examples with `xhsAll_<SID>`/`dyAll_<SID>`.
- State `SID` should match the `BROLL_RES` basename for multi-session isolation.

- [ ] **Step 6: Rerun text tests**

Run:

```bash
python3 tests/test_text_contracts.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add skills/broll/SKILL.md skills/broll-auto/SKILL.md CLAUDE.md USAGE-Claude.md USAGE-Codex.md browser_harvest_codex.md tests/test_text_contracts.py
git commit -m "docs(compat): make broll skills runtime neutral"
```

## Task 6: Browser Raw Schema and broll-auto Fixtures

**Files:**
- Test: `tests/test_browser_schema_compat.py`
- Test: `tests/test_broll_auto_compat.py`
- Possibly modify: `browser_harvest_codex.md` if Task 5 did not complete SID wording.
- Possibly modify: `build_autorun_selected.py` only if tests expose missing fields.

**Interfaces:**
- Consumes: `merge_scored.py`, `build_autorun_selected.py`
- Produces: tests proving raw schema and broll-auto selected item fields survive compatibility changes.

- [ ] **Step 1: Write browser schema compatibility test**

Create `tests/test_browser_schema_compat.py`:

```python
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_xhs_image_list_duration_and_src_pool_survive_merge():
    with tempfile.TemporaryDirectory() as td:
        res = Path(td)
        (res / "xhs_raw.json").write_text(json.dumps([
            {
                "platform": "小红书",
                "title": "翻书空镜",
                "page": "https://www.xiaohongshu.com/explore/abc?xsec_token=tok",
                "cover": "https://img/cover.jpg",
                "type": "normal",
                "duration": 12,
                "imgs": ["https://img/1.jpg"],
            }
        ], ensure_ascii=False))
        (res / "dy_raw.json").write_text(json.dumps([
            {
                "platform": "抖音",
                "title": "老空调 复古",
                "page": "https://www.douyin.com/video/1234567890123456789",
                "cover": "https://img/dy.jpg",
                "duration": 8,
            }
        ], ensure_ascii=False))
        env = os.environ.copy()
        env["BROLL_RES"] = str(res)
        result = subprocess.run(["python3", str(ROOT / "merge_scored.py")], cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        assert result.returncode == 0, result.stdout
        scored = json.loads((res / "scored.json").read_text())
        assert scored
        for item in scored:
            assert {"platform", "title", "url", "page", "cover", "duration", "src_pool"} <= set(item)
        imgmap = json.loads((res / "xhs_imgs.json").read_text())
        assert imgmap
        first = next(iter(imgmap.values()))
        assert first["t"] == "normal"
        assert first["imgs"] == ["https://img/1.jpg"]
```

- [ ] **Step 2: Write broll-auto compatibility test**

Create `tests/test_broll_auto_compat.py`:

```python
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_autorun_selected_preserves_routing_pool_and_audit_fields():
    with tempfile.TemporaryDirectory() as td:
        res = Path(td)
        scored = [
            {"platform": "小红书", "title": "主题素材", "url": "", "page": "https://xhs/1", "cover": "", "duration": 10, "src_pool": "theme"},
            {"platform": "抖音", "title": "中性垫片", "url": "", "page": "https://dy/2", "cover": "", "duration": 6, "src_pool": "filler"},
        ]
        verdicts = [
            {"idx": 0, "stable_id": "xhs:1", "verdict": "keep", "score": 88, "title": "主题素材", "platform": "小红书", "page": "https://xhs/1"},
        ]
        verdicts_filler = [
            {"idx": 1, "stable_id": "dy:2", "verdict": "keep", "score": 80, "title": "中性垫片", "platform": "抖音", "page": "https://dy/2", "pool": "filler"},
        ]
        script_matches = {"s1": {"name": "稿一", "persona": "人设A", "words": 300, "matched": [{"idx": 0, "stable_id": "xhs:1", "reason": "match", "score": 88}]}}
        filler_matches = {"s1": {"name": "稿一", "persona": "人设A", "matched": [{"idx": 1, "src_sentence": "翻书"}]}}
        kb_routing = {"links": {"https://doc/1": {"account": "a@example.com", "kb_name": "库A", "script_ids": ["s1"]}}}
        (res / "scored.json").write_text(json.dumps(scored, ensure_ascii=False))
        (res / "verdicts.json").write_text(json.dumps(verdicts, ensure_ascii=False))
        (res / "verdicts_filler.json").write_text(json.dumps(verdicts_filler, ensure_ascii=False))
        (res / "script_matches.json").write_text(json.dumps(script_matches, ensure_ascii=False))
        (res / "filler_matches.json").write_text(json.dumps(filler_matches, ensure_ascii=False))
        (res / "kb_routing.json").write_text(json.dumps(kb_routing, ensure_ascii=False))
        env = os.environ.copy()
        env["BROLL_RES"] = str(res)

        result = subprocess.run(["python3", str(ROOT / "build_autorun_selected.py")], cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        assert result.returncode == 0, result.stdout
        selected = json.loads((res / "autorun_selected.json").read_text())
        assert len(selected) == 2
        pools = {item.get("pool", "theme") for item in selected}
        assert pools == {"theme", "filler"}
        for item in selected:
            assert item["account"] == "a@example.com"
            assert item["kb_name"] == "库A"
            assert item["source_link"] == "https://doc/1"
            assert "script_name" in item
            assert "persona" in item
            assert "audit" in item
        filler = [x for x in selected if x.get("pool") == "filler"][0]
        assert filler["from_script"] == "翻书"
```

- [ ] **Step 3: Run tests and observe failures**

Run:

```bash
python3 tests/test_browser_schema_compat.py
python3 tests/test_broll_auto_compat.py
```

Expected: browser schema test may pass if existing merge already supports these fields; broll-auto test may expose missing `audit` or filler fields. If a test passes immediately, keep it as regression coverage.

- [ ] **Step 4: Fix only exposed compatibility gaps**

If `build_autorun_selected.py` omits `audit`, set default:

```python
item["audit"] = item.get("audit") or "pass"
```

If filler items omit `from_script`, copy it from `filler_matches.json` matched entry.

Do not modify `autorun_kb.py` unless the fixture proves it cannot consume existing selected item fields.

- [ ] **Step 5: Rerun tests**

Run:

```bash
python3 tests/test_browser_schema_compat.py
python3 tests/test_broll_auto_compat.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_browser_schema_compat.py tests/test_broll_auto_compat.py build_autorun_selected.py
git commit -m "test(compat): cover browser raw schema and broll-auto selection"
```

## Task 7: Full Verification and Skill Reinstall

**Files:**
- Modify only if previous verification exposes a small bug.

**Interfaces:**
- Consumes all prior tasks.
- Produces installed current skills for local Claude Code and Codex clients.

- [ ] **Step 1: Run all new compatibility tests**

Run:

```bash
python3 tests/test_sync_skills.py
python3 tests/test_selfupdate_safe.py
python3 tests/test_setup_codex_browser.py
python3 tests/test_codex_chrome.py
python3 tests/test_text_contracts.py
python3 tests/test_browser_schema_compat.py
python3 tests/test_broll_auto_compat.py
```

Expected: all PASS.

- [ ] **Step 2: Run existing lightweight tests**

Run:

```bash
for t in tests/test_*.py; do python3 "$t"; done
```

Expected: all tests that use system `python3` PASS. If a test requires douyin venv python and fails with missing `requests`, rerun that specific test with:

```bash
~/.local/share/uv/tools/douyin-mcp-server/bin/python tests/<test_name>.py
```

- [ ] **Step 3: Run install/update/preflight smoke**

Run:

```bash
bash ./sync_skills.sh
bash ./selfupdate.sh
bash ./preflight.sh
```

Expected: no red light from compatibility changes; preflight may keep existing expected warnings for ports or uv.

- [ ] **Step 4: Run Codex Chrome smoke**

Run:

```bash
bash ./codex_chrome.sh
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool | sed -n '1,20p'
```

Expected: Chrome version JSON prints. Do not use Codex in-app browser for `http://127.0.0.1:9222/`.

- [ ] **Step 5: Check installed skill drift**

Run:

```bash
bash ./sync_skills.sh --check
```

Expected: exit `0`, no drift warnings.

- [ ] **Step 6: Final commit if verification needed small fixes**

If Step 1-5 required fixes:

```bash
git add <fixed-files>
git commit -m "fix(compat): final verification fixes"
```

If no fixes were needed, do not create an empty commit.

## Self-Review Checklist

- Spec coverage:
  - Skill sync: Task 1 and Task 2.
  - Codex MCP attach: Task 3.
  - Codex Chrome lifecycle: Task 4.
  - Runtime-neutral docs: Task 5.
  - Raw schema and `broll-auto` stage two: Task 6.
  - Verification: Task 7.
- Red-flag scan: no unfinished marker text or unspecified “add tests” steps.
- Type consistency: shell interfaces are `sync_skills.sh [--check]`, `setup_codex_browser.sh`, `codex_chrome.sh`; fixture files match existing data contracts.
