#!/usr/bin/env python3
"""R1 人物主体降分 + R2 超20分钟弃:造 fixture 跑 apply_verdicts.py,断言三色。"""
import json, os, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run(tmp):
    cands = [
        {"idx":0,"platform":"B站/YT","title":"超长视频","url":"https://www.bilibili.com/video/BV10000000aa","page":"https://www.bilibili.com/video/BV10000000aa","cover":"","duration":1500,"stage0":"need_llm"},
        {"idx":1,"platform":"B站/YT","title":"单人说话头","url":"https://www.bilibili.com/video/BV20000000bb","page":"https://www.bilibili.com/video/BV20000000bb","cover":"","duration":120,"stage0":"need_llm"},
        {"idx":2,"platform":"B站/YT","title":"现场空镜","url":"https://www.bilibili.com/video/BV30000000cc","page":"https://www.bilibili.com/video/BV30000000cc","cover":"","duration":90,"stage0":"need_llm"},
    ]
    scores = [
        {"idx":0,"score":90,"scene":"x","era":"x","reason":"长","need_cover":False,"person_primary":"none"},
        {"idx":1,"score":80,"scene":"x","era":"x","reason":"单人","need_cover":False,"person_primary":"dominant"},
        {"idx":2,"score":80,"scene":"x","era":"x","reason":"现场","need_cover":False,"person_primary":"none"},
    ]
    spec = {"topic":"测试","plat_thresholds":{"B站/YT":{"keep_hi":62,"review_lo":40}},
            "max_duration_sec":1200,"person_penalty":{"dominant":35,"partial":12}}
    json.dump(cands, open(os.path.join(tmp,"candidates.json"),"w"), ensure_ascii=False)
    json.dump(scores, open(os.path.join(tmp,"scores_part0.json"),"w"), ensure_ascii=False)
    json.dump(spec, open(os.path.join(tmp,"relevance_spec.json"),"w"), ensure_ascii=False)
    env = dict(os.environ, BROLL_RES=tmp)
    r = subprocess.run([sys.executable, os.path.join(REPO,"apply_verdicts.py")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return {c["idx"]: c for c in json.load(open(os.path.join(tmp,"verdicts.json")))}

with tempfile.TemporaryDirectory() as tmp:
    v = run(tmp)
    assert v[0]["verdict"] == "drop" and "超20分钟" in v[0]["vreason"], v[0]   # R2
    assert v[1]["verdict"] == "review", v[1]                                   # R1:80-35=45 → review
    assert "人物主体" in v[1]["vreason"], v[1]
    assert v[2]["verdict"] == "keep", v[2]                                     # 对照:80 none → keep
print("OK")
