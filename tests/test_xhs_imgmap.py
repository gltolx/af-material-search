#!/usr/bin/env python3
"""共享建图:xhs_raw.json(p=charCode编码URL,type,imgs)→ {note_id:{t,imgs}}。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xhs_imgmap as xim

def enc(s):  # 模拟收割端 charCode 点分隔编码
    return ".".join(str(ord(c)) for c in s)

url = "https://www.xiaohongshu.com/explore/abc123def456?xsec_token=T&xsec_source=pc_search"
raw = [
    {"p": enc(url), "type": "normal", "cover": "c", "title": "图文", "imgs": ["http://img1", "http://img2"]},
    {"p": enc("https://www.xiaohongshu.com/explore/0011223344556677?xsec_token=X"), "type": "video", "imgs": []},
    {"p": "", "type": "normal", "imgs": ["x"]},   # 无法解析 → 跳过
]
m = xim.build_imgmap(raw)
assert m["abc123def456"] == {"t": "normal", "imgs": ["http://img1", "http://img2"]}, m
assert m["0011223344556677"] == {"t": "video", "imgs": []}, m
assert len(m) == 2, m   # 第三条 p 空 → 不进
# dec 往返
assert xim.dec(enc("世界杯/2002")) == "世界杯/2002"
print("OK")
