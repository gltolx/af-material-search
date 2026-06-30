import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def make_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


def run_script(home: Path, bindir: Path):
    toolbin = bindir.parent / "tools"
    toolbin.mkdir(exist_ok=True)
    for name in ("awk", "cp", "date", "dirname"):
        target = shutil.which(name)
        if target:
            link = toolbin / name
            if not link.exists():
                link.symlink_to(target)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{toolbin}"
    return subprocess.run(
        ["/bin/bash", str(ROOT / "setup_codex_browser.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class SetupCodexBrowserTests(unittest.TestCase):
    def test_registers_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            bindir = base / "bin"
            (home / ".codex").mkdir(parents=True)
            bindir.mkdir()
            log = base / "codex.log"
            make_bin(bindir, "npx", "exit 0\n")
            make_bin(bindir, "codex", f"echo \"$@\" >> {log}; exit 0\n")

            result = run_script(home, bindir)

            self.assertEqual(result.returncode, 0, result.stdout)
            text = log.read_text()
            self.assertIn("mcp add chrome-devtools", text)
            self.assertIn("chrome-devtools-mcp@1.4.0", text)
            self.assertIn("--slim", text)
            self.assertIn("--browserUrl http://127.0.0.1:9222", text)
            self.assertIn("--no-usage-statistics", text)
            self.assertIn("--no-performance-crux", text)

    def test_wrong_existing_config_warns_or_repairs_not_silent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            bindir = base / "bin"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex" / "config.toml").write_text(
                '[mcp_servers.chrome-devtools]\ncommand="npx"\nargs=["chrome-devtools-mcp@1.4.0"]\n'
            )
            bindir.mkdir()
            make_bin(bindir, "npx", "exit 0\n")
            make_bin(
                bindir,
                "codex",
                'if [ "$1 $2 $3" = "mcp remove chrome-devtools" ]; then exit 1; fi; exit 0\n',
            )

            result = run_script(home, bindir)

            self.assertEqual(result.returncode, 0)
            self.assertIn("browserUrl", result.stdout)
            self.assertNotIn("静默", result.stdout)

    def test_correct_existing_config_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            bindir = base / "bin"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex" / "config.toml").write_text(
                '[mcp_servers.chrome-devtools]\nargs=["chrome-devtools-mcp@1.4.0","--slim","--browserUrl","http://127.0.0.1:9222","--no-usage-statistics","--no-performance-crux"]\n'
            )
            bindir.mkdir()
            make_bin(bindir, "npx", "exit 0\n")
            log = base / "codex.log"
            make_bin(bindir, "codex", f"echo \"$@\" >> {log}; exit 17\n")

            result = run_script(home, bindir)

            self.assertEqual(result.returncode, 0)
            self.assertTrue("已符合" in result.stdout or "OK" in result.stdout)
            self.assertFalse(log.exists(), result.stdout)

    def test_checks_only_chrome_devtools_section(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            bindir = base / "bin"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex" / "config.toml").write_text(
                '[mcp_servers.chrome-devtools]\nargs=["chrome-devtools-mcp@latest"]\n'
                '[mcp_servers.other]\nargs=["--slim","--browserUrl","http://127.0.0.1:9222","--no-usage-statistics","--no-performance-crux","chrome-devtools-mcp@1.4.0"]\n'
            )
            bindir.mkdir()
            make_bin(bindir, "npx", "exit 0\n")
            make_bin(
                bindir,
                "codex",
                'if [ "$1 $2 $3" = "mcp remove chrome-devtools" ]; then exit 1; fi; exit 0\n',
            )

            result = run_script(home, bindir)

            self.assertEqual(result.returncode, 0)
            self.assertIn("未 attach", result.stdout)
            self.assertNotIn("已符合", result.stdout)

    def test_repairs_latest_config_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            bindir = base / "bin"
            cfg = home / ".codex" / "config.toml"
            (home / ".codex").mkdir(parents=True)
            original = '[mcp_servers.chrome-devtools]\nargs=["chrome-devtools-mcp@latest","--slim","--browserUrl","http://127.0.0.1:9222"]\n'
            cfg.write_text(original)
            bindir.mkdir()
            log = base / "codex.log"
            make_bin(bindir, "npx", "exit 0\n")
            make_bin(
                bindir,
                "codex",
                f"""echo "$@" >> {log}
if [ "$1 $2 $3" = "mcp remove chrome-devtools" ]; then
  : > "$HOME/.codex/config.toml"
  exit 0
fi
if [ "$1 $2 $3" = "mcp add chrome-devtools" ]; then
  printf '[mcp_servers.chrome-devtools]\\nargs=[\\"%s\\",\\"%s\\",\\"%s\\",\\"%s\\",\\"%s\\",\\"%s\\",\\"%s\\",\\"%s\\"]\\n' "$5" "$6" "$7" "$8" "$9" "${{10}}" "${{11}}" "${{12}}" > "$HOME/.codex/config.toml"
  exit 0
fi
exit 1
""",
            )

            result = run_script(home, bindir)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("已修复", result.stdout)
            text = log.read_text()
            self.assertIn("mcp remove chrome-devtools", text)
            self.assertIn("mcp add chrome-devtools", text)
            repaired = cfg.read_text()
            self.assertIn("npx", repaired)
            self.assertIn("-y", repaired)
            self.assertIn("chrome-devtools-mcp@1.4.0", repaired)
            self.assertIn("--slim", repaired)
            self.assertIn("--browserUrl", repaired)
            self.assertIn("http://127.0.0.1:9222", repaired)
            self.assertIn("--no-usage-statistics", repaired)
            self.assertIn("--no-performance-crux", repaired)
            backups = list((home / ".codex").glob("config.toml.bak.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)

    def test_restore_original_config_when_add_fails(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            bindir = base / "bin"
            cfg = home / ".codex" / "config.toml"
            (home / ".codex").mkdir(parents=True)
            original = '[mcp_servers.chrome-devtools]\nargs=["chrome-devtools-mcp@latest"]\n'
            cfg.write_text(original)
            bindir.mkdir()
            make_bin(bindir, "npx", "exit 0\n")
            make_bin(
                bindir,
                "codex",
                """if [ "$1 $2 $3" = "mcp remove chrome-devtools" ]; then
  echo removed > "$HOME/.codex/config.toml"
  exit 0
fi
if [ "$1 $2 $3" = "mcp add chrome-devtools" ]; then
  exit 1
fi
exit 1
""",
            )

            result = run_script(home, bindir)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("自动重加失败", result.stdout)
            self.assertIn("codex mcp remove chrome-devtools && codex mcp add", result.stdout)
            self.assertEqual(cfg.read_text(), original)

    def test_missing_codex_dir_npx_or_cli_never_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            bindir = base / "bin"
            bindir.mkdir()

            result = run_script(home, bindir)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("无 ~/.codex", result.stdout)

            (home / ".codex").mkdir(parents=True)
            result = run_script(home, bindir)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("没有 node/npx", result.stdout)

            make_bin(bindir, "npx", "exit 0\n")
            result = run_script(home, bindir)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("没有 codex CLI", result.stdout)


if __name__ == "__main__":
    unittest.main()
