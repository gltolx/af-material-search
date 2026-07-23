#!/usr/bin/env python3
"""filtered.html：稳定选择 ID、持久化和已选工作台生成契约。"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with tempfile.TemporaryDirectory() as tmp:
    candidates = [
        {
            "idx": 0,
            "platform": "B站/YT",
            "title": "主题现场",
            "url": "https://www.bilibili.com/video/BV1234567890",
            "page": "https://www.bilibili.com/video/BV1234567890",
            "cover": "",
            "duration": 80,
            "stage0": "need_llm",
        },
        {
            "idx": 1,
            "platform": "小红书",
            "title": "窗边空镜",
            "url": "",
            "page": "https://www.xiaohongshu.com/explore/abcdef123456?xsec_token=fresh",
            "cover": "",
            "duration": 12,
            "stage0": "need_llm_filler",
            "pool": "filler",
        },
    ]
    scores = [{
        "idx": 0,
        "score": 82,
        "scene": "现场",
        "era": "当代",
        "reason": "主题匹配",
        "need_cover": False,
        "person_primary": "none",
    }]
    filler_scores = [{
        "idx": 1,
        "score": 75,
        "scene": "空镜",
        "era": "当代",
        "reason": "干净中性",
        "need_cover": False,
        "person_primary": "none",
    }]
    spec = {
        "topic": "选择工作台测试",
        "plat_thresholds": {"B站/YT": {"keep_hi": 62, "review_lo": 40}},
        "max_duration_sec": 1200,
        "person_penalty": {"dominant": 35, "partial": 12},
        "filler": {
            "enable": True,
            "max_dur_sec": 25,
            "per_script_cap": 6,
            "plat_thresholds": {"keep_hi": 55, "review_lo": 35},
        },
    }
    script_matches = {
        "s001": {
            "persona": "测试人设",
            "name": "主题稿",
            "words": 120,
            "matched": [{"idx": 0, "stable_id": "BV1234567890", "reason": "贴题", "score": 82}],
        }
    }
    filler_matches = {
        "s001": {
            "persona": "测试人设",
            "name": "主题稿",
            "matched": [{"idx": 1, "src_sentence": "窗边的安静画面"}],
        }
    }
    files = {
        "candidates.json": candidates,
        "scores_part0.json": scores,
        "scores_filler_part0.json": filler_scores,
        "relevance_spec.json": spec,
        "script_matches.json": script_matches,
        "filler_matches.json": filler_matches,
    }
    for name, payload in files.items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "apply_verdicts.py")],
        env=dict(os.environ, BROLL_RES=tmp),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    html = open(os.path.join(tmp, "filtered.html"), encoding="utf-8").read()

    assert 'data-id="BV1234567890"' in html
    assert 'data-id="xhs_abcdef123456"' in html
    assert 'id="showSelected"' in html
    assert 'id="selectedWorkspace"' in html
    assert 'var SELECTION_KEY="af_selection:' in html
    assert 'BASE+"/selection"' in html
    assert 'localStorage.setItem(SELECTION_KEY' in html
    assert "known_ids" in html and "selected_ids" in html and "revision" in html
    for token in [
        "selectionHomes",
        "document.createComment",
        "selectedGrid.appendChild",
        "restoreCard",
        "setSelectedMode",
        "返回全部素材",
        'e.key==="F1"',
        "focusNextSelected",
        "scrollIntoView",
        'block:"center"',
        'closest("details")',
        "focus-hit",
        "selection-restoring",
        "AbortController",
        "base_revision",
        "namespace:SELECTION_NS",
        "hasRemoved",
        "pendingOverrides",
        "replayOverrides();",
        "clearConfirmed(sentOverrides)",
    ]:
        assert token in html, token

    script = re.search(r"<script>(.*)</script>", html, re.S)
    assert script, "生成页缺 script"
    if shutil.which("node"):
        js_path = os.path.join(tmp, "selection_workspace.js")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(script.group(1))
        checked = subprocess.run(["node", "--check", js_path], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr

        override_match = re.search(
            r"/\* selection-overrides:start.*?\*/(.*?)/\* selection-overrides:end \*/",
            script.group(1),
            re.S,
        )
        assert override_match, "缺 selection override 可执行区块"
        override_block = override_match.group(1)
        override_harness = """
var boxes=[{dataset:{id:"a"},checked:false},{dataset:{id:"b"},checked:true},{dataset:{id:"c"},checked:true}];
function allSels(){return boxes;}
""" + override_block + """
recordOverride("a",true);recordOverride("b",false);
var sent=captureOverrides();
replayOverrides();
if(!boxes[0].checked||boxes[1].checked||!boxes[2].checked)throw new Error("409 未重放本地 override");
recordOverride("a",false);clearConfirmed(sent);
if(!pendingOverrides.a||pendingOverrides.a.checked!==false)throw new Error("清除了后续较新的操作");
if(pendingOverrides.b)throw new Error("已确认操作未清除");
boxes[2].checked=true;
var latest={selected_ids:["a","c"],known_ids:["a","b","c"]};
var desired={selected_ids:["b"],known_ids:["a","b"]};
pendingOverrides={};recordStateDiff(latest,desired);replayOverrides();
if(boxes[0].checked||!boxes[1].checked)throw new Error("离线状态差异未记录/重放");
if(!boxes[2].checked)throw new Error("旧 local 不应取消新页面的 AI 默认勾选");
"""
        replay = subprocess.run(["node", "-e", override_harness], capture_output=True, text=True)
        assert replay.returncode == 0, replay.stderr

print("OK")
