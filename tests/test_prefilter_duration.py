#!/usr/bin/env python3
"""R2 预过滤:已知 duration>1200 的候选直接 kill(省 AI 打分)。"""
import json, os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with tempfile.TemporaryDirectory() as tmp:
    scored = [
        {"platform":"B站/YT","title":"超长片","url":"u1","page":"p1","cover":"","duration":1500},
        {"platform":"B站/YT","title":"正常片","url":"u2","page":"p2","cover":"","duration":100},
        {"platform":"B站/YT","title":"未知时长","url":"u3","page":"p3","cover":"","duration":None},
    ]
    spec = {"topic":"t","negative_terms":[],"max_duration_sec":1200}
    json.dump(scored, open(os.path.join(tmp,"scored.json"),"w"), ensure_ascii=False)
    json.dump(spec, open(os.path.join(tmp,"relevance_spec.json"),"w"), ensure_ascii=False)
    env = dict(os.environ, BROLL_RES=tmp)
    r = subprocess.run([sys.executable, os.path.join(REPO,"score_candidates.py")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    cands = json.load(open(os.path.join(tmp,"candidates.json")))
    by = {c["title"]: c for c in cands}
    assert by["超长片"]["stage0"] == "kill" and "超20" in by["超长片"].get("kill_reason",""), by["超长片"]
    assert by["正常片"]["stage0"] == "need_llm", by["正常片"]      # 未超长不动
    assert by["未知时长"]["stage0"] == "need_llm", by["未知时长"]  # 时长缺失放行(留给 apply_verdicts 回填后再卡)
print("OK")
