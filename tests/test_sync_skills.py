import os
import shutil
import subprocess
import tempfile
import unittest
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
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class SyncSkillsTests(unittest.TestCase):
    def test_sync_installs_both_skills_to_both_clients(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo with & chars"
            home = base / "home"
            repo.mkdir()
            (home / ".claude").mkdir(parents=True)
            (home / ".codex").mkdir(parents=True)
            copy_repo(repo)

            result = run(["bash", "./sync_skills.sh"], repo, home)

            self.assertEqual(result.returncode, 0, result.stdout)
            for client in [".claude", ".codex"]:
                for skill in ["broll", "broll-auto"]:
                    installed = home / client / "skills" / skill / "SKILL.md"
                    self.assertTrue(installed.exists())
                    text = installed.read_text()
                    self.assertNotIn("{{BROLL_HOME}}", text)
                    self.assertIn(str(repo), text)

    def test_sync_check_reports_drift(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            home = base / "home"
            repo.mkdir()
            (home / ".codex").mkdir(parents=True)
            copy_repo(repo)

            first = run(["bash", "./sync_skills.sh"], repo, home)
            self.assertEqual(first.returncode, 0, first.stdout)
            installed = home / ".codex" / "skills" / "broll" / "SKILL.md"
            installed.write_text(installed.read_text() + "\nDRIFT\n")

            result = run(["bash", "./sync_skills.sh", "--check"], repo, home)

            self.assertEqual(result.returncode, 2)
            self.assertTrue("漂移" in result.stdout or "drift" in result.stdout.lower())

    def test_sync_warns_without_clients_but_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            home = base / "home"
            repo.mkdir()
            home.mkdir()
            copy_repo(repo)

            result = run(["bash", "./sync_skills.sh"], repo, home)

            self.assertEqual(result.returncode, 0)
            self.assertTrue("没找到" in result.stdout or "无" in result.stdout)


if __name__ == "__main__":
    unittest.main()
