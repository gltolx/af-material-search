#!/usr/bin/env python3
"""四平台收割之「干净三平台」(小红书/B站/YouTube)→ 合并写 BROLL_RES/scored.json。
抖音走浏览器精搜单独补(本脚本不碰)。产物字段严守数据契约:{platform,title,url,page,cover}。
- 小红书:xhs CLI(已登录),video 笔记;display_title 常空→留空交 score_candidates 转 need_enrich。
- B站:开放 all/v2 搜索(KR 可用、无 cookie),取 video 组。
- YouTube:yt-dlp ytsearch flat-playlist。
幂等合并:读已有 scored.json,按 canonical id 去重后并入(抖音条目会被保留)。
用法:BROLL_RES=/abs/dir python3 harvest_clean.py
"""
import json, os, re, subprocess, time, urllib.request, urllib.parse, html as _html

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(RES, exist_ok=True)
XHS = os.path.expanduser("~/.local/share/uv/tools/xiaohongshu-cli/bin/xhs")

def _find_ytdlp():  # 版本无关地定位 yt-dlp(换机 system python ≠3.9 也不坏):env→PATH→任意 ~/Library/Python/3.*/bin→~/.local/bin
    import glob, shutil
    p = os.environ.get("YTDLP")
    if p and os.path.exists(p): return p
    p = shutil.which("yt-dlp")
    if p: return p
    c = [x for x in glob.glob(os.path.expanduser("~/Library/Python/3.*/bin/yt-dlp")) + [os.path.expanduser("~/.local/bin/yt-dlp")] if os.path.exists(x)]
    return sorted(c, reverse=True)[0] if c else "yt-dlp"
YTDLP = _find_ytdlp()

# ---- 查询集(§14:必带年代/意图锚点 + 平台行话;描述≠召回) ----
XHS_Q = [
    "老式电视机 怀旧", "90年代 看电视 回忆杀", "复古 电风扇 年代感", "老吊扇 怀旧",
    "夏天 凉席 西瓜 童年", "小卖部 怀旧 童年", "那些年 看球 回忆", "老电视 雪花屏 回忆",
    "80后 90后 童年 夏天", "老式落地扇 复古", "怀旧 夏天 风扇 空镜", "2002世界杯 回忆杀",
    "童年 夏天 闷热 老房子", "复古 客厅 老电视 年代感", "一起看球 那年夏天", "怀旧 老物件 年代感",
    "老式蒲扇 夏天 童年", "九十年代 夏天 回忆",
]
BILI_Q = [
    "2002世界杯 中国队 集锦", "2002世界杯 国足 进球", "米卢 中国队 2002", "米卢 快乐足球",
    "2002韩日世界杯 中国队", "国足 五里河 出线", "范志毅 孙继海 国足 2002", "中国队 巴西 2002世界杯",
    "2002世界杯 中国队 纪录片", "奥克斯 米卢 广告", "国足 2002 世界杯 回顾", "李铁 杨晨 郝海东 2002",
    "中国队 哥斯达黎加 土耳其 2002", "空调 生产线 制造 实拍",
]
YT_Q = [
    ("2002 World Cup China national team", 15), ("米卢 中国队 2002世界杯", 12),
    ("China 2002 World Cup highlights", 12), ("2002世界杯 中国队 集锦", 12),
    ("Bora Milutinovic China football", 10),
]

def canon_id(c):
    u = (c.get("page") or "") + " " + (c.get("url") or ""); p = c["platform"]
    if p == "小红书":
        m = re.search(r"/explore/([0-9a-fA-F]{12,})", u)
        if m: return "xhs:" + m.group(1)
    m = re.search(r"/video/(BV[0-9A-Za-z]{8,})", u)
    if m: return "bili:" + m.group(1)
    m = re.search(r"[?&]v=([\w-]{6,})", u) or re.search(r"youtu\.be/([\w-]{6,})", u)
    if m: return "yt:" + m.group(1)
    return "raw:" + p + ":" + (c.get("page") or c.get("url") or c.get("title") or "")[:40]

out = []  # new harvest

# ===== 小红书 =====
xhs_n = 0
for kw in XHS_Q:
    try:
        r = subprocess.run([XHS, "search", kw, "--type", "video", "--json"],
                           capture_output=True, text=True, timeout=70)
        d = json.loads(r.stdout)
        items = (d.get("data") or {}).get("items") or []
    except Exception as ex:
        print("  ⚠️ xhs", kw, ex); time.sleep(1.0); continue
    for it in items:
        if it.get("model_type") != "note":
            continue
        nc = it.get("note_card") or {}
        if nc.get("type") != "video":
            continue
        nid = it.get("id"); tok = it.get("xsec_token") or ""
        if not nid:
            continue
        page = f"https://www.xiaohongshu.com/explore/{nid}"
        if tok:
            page += f"?xsec_token={tok}&xsec_source=pc_search"
        cover = (nc.get("cover") or {}).get("url_default") or ""
        title = (nc.get("display_title") or "").strip()
        out.append({"platform": "小红书", "title": title, "url": page, "page": page, "cover": cover})
        xhs_n += 1
    time.sleep(0.8)
print(f"小红书 收 {xhs_n}")

# ===== B站 =====
def strip_em(s): return re.sub(r"</?em[^>]*>", "", _html.unescape(s or ""))
def parse_dur(s):  # B站搜索API时长=不补零 M:SS(分钟可超60)→ 整数秒
    if not s: return None
    parts = str(s).strip().split(":")
    if not parts or not all(p.strip().isdigit() for p in parts): return None
    sec = 0
    for p in parts: sec = sec * 60 + int(p)
    return sec
bili_n = 0
for kw in BILI_Q:
    for page in (1, 2):
        try:
            q = urllib.parse.urlencode({"keyword": kw, "page": page})
            req = urllib.request.Request(
                "https://api.bilibili.com/x/web-interface/search/all/v2?" + q,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception as ex:
            print("  ⚠️ bili", kw, page, ex); time.sleep(1.0); continue
        groups = (d.get("data") or {}).get("result") or []
        vids = []
        for g in groups:
            if isinstance(g, dict) and g.get("result_type") == "video":
                vids = g.get("data") or []
        for v in vids:
            bv = v.get("bvid")
            if not bv:
                continue
            pic = v.get("pic") or ""
            if pic.startswith("//"): pic = "https:" + pic
            url = f"https://www.bilibili.com/video/{bv}"
            out.append({"platform": "B站/YT", "title": strip_em(v.get("title")),
                        "url": url, "page": url, "cover": pic, "duration": parse_dur(v.get("duration"))})
            bili_n += 1
        time.sleep(0.5)
print(f"B站 收 {bili_n}")

# ===== YouTube =====
yt_n = 0
for kw, n in YT_Q:
    try:
        r = subprocess.run([YTDLP, f"ytsearch{n}:{kw}", "--flat-playlist",
                            "--print", "%(id)s|%(title)s|%(channel)s|%(duration)s", "--no-warnings"],
                           capture_output=True, text=True, timeout=120)
    except Exception as ex:
        print("  ⚠️ yt", kw, ex); continue
    for line in r.stdout.splitlines():
        parts = line.split("|", 3)
        if len(parts) < 2 or not parts[0].strip():
            continue
        vid = parts[0].strip(); title = parts[1].strip()
        url = f"https://www.youtube.com/watch?v={vid}"
        cover = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        dur = int(float(parts[3].strip())) if len(parts) >= 4 and re.match(r"^\d+(\.\d+)?$", parts[3].strip()) else None
        out.append({"platform": "B站/YT", "title": title, "url": url, "page": url, "cover": cover, "duration": dur})
        yt_n += 1
print(f"YouTube 收 {yt_n}")

# ===== 合并已有(保留抖音等)+ 去重 =====
existing = []
sp = os.path.join(RES, "scored.json")
if os.path.exists(sp):
    try: existing = json.load(open(sp, encoding="utf-8"))
    except Exception: existing = []

merged, seen = [], set()
for c in existing + out:
    k = canon_id(c)
    if k in seen:
        continue
    seen.add(k)
    merged.append({"platform": c["platform"], "title": c.get("title", ""),
                   "url": c.get("url", ""), "page": c.get("page", ""), "cover": c.get("cover", ""),
                   "duration": c.get("duration")})

json.dump(merged, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
from collections import Counter
print("—— 合并后 scored.json ——")
print("total", len(merged), dict(Counter(c["platform"] for c in merged)))
