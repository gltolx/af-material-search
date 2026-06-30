import os
import shutil
import subprocess
import tempfile
import unittest
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
    return subprocess.run(
        ["bash", "./selfupdate.sh"],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class SelfupdateSafeTests(unittest.TestCase):
    def test_selfupdate_non_git_still_syncs_skills(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            home = base / "home"
            repo.mkdir()
            (home / ".codex").mkdir(parents=True)
            copy_min_repo(repo)

            result = run_selfupdate(repo, home)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((home / ".codex" / "skills" / "broll" / "SKILL.md").exists())

    def test_selfupdate_already_latest_refreshes_drifted_skill(self):
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

            sync = subprocess.run(
                ["bash", "./sync_skills.sh"],
                cwd=repo,
                env={**os.environ, "HOME": str(home)},
                text=True,
                stdout=subprocess.PIPE,
            )
            self.assertEqual(sync.returncode, 0)
            installed = home / ".claude" / "skills" / "broll" / "SKILL.md"
            installed.write_text("__DRIFT_SENTINEL__")

            result = run_selfupdate(repo, home)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("__DRIFT_SENTINEL__", installed.read_text())


if __name__ == "__main__":
    unittest.main()
