#!/usr/bin/env python3
"""R4:生成的 filtered.html 里,两处 items.map 都带上 script_name/persona(从 data-script/data-persona 取)。"""
import json, os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with tempfile.TemporaryDirectory() as tmp:
    cands = [{"idx":0,"platform":"B站/YT","title":"t","url":"https://www.bilibili.com/video/BV30000000cc","page":"https://www.bilibili.com/video/BV30000000cc","cover":"","duration":90,"stage0":"need_llm"}]
    scores = [{"idx":0,"score":80,"reason":"x","need_cover":False,"person_primary":"none"}]
    spec = {"topic":"t","plat_thresholds":{"B站/YT":{"keep_hi":62,"review_lo":40}}}
    for n,o in [("candidates.json",cands),("scores_part0.json",scores),("relevance_spec.json",spec)]:
        json.dump(o, open(os.path.join(tmp,n),"w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(REPO,"apply_verdicts.py")],
                       env=dict(os.environ, BROLL_RES=tmp), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    h = open(os.path.join(tmp,"filtered.html"), encoding="utf-8").read()
    assert h.count("script_name:d.script") == 2, ("两处 map 都要带 script_name", h.count("script_name:d.script"))
    assert h.count("persona:d.persona") == 2, h.count("persona:d.persona")
print("OK")
