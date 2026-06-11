#!/usr/bin/env python3
"""集成:merge_scored.py 在 xhs_raw.json 存在时,顺手自动产 xhs_imgs.json。"""
import json, os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def enc(s):
    return ".".join(str(ord(c)) for c in s)

with tempfile.TemporaryDirectory() as tmp:
    url = "https://www.xiaohongshu.com/explore/abc123def456?xsec_token=T"
    # 归一化后的 harvest_xhs.json(merge 读它建 scored)
    json.dump([{"platform":"小红书","title":"图文","url":url,"page":url,"cover":"c"}],
              open(os.path.join(tmp,"harvest_xhs.json"),"w"), ensure_ascii=False)
    # master:带 type/imgs
    json.dump([{"p":enc(url),"type":"normal","cover":"c","title":"图文","imgs":["http://img1"]}],
              open(os.path.join(tmp,"xhs_raw.json"),"w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(REPO,"merge_scored.py")],
                       env=dict(os.environ, BROLL_RES=tmp), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(os.path.join(tmp,"scored.json")), "scored.json 应产出"
    p = os.path.join(tmp,"xhs_imgs.json")
    assert os.path.exists(p), "merge 应自动产 xhs_imgs.json"
    m = json.load(open(p))
    assert m["abc123def456"] == {"t":"normal","imgs":["http://img1"]}, m
print("OK")
