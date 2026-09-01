import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import download_server as ds


class DouyinYtDlpFallbackTests(unittest.TestCase):
    def test_uses_logged_in_ytdlp_when_iesdouyin_schema_has_no_play(self):
        item = {
            "platform": "抖音",
            "title": "奥克斯空调",
            "page": "https://www.douyin.com/video/7621255775062592820",
            "url": "",
        }
        calls = []

        with tempfile.TemporaryDirectory() as tmp:
            fake_ytdlp = Path(tmp, "yt-dlp-new")
            fake_ytdlp.write_text("", encoding="utf-8")
            outbase = os.path.join(tmp, "clip")

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                output = cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4")
                Path(output).write_bytes(b"x" * 2048)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.object(ds, "dy_resolve", return_value={"play": "", "err": "'videoInfoRes'"}), \
                    mock.patch.object(ds, "YTDLP_NEW", str(fake_ytdlp)), \
                    mock.patch.object(ds.subprocess, "run", side_effect=fake_run):
                fn, err = ds.dl_douyin(item, outbase)

            self.assertEqual(fn, outbase + ".mp4")
            self.assertEqual(err, "")
            self.assertEqual(len(calls), 1)
            command = calls[0][0]
            self.assertEqual(command[0], str(fake_ytdlp))
            self.assertIn("--cookies-from-browser", command)
            self.assertIn("chrome", command)
            self.assertIn(item["page"], command)


if __name__ == "__main__":
    unittest.main()
