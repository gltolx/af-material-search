import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def make_bin(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def run_script(base: Path, bindir: Path, extra_env=None):
    extra_env = extra_env or {}
    chrome_override = extra_env.get("CHROME_BIN")
    if chrome_override:
        chrome = Path(chrome_override)
    else:
        chrome = make_bin(bindir, "chrome", f'echo "$@" >> "{base / "chrome.log"}"\n')
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "CHROME_BIN": str(chrome),
            "CODEX_HARVEST_PROFILE": str(base / "profile"),
            "CODEX_HARVEST_PORT": "19222",
        }
    )
    env.update(extra_env)
    return subprocess.run(
        ["/bin/bash", str(ROOT / "codex_chrome.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class CodexChromeTests(unittest.TestCase):
    def test_reuses_existing_debug_port(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bindir = base / "bin"
            bindir.mkdir()
            make_bin(bindir, "curl", f'echo "$@" >> "{base / "curl.log"}"\nexit 0\n')
            make_bin(bindir, "open", f'echo "$@" >> "{base / "open.log"}"\nexit 0\n')

            result = run_script(base, bindir)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("复用", result.stdout)
            self.assertIn("/json/version", (base / "curl.log").read_text())
            self.assertFalse((base / "open.log").exists())

    def test_darwin_launches_with_open_and_waits_for_json_version(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bindir = base / "bin"
            bindir.mkdir()
            counter = base / "curl-count"
            make_bin(
                bindir,
                "curl",
                f"""count=0
[ -f "{counter}" ] && count=$(cat "{counter}")
count=$((count + 1))
echo "$count" > "{counter}"
echo "$@" >> "{base / "curl.log"}"
[ "$count" -ge 3 ] && exit 0
exit 7
""",
            )
            make_bin(bindir, "uname", "echo Darwin\n")
            make_bin(bindir, "sleep", "exit 0\n")
            make_bin(bindir, "open", f'echo "$@" >> "{base / "open.log"}"\nexit 0\n')

            result = run_script(base, bindir)

            self.assertEqual(result.returncode, 0, result.stdout)
            opened = (base / "open.log").read_text()
            self.assertIn("-na Google Chrome --args", opened)
            self.assertIn("--remote-debugging-port=19222", opened)
            self.assertIn(f"--user-data-dir={base / 'profile'}", opened)
            self.assertIn("about:blank", opened)
            self.assertIn("json/version", result.stdout)
            self.assertEqual(counter.read_text().strip(), "3")

    def test_removes_stale_singleton_locks_when_profile_has_no_process(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bindir = base / "bin"
            bindir.mkdir()
            profile = base / "profile"
            profile.mkdir()
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                (profile / name).write_text("stale")
            make_bin(bindir, "curl", f'echo "$@" >> "{base / "curl.log"}"\n[ -f "{base / "launched"}" ] && exit 0\nexit 7\n')
            make_bin(bindir, "uname", "echo Darwin\n")
            make_bin(bindir, "pgrep", "exit 1\n")
            make_bin(bindir, "sleep", "exit 0\n")
            make_bin(bindir, "open", f'touch "{base / "launched"}"\nexit 0\n')

            result = run_script(base, bindir)

            self.assertEqual(result.returncode, 0, result.stdout)
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                self.assertFalse((profile / name).exists())
            self.assertIn("清理陈旧 Chrome profile 锁", result.stdout)

    def test_non_darwin_launches_chrome_binary_directly(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bindir = base / "bin"
            bindir.mkdir()
            make_bin(bindir, "curl", f'echo "$@" >> "{base / "curl.log"}"\n[ -f "{base / "launched"}" ] && exit 0\nexit 7\n')
            make_bin(bindir, "uname", "echo Linux\n")
            make_bin(bindir, "sleep", "exit 0\n")
            chrome = make_bin(bindir, "chrome", f'touch "{base / "launched"}"\necho "$@" >> "{base / "chrome.log"}"\n')

            result = run_script(base, bindir, {"CHROME_BIN": str(chrome)})

            self.assertEqual(result.returncode, 0, result.stdout)
            launched = (base / "chrome.log").read_text()
            self.assertIn("--remote-debugging-port=19222", launched)
            self.assertIn("about:blank", launched)


if __name__ == "__main__":
    unittest.main()
