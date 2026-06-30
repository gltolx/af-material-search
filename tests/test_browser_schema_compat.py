#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def enc(text: str) -> str:
    return ".".join(str(ord(c)) for c in text)


class BrowserSchemaCompatTests(unittest.TestCase):
    def test_merge_preserves_pool_duration_and_xhs_image_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp)
            theme_url = "https://www.xiaohongshu.com/explore/abc123def456?xsec_token=T"
            filler_url = "https://www.xiaohongshu.com/explore/001122334455?xsec_token=F"

            (res / "harvest_xhs.json").write_text(
                json.dumps(
                    [
                        {
                            "platform": "小红书",
                            "title": "主题图文",
                            "url": theme_url,
                            "page": theme_url,
                            "cover": "theme-cover",
                            "duration": 12,
                        }
                    ],
                    ensure_ascii=False,
                )
            )
            (res / "harvest_xhs_filler.json").write_text(
                json.dumps(
                    [
                        {
                            "platform": "小红书",
                            "title": "重复但来自 filler",
                            "url": theme_url,
                            "page": theme_url,
                            "cover": "dup-cover",
                            "duration": 8,
                        },
                        {
                            "platform": "小红书",
                            "title": "中性空镜",
                            "url": filler_url,
                            "page": filler_url,
                            "cover": "filler-cover",
                            "duration": 6,
                        },
                    ],
                    ensure_ascii=False,
                )
            )
            (res / "harvest_douyin_filler.json").write_text(
                json.dumps(
                    [
                        {
                            "platform": "抖音",
                            "title": "键盘特写",
                            "page": "https://www.douyin.com/video/7312345678901234567",
                            "cover": "dy-cover",
                            "duration": 5,
                        },
                        {"platform": "抖音", "title": "", "page": "", "cover": ""},
                    ],
                    ensure_ascii=False,
                )
            )
            (res / "xhs_raw.json").write_text(
                json.dumps(
                    [{"p": enc(theme_url), "type": "video", "imgs": []}],
                    ensure_ascii=False,
                )
            )
            (res / "xhs_raw_filler.json").write_text(
                json.dumps(
                    [{"p": enc(filler_url), "type": "normal", "imgs": ["http://img/filler.jpg"]}],
                    ensure_ascii=False,
                )
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "merge_scored.py")],
                env={**os.environ, "BROLL_RES": str(res)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            scored = json.loads((res / "scored.json").read_text())
            by_page = {item["page"]: item for item in scored}
            self.assertEqual(by_page[theme_url]["src_pool"], "theme")
            self.assertEqual(by_page[theme_url]["duration"], 12)
            self.assertEqual(by_page[filler_url]["src_pool"], "filler")
            self.assertEqual(by_page[filler_url]["duration"], 6)
            self.assertEqual(len([item for item in scored if item["page"] == theme_url]), 1)
            self.assertEqual(sum(1 for item in scored if item["src_pool"] == "filler"), 2)

            imgmap = json.loads((res / "xhs_imgs.json").read_text())
            self.assertEqual(imgmap["abc123def456"], {"t": "video", "imgs": []})
            self.assertEqual(
                imgmap["001122334455"],
                {"t": "normal", "imgs": ["http://img/filler.jpg"]},
            )


if __name__ == "__main__":
    unittest.main()
