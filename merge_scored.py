#!/usr/bin/env python3
"""把三股并行收割产物合并成最终 scored.json(数据契约5字段),按 canonical id 去重。
- harvest_net.json   : B站 ∥ YouTube(后台线程流)
- harvest_xhs.json   : 小红书(浏览器 DOM 流)
- harvest_douyin.json: 抖音(浏览器拦截器流;url 留空,解析延后到选片)
丢掉抖音里既无标题又无封面的死条目。
"""
import json, os, re
from collections import Counter
RES = os.environ.get("BROLL_RES") or "results"

def load(p):
    p = os.path.join(RES, p)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []

net = load("harvest_net.json")
xhs = load("harvest_xhs.json")
dy_raw = load("harvest_douyin.json")
dy = [x for x in dy_raw if (x.get("title") or "").strip() or (x.get("cover") or "")]
dropped = len(dy_raw) - len(dy)

def canon(c):
    u = (c.get("page") or "") + " " + (c.get("url") or ""); p = c["platform"]
    if p == "抖音":
        m = re.search(r"/video/(\d{10,})", u)
        if m: return "dy:" + m.group(1)
    if p == "小红书":
        m = re.search(r"/explore/([0-9a-fA-F]{12,})", u)
        if m: return "xhs:" + m.group(1)
    m = re.search(r"/video/(BV[0-9A-Za-z]{8,})", u)
    if m: return "bili:" + m.group(1)
    m = re.search(r"[?&]v=([\w-]{6,})", u) or re.search(r"youtu\.be/([\w-]{6,})", u)
    if m: return "yt:" + m.group(1)
    return "raw:" + p + ":" + (c.get("page") or c.get("url") or c.get("title") or "")[:40]

merged, seen = [], set()
for c in net + xhs + dy:
    k = canon(c)
    if k in seen: continue
    seen.add(k)
    merged.append({"platform": c["platform"], "title": c.get("title", ""),
                   "url": c.get("url", ""), "page": c.get("page", ""), "cover": c.get("cover", ""),
                   "duration": c.get("duration")})

json.dump(merged, open(os.path.join(RES, "scored.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# 顺手自动产 xhs_imgs.json(note_id→{t,imgs}):下载图文笔记需它,否则 yt-dlp 把图文下成幻灯片 mp4。
# 以前只靠 backfill_xhs_token.py 单独跑,易漏 → 现随收割合并自动产(master = xhs_raw.json)。
raw_xhs = load("xhs_raw.json")
if raw_xhs:
    import xhs_imgmap
    imgmap = xhs_imgmap.build_imgmap(raw_xhs)
    json.dump(imgmap, open(os.path.join(RES, "xhs_imgs.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"xhs_imgs.json 自动产:{len(imgmap)} 个 note(图文走图片下载,缺则被下成幻灯片mp4)")
else:
    print("⚠️ 无 xhs_raw.json → 未产 xhs_imgs.json:小红书图文笔记下载会被 yt-dlp 下成幻灯片 mp4。需带 imageList 重收割小红书。")

print(f"抖音死条目丢弃 {dropped}")
print("最终 scored.json:", len(merged), dict(Counter(x["platform"] for x in merged)))
