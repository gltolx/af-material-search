import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class TextContractTests(unittest.TestCase):
    def test_runtime_text_has_no_stale_claude_only_phrasing(self):
        targets = [
            "skills/broll/SKILL.md",
            "skills/broll-auto/SKILL.md",
            "browser_harvest_codex.md",
        ]
        forbidden = [
            "AskUserQuestion",
            "Codex 旁注",
            "只有 Codex 看",
            "Claude 绝不自动",
        ]
        for name in targets:
            text = read(name)
            for phrase in forbidden:
                self.assertNotIn(phrase, text, f"{name} still contains {phrase}")

    def test_broll_skill_declares_runtime_adapter(self):
        text = read("skills/broll/SKILL.md")
        for phrase in [
            "运行时适配层",
            "Claude Code",
            "Codex",
            "browser_harvest_snippets.md",
            "browser_harvest_codex.md",
            "codex_chrome.sh",
        ]:
            self.assertIn(phrase, text)

    def test_codex_harvest_doc_uses_sid_and_json_version_contract(self):
        text = read("browser_harvest_codex.md")
        for phrase in [
            "xhsAll_<SID>",
            "dyAll_<SID>",
            "BROLL_RES 末段",
            "xhs_raw.json",
            "dy_raw.json",
            "/json/version",
        ]:
            self.assertIn(phrase, text)
        self.assertIn("不要打开裸 http://127.0.0.1:9222/", text)

    def test_usage_docs_include_youtube_login(self):
        self.assertIn("YouTube", read("USAGE-Claude.md"))
        codex = read("USAGE-Codex.md")
        self.assertIn("YouTube", codex)
        self.assertIn("/json/version", codex)


if __name__ == "__main__":
    unittest.main()
