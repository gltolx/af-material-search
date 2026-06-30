#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BrollAutoCompatTests(unittest.TestCase):
    def test_autorun_selected_routes_theme_and_filler_to_script_kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp)
            (res / "verdicts.json").write_text(
                json.dumps(
                    [
                        {
                            "idx": 1,
                            "platform": "小红书",
                            "page": "https://www.xiaohongshu.com/explore/abc123def456",
                            "url": "",
                            "title": "主题素材 A",
                            "verdict": "keep",
                            "vscore": 91,
                        },
                        {
                            "idx": 2,
                            "platform": "B站/YT",
                            "page": "https://www.bilibili.com/video/BV1234567890",
                            "url": "",
                            "title": "主题素材 B",
                            "verdict": "review",
                            "vscore": 76,
                        },
                    ],
                    ensure_ascii=False,
                )
            )
            (res / "script_matches.json").write_text(
                json.dumps(
                    {
                        "s1": {
                            "name": "稿一",
                            "persona": "人设A",
                            "matched": [{"idx": 1, "stable_id": "xhs:abc"}],
                        },
                        "s2": {
                            "name": "稿二",
                            "persona": "人设B",
                            "matched": [{"idx": 2, "stable_id": "bili:bv"}],
                        },
                    },
                    ensure_ascii=False,
                )
            )
            (res / "kb_routing.json").write_text(
                json.dumps(
                    {
                        "links": {
                            "https://docs.example/a": {
                                "account": "a@example.com",
                                "kb_name": "库A",
                                "script_ids": ["s1"],
                            },
                            "https://docs.example/b": {
                                "account": "b@example.com",
                                "kb_name": "库B",
                                "script_ids": ["s2"],
                            },
                        }
                    },
                    ensure_ascii=False,
                )
            )
            (res / "verdicts_filler.json").write_text(
                json.dumps(
                    [
                        {
                            "idx": 10,
                            "platform": "抖音",
                            "page": "https://www.douyin.com/video/7312345678901234567",
                            "url": "",
                            "title": "键盘空镜",
                            "verdict": "keep",
                            "vscore": 88,
                        },
                        {
                            "idx": 1,
                            "platform": "小红书",
                            "page": "https://www.xiaohongshu.com/explore/dup",
                            "url": "",
                            "title": "重复 idx 不应入选",
                            "verdict": "keep",
                            "vscore": 99,
                        },
                    ],
                    ensure_ascii=False,
                )
            )
            (res / "filler_matches.json").write_text(
                json.dumps(
                    {
                        "s1": {
                            "name": "稿一",
                            "persona": "人设A",
                            "matched": [
                                {"idx": 10, "src_sentence": "镜头切到键盘敲击"},
                                {"idx": 1, "src_sentence": "重复 idx"},
                            ],
                        }
                    },
                    ensure_ascii=False,
                )
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "build_autorun_selected.py")],
                env={**os.environ, "BROLL_RES": str(res)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            selected = json.loads((res / "autorun_selected.json").read_text())
            self.assertEqual(len(selected), 3)

            theme_a = next(item for item in selected if item["title"] == "主题素材 A")
            self.assertEqual(theme_a["pool"], "theme")
            self.assertEqual(theme_a["source_link"], "https://docs.example/a")
            self.assertEqual(theme_a["account"], "a@example.com")
            self.assertEqual(theme_a["kb_name"], "库A")
            self.assertEqual(theme_a["script_name"], "稿一")
            self.assertEqual(theme_a["persona"], "人设A")
            self.assertEqual(theme_a["audit"], "pass")

            theme_b = next(item for item in selected if item["title"] == "主题素材 B")
            self.assertEqual(theme_b["source_link"], "https://docs.example/b")
            self.assertEqual(theme_b["account"], "b@example.com")
            self.assertEqual(theme_b["kb_name"], "库B")

            filler = next(item for item in selected if item["title"] == "键盘空镜")
            self.assertEqual(filler["pool"], "filler")
            self.assertEqual(filler["from_script"], "镜头切到键盘敲击")
            self.assertEqual(filler["source_link"], "https://docs.example/a")
            self.assertEqual(filler["account"], "a@example.com")
            self.assertEqual(filler["kb_name"], "库A")
            self.assertNotIn("重复 idx 不应入选", [item["title"] for item in selected])

            required = {
                "platform",
                "page",
                "url",
                "title",
                "verdict",
                "score",
                "script_name",
                "persona",
                "pool",
                "source_link",
                "account",
                "kb_name",
                "audit",
            }
            for item in selected:
                self.assertTrue(required.issubset(item.keys()), item)


if __name__ == "__main__":
    unittest.main()
