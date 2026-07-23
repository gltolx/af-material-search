#!/usr/bin/env python3
"""filtered 页面选择状态：结果目录级持久化、校验与原子覆盖。"""
import glob
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import download_server as ds


original_res = ds.RES
try:
    with tempfile.TemporaryDirectory() as tmp:
        ds.RES = tmp

        missing = ds.load_selection_state()
        assert missing == {
            "initialized": False,
            "version": 1,
            "revision": 0,
            "known_ids": [],
            "selected_ids": [],
            "updated_at": "",
        }, missing

        saved = ds.save_selection_state({
            "version": 1,
            "revision": 3,
            "known_ids": ["BV1234567890", "BV1234567890", "xhs_abcd12345678"],
            "selected_ids": [],
        })
        assert saved["revision"] == 3, saved
        assert saved["known_ids"] == ["BV1234567890", "xhs_abcd12345678"], saved
        assert saved["selected_ids"] == [], saved
        assert saved["updated_at"], saved

        loaded = ds.load_selection_state()
        assert loaded["initialized"] is True, loaded
        assert loaded["revision"] == 3, loaded
        assert loaded["selected_ids"] == [], loaded
        assert not glob.glob(os.path.join(tmp, ".selection_state.*.tmp")), os.listdir(tmp)

        namespace = ds.selection_namespace()
        assert len(namespace) == 16, namespace
        with tempfile.TemporaryDirectory() as other:
            ds.RES = other
            assert ds.selection_namespace() != namespace
        ds.RES = tmp

        try:
            ds.save_selection_state({
                "version": 1,
                "revision": 2,
                "known_ids": ["BV1234567890"],
                "selected_ids": ["BV1234567890"],
            })
            raise AssertionError("旧 revision 不得覆盖新状态")
        except ds.SelectionConflict as exc:
            assert exc.current["revision"] == 3, exc.current

        try:
            ds.save_selection_state({
                "version": 1,
                "revision": 4,
                "known_ids": ["BV1234567890"],
                "selected_ids": ["BV1234567890"],
            }, base_revision=2)
            raise AssertionError("错误 base_revision 应冲突")
        except ds.SelectionConflict as exc:
            assert exc.current["revision"] == 3, exc.current

        cas_saved = ds.save_selection_state({
            "version": 1,
            "revision": 4,
            "known_ids": ["BV1234567890"],
            "selected_ids": ["BV1234567890"],
        }, base_revision=3)
        assert cas_saved["revision"] == 4, cas_saved

        try:
            ds.save_selection_state({
                "version": 1,
                "revision": 4,
                "known_ids": ["known"],
                "selected_ids": ["unknown"],
            })
            raise AssertionError("selected_ids 非 known_ids 子集时应拒绝")
        except ValueError as exc:
            assert "subset" in str(exc), exc

        try:
            ds.save_selection_state({
                "version": 1,
                "revision": -1,
                "known_ids": [],
                "selected_ids": [],
            })
            raise AssertionError("负 revision 应拒绝")
        except ValueError as exc:
            assert "revision" in str(exc), exc

        with open(os.path.join(tmp, "selection_state.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        try:
            ds.load_selection_state()
            raise AssertionError("损坏 JSON 应显式报错")
        except ValueError as exc:
            assert "invalid selection state" in str(exc), exc
finally:
    ds.RES = original_res

source = open(os.path.join(REPO, "download_server.py"), encoding="utf-8").read()
assert 'parsed.path == "/selection"' in source, "缺 /selection 路由"
assert "load_selection_state()" in source, "GET 未读取选择状态"
assert "save_selection_state(body" in source, "POST 未保存选择状态"
assert 'Access-Control-Allow-Methods", "GET,POST,OPTIONS"' in source, "CORS 未开放 GET"
assert 'body.get("namespace") != selection_namespace()' in source, "POST 未绑定当前 BROLL_RES namespace"
assert "SelectionConflict" in source and "409" in source, "缺 revision CAS 冲突响应"

print("OK")
