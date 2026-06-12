#!/usr/bin/env python3
"""把浏览器收割的编码 raw 文件解码还原成 merge_scored.py 期望的 harvest_* schema。
- xhs_raw.json  ({p:charCode编码explore URL, type, cover, title, imgs}) -> harvest_xhs.json
- dy_raw.json   ({sid:点分隔19位id, cover, title})                      -> harvest_douyin.json
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

# ---- 小红书 ----
xhs_out = []
for x in load("xhs_raw.json"):
    page = dec(x.get("p") or "")   # https://www.xiaohongshu.com/explore/{id}?xsec_token={tok}&...
    if not page:
        continue
    xhs_out.append({
        "platform": "小红书",
        "title": (x.get("title") or "").strip(),
        "url": page,
        "page": page,
        "cover": x.get("cover") or "",
        "type": x.get("type") or "",
    })
json.dump(xhs_out, open(os.path.join(RES, "harvest_xhs.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# ---- 抖音 ----
dy_out = []
for x in load("dy_raw.json"):
    vid = (x.get("sid") or "").replace(".", "")
    if not vid:
        continue
    dy_out.append({
        "platform": "抖音",
        "title": (x.get("title") or "").strip(),
        "url": "",  # 无水印解析延后到选片后(resolve_douyin.py)
        "page": "https://www.douyin.com/video/" + vid,
        "cover": x.get("cover") or "",
    })
json.dump(dy_out, open(os.path.join(RES, "harvest_douyin.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

print(f"harvest_xhs.json: {len(xhs_out)}  (video={sum(1 for x in xhs_out if x.get('type')=='video')})")
print(f"harvest_douyin.json: {len(dy_out)}")
