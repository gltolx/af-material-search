import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import download_server as ds


ITEM = {
    "platform": "Pexels",
    "title": "团队办公",
    "page": "https://www.pexels.com/zh-cn/video/8479048/",
    "url": "https://videos.pexels.com/video-files/8479048/sample.mp4",
}


class FakeResponse:
    def __init__(self, status_code=200, chunks=()):
        self.status_code = status_code
        self._chunks = chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)

    def iter_content(self, _chunk_size):
        for chunk in self._chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class PexelsDownloadTests(unittest.TestCase):
    def test_platform_and_native_id_filename(self):
        self.assertEqual(ds.platform_of(ITEM), "pexels")
        self.assertTrue(ds.build_name(ITEM).startswith("Pexels_8479048_"))

    def test_rejects_non_official_https_mp4_urls_without_request(self):
        invalid_urls = (
            "",
            "http://videos.pexels.com/video-files/1/clip.mp4",
            "https://videos.pexels.com.evil.example/clip.mp4",
            "https://example.com/evil.mp4",
            "https://videos.pexels.com/video-files/1/clip.mov",
        )
        get = mock.Mock()
        with mock.patch.dict(sys.modules, {"requests": SimpleNamespace(get=get)}):
            for url in invalid_urls:
                item = dict(ITEM, url=url)
                item.pop("direct_url", None)
                fn, err = ds.dl_pexels(item, "/tmp/never-write")
                self.assertIsNone(fn)
                self.assertTrue(err)
        get.assert_not_called()

    def test_rejects_redirects_and_disables_automatic_following(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse(status_code=302, chunks=(b"x" * 2048,))

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(sys.modules, {"requests": SimpleNamespace(get=fake_get)}), \
                mock.patch.object(ds.time, "sleep", return_value=None):
            outbase = os.path.join(tmp, "clip")
            fn, err = ds.dl_pexels(ITEM, outbase)

            self.assertIsNone(fn)
            self.assertIn("30", err)
            self.assertFalse(os.path.exists(outbase + ".mp4"))
            self.assertFalse(os.path.exists(outbase + ".mp4.part"))

        self.assertGreaterEqual(len(calls), 1)
        self.assertTrue(all(call[1].get("allow_redirects") is False for call in calls))

    def test_success_is_published_atomically_from_same_directory_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            outbase = os.path.join(tmp, "clip")
            final_path = outbase + ".mp4"
            part_path = final_path + ".part"

            class AtomicResponse(FakeResponse):
                def iter_content(self, _chunk_size):
                    self_outer.assertFalse(os.path.exists(final_path))
                    self_outer.assertTrue(os.path.exists(part_path))
                    yield b"x" * 2048

            self_outer = self
            calls = []

            def fake_get(url, **kwargs):
                calls.append((url, kwargs))
                return AtomicResponse()

            with mock.patch.dict(sys.modules, {"requests": SimpleNamespace(get=fake_get)}), \
                    mock.patch.object(ds.time, "sleep", return_value=None):
                fn, err = ds.dl_pexels(ITEM, outbase)

            self.assertEqual(err, "")
            self.assertEqual(fn, final_path)
            self.assertEqual(os.path.getsize(final_path), 2048)
            self.assertFalse(os.path.exists(part_path))
            self.assertEqual(calls[0][1].get("allow_redirects"), False)

    def test_interruption_cleans_part_and_next_process_retries_instead_of_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            outbase = os.path.join(tmp, ds.build_name(ITEM))
            interrupted_calls = []

            def interrupted_get(url, **kwargs):
                interrupted_calls.append((url, kwargs))
                return FakeResponse(chunks=(b"x" * 2048, RuntimeError("connection interrupted")))

            with mock.patch.dict(sys.modules, {"requests": SimpleNamespace(get=interrupted_get)}), \
                    mock.patch.object(ds.time, "sleep", return_value=None):
                fn, err = ds.dl_pexels(ITEM, outbase)

            self.assertIsNone(fn)
            self.assertIn("interrupted", err)
            self.assertFalse(os.path.exists(outbase + ".mp4"))
            self.assertFalse(os.path.exists(outbase + ".mp4.part"))

            successful_calls = []

            def successful_get(url, **kwargs):
                successful_calls.append((url, kwargs))
                return FakeResponse(chunks=(b"y" * 2048,))

            with mock.patch.dict(sys.modules, {"requests": SimpleNamespace(get=successful_get)}), \
                    mock.patch.object(ds.time, "sleep", return_value=None):
                result = ds.process(ITEM, tmp)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(successful_calls), 1)
            self.assertTrue(os.path.exists(outbase + ".mp4"))
            self.assertTrue(all(call[1].get("allow_redirects") is False
                                for call in interrupted_calls + successful_calls))


if __name__ == "__main__":
    unittest.main()
