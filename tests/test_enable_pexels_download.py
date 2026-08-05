import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import enable_pexels_download as epd


SOURCE = '''<!doctype html>
<html><head><title>Pexels 视频素材结果页</title></head><body>
<p>🔒 Pexels 补充页仅验收；下载请点卡片进入原站，清洗/入库已禁用</p>
<button id="dlSel" class="dlbtn" style="display:none" aria-hidden="true" disabled>下载</button>
<button id="clnSel" class="dlbtn" style="display:none" disabled>清洗</button>
<section>
  <a data-plat="Pexels" data-page="https://www.pexels.com/video/one/" data-url="" href="#">one</a>
  <input type="checkbox" data-plat="Pexels" data-page="https://www.pexels.com/video/one/" data-url="">
  <a data-plat="Pexels" data-page="https://www.pexels.com/video/two/" data-url="" href="#">two</a>
  <input type="checkbox" data-plat="Pexels" data-page="https://www.pexels.com/video/two/" data-url="">
  <a data-plat="Pexels" data-page="https://www.pexels.com/video/three/" data-url="" href="#">three</a>
  <input type="checkbox" data-plat="Pexels" data-page="https://www.pexels.com/video/three/" data-url="">
  <a data-plat="Pexels" data-page="https://www.pexels.com/video/four/" data-url="" href="#">four</a>
  <input type="checkbox" data-plat="Pexels" data-page="https://www.pexels.com/video/four/" data-url="">
  <a data-plat="Bilibili" data-page="https://example.com/video/one/" data-url="keep" href="#">other</a>
</section>
<script id="disablePexelsDownloadGuard">document.getElementById('dlSel').style.display = 'none';</script>
<script id="disableCleaningGuard">document.getElementById('clnSel').style.display = 'none';</script>
 </body></html>
'''


class EnablePexelsDownloadTests(unittest.TestCase):
    def _write_fixture(self, result_dir: Path):
        page_path = result_dir / "filtered.html"
        page_path.write_text(SOURCE, encoding="utf-8")
        (result_dir / "harvest_pexels.json").write_text(
            json.dumps(
                [
                    {
                        "page": "https://www.pexels.com/video/one/",
                        "direct_url": "https://videos.pexels.com/video-files/one.mp4?x=1&y=2",
                    },
                    {
                        "page": "https://www.pexels.com/video/two/",
                        "direct_url": "https://videos.pexels.com/video-files/two.mp4",
                    },
                    {
                        "page": "https://www.pexels.com/video/three/",
                        "direct_url": "http://videos.pexels.com/video-files/three.mp4",
                    },
                    {
                        "page": "https://www.pexels.com/video/four/",
                        "direct_url": "https://videos.pexels.com.evil.example/video-files/four.mp4",
                    },
                    {
                        "page": "https://www.pexels.com/video/missing/",
                        "direct_url": "https://videos.pexels.com/video-files/not-video.jpg",
                    },
                ]
            ),
            encoding="utf-8",
        )
        return page_path

    def test_only_official_https_mp4_urls_are_injected_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir = Path(tmp)
            page_path = self._write_fixture(result_dir)

            updated = epd.enable_result_dir(result_dir)
            output = page_path.read_text(encoding="utf-8")

            self.assertEqual(updated, 2)
            self.assertEqual(output.count("https://videos.pexels.com/"), 4)
            self.assertIn("?x=1&amp;y=2", output)
            self.assertNotIn("http://videos.pexels.com/video-files/three.mp4", output)
            self.assertNotIn("videos.pexels.com.evil.example", output)
            self.assertEqual(output.count('data-page="https://www.pexels.com/video/three/" data-url=""'), 2)
            self.assertEqual(output.count('data-page="https://www.pexels.com/video/four/" data-url=""'), 2)

    def test_page_controls_and_non_pexels_content_remain_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir = Path(tmp)
            page_path = self._write_fixture(result_dir)
            epd.enable_result_dir(result_dir)
            output = page_path.read_text(encoding="utf-8")

            self.assertIn('id="dlSel"', output)
            self.assertNotIn('id="dlSel" class="dlbtn" style="display:none"', output)
            self.assertNotIn('id="disablePexelsDownloadGuard"', output)
            self.assertIn('id="clnSel" class="dlbtn" style="display:none"', output)
            self.assertIn('id="disableCleaningGuard"', output)
            self.assertIn("<title>Pexels 视频素材结果页</title>", output)
            self.assertIn("<p>Pexels 支持批量下载；清洗/入库已禁用</p>", output)
            self.assertIn(
                'data-plat="Bilibili" data-page="https://example.com/video/one/" data-url="keep"',
                output,
            )
            self.assertEqual(epd.enable_result_dir(result_dir), 2)

    def test_atomic_replace_preserves_existing_filtered_html_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir = Path(tmp)
            page_path = self._write_fixture(result_dir)
            os.chmod(page_path, 0o640)

            epd.enable_result_dir(result_dir)

            self.assertEqual(stat.S_IMODE(page_path.stat().st_mode), 0o640)

    def test_cli_and_missing_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir = Path(tmp)
            self._write_fixture(result_dir)
            cli = subprocess.run(
                [sys.executable, str(REPO / "enable_pexels_download.py"), str(result_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli.returncode, 0)
            self.assertEqual(cli.stdout, "2\n")

        missing_arg = subprocess.run(
            [sys.executable, str(REPO / "enable_pexels_download.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(missing_arg.returncode, 0)
        self.assertIn("usage:", missing_arg.stderr)


if __name__ == "__main__":
    unittest.main()
