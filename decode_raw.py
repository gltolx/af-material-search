#!/usr/bin/env python3
"""把浏览器收割的编码 raw 文件解码还原成 merge_scored.py 期望的 harvest_* schema。
- xhs_raw.json  ({p:charCode编码explore URL, type, cover, title, imgs}) -> harvest_xhs.json
- dy_raw.json   ({sid:点分隔19位id, cover, title})                      -> harvest_douyin.json
- (中性垫片池 filler 平行轨)xhs_raw_filler.json -> harvest_xhs_filler.json;dy_raw_filler.json -> harvest_douyin_filler.json
解码逻辑严格照 browser_harvest_snippets.md(charCode 点分隔 / sid 去点)。
duration 此处不带(收割未采),由 backfill_duration.py 后补。system python3 跑即可(无三方依赖)。
"""
import json, os
RES = os.environ.get("BROLL_RES") or "results"

def load(p):
    fp = os.path.join(RES, p)
    return json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else []

def dec(code):  # charCode 数字码·点分隔 → 原串
    return "".join(chr(int(x)) for x in code.split(".")) if code else ""

def dec_xhs(items):
    out = []
    for x in items:
        page = dec(x.get("p") or "")   # https://www.xiaohongshu.com/explore/{id}?xsec_token={tok}&...
        if not page:
            continue
        out.append({"platform": "小红书", "title": (x.get("title") or "").strip(),
                    "url": page, "page": page, "cover": x.get("cover") or "", "type": x.get("type") or ""})
    return out

def dec_dy(items):
    out = []
    for x in items:
        vid = (x.get("sid") or "").replace(".", "")
        if not vid:
            continue
        out.append({"platform": "抖音", "title": (x.get("title") or "").strip(),
                    "url": "", "page": "https://www.douyin.com/video/" + vid, "cover": x.get("cover") or ""})
    return out

def dump(obj, name):
    json.dump(obj, open(os.path.join(RES, name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ---- 主题轨 ----
xhs_out = dec_xhs(load("xhs_raw.json")); dump(xhs_out, "harvest_xhs.json")
dy_out = dec_dy(load("dy_raw.json")); dump(dy_out, "harvest_douyin.json")
print(f"harvest_xhs.json: {len(xhs_out)}  (video={sum(1 for x in xhs_out if x.get('type')=='video')})")
print(f"harvest_douyin.json: {len(dy_out)}")

# ---- 中性垫片池 filler 平行轨(raw 文件存在才产;不影响主题档)----
xhs_f_raw = load("xhs_raw_filler.json"); dy_f_raw = load("dy_raw_filler.json")
if xhs_f_raw:
    xf = dec_xhs(xhs_f_raw); dump(xf, "harvest_xhs_filler.json")
    print(f"harvest_xhs_filler.json: {len(xf)}  (video={sum(1 for x in xf if x.get('type')=='video')})")
if dy_f_raw:
    df = dec_dy(dy_f_raw); dump(df, "harvest_douyin_filler.json")
    print(f"harvest_douyin_filler.json: {len(df)}")
