#!/usr/bin/env python3
"""R3+R4:有 script_matches.json 时,匹配卡片自动勾选 + 带 data-script/persona + 标签;欠匹配给提示。"""
import json, os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with tempfile.TemporaryDirectory() as tmp:
    cands = [
        {"idx":0,"platform":"B站/YT","title":"现场空镜","url":"https://www.bilibili.com/video/BV30000000cc","page":"https://www.bilibili.com/video/BV30000000cc","cover":"","duration":90,"stage0":"need_llm"},
        {"idx":1,"platform":"B站/YT","title":"另一条","url":"https://www.bilibili.com/video/BV40000000dd","page":"https://www.bilibili.com/video/BV40000000dd","cover":"","duration":80,"stage0":"need_llm"},
    ]
    scores = [
        {"idx":0,"score":80,"scene":"x","era":"x","reason":"现场","need_cover":False,"person_primary":"none"},
        {"idx":1,"score":75,"scene":"x","era":"x","reason":"备选","need_cover":False,"person_primary":"none"},
    ]
    spec = {"topic":"测试","plat_thresholds":{"B站/YT":{"keep_hi":62,"review_lo":40}},
            "max_duration_sec":1200,"person_penalty":{"dominant":35,"partial":12}}
    matches = {"s001":{"persona":"老王","name":"开场白稿","words":120,
                       "matched":[{"idx":0,"stable_id":"BV30000000cc","reason":"贴主旨","score":80}]}}
    for n, o in [("candidates.json",cands),("scores_part0.json",scores),
                 ("relevance_spec.json",spec),("script_matches.json",matches)]:
        json.dump(o, open(os.path.join(tmp,n),"w"), ensure_ascii=False)
    env = dict(os.environ, BROLL_RES=tmp)
    r = subprocess.run([sys.executable, os.path.join(REPO,"apply_verdicts.py")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    htmltext = open(os.path.join(tmp,"filtered.html"), encoding="utf-8").read()
    # idx0 被匹配 → 该 checkbox 带 checked + data-script + data-persona
    assert 'data-script="开场白稿"' in htmltext, "缺 data-script"
    assert 'data-persona="老王"' in htmltext, "缺 data-persona"
    # 该 checkbox 行带 checked(找到 data-script 后紧随 checked)
    seg = htmltext[htmltext.index('data-script="开场白稿"'):htmltext.index('data-script="开场白稿"')+80]
    assert "checked" in seg, ("匹配 checkbox 未自动勾选", seg)
    # 可见标签
    assert "开场白稿" in htmltext and "老王" in htmltext, "缺人设/稿名标签"
    # 欠匹配提示(s001 只 1 条 <2)
    assert "欠匹配" in htmltext, "缺欠匹配提示"
print("OK")
