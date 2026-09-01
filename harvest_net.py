#!/usr/bin/env python3
"""Track-1 后台流:B站 ∥ YouTube 并发收割(ThreadPoolExecutor,都是网络 I/O,GIL 不挡)。
→ 写 BROLL_RES/harvest_net.json(数据契约5字段),各线程往 timings.jsonl 记起止。
跑:BROLL_RES=<dir> python3 harvest_net.py &  (run_in_background)
"""
import json, os, random, re, subprocess, time, urllib.request, urllib.parse, html as _html
from concurrent.futures import ThreadPoolExecutor

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(RES, exist_ok=True)

def _find_ytdlp():  # 版本无关地定位 yt-dlp 二进制(换机 system python ≠3.9 也不坏):env→PATH→任意 ~/Library/Python/3.*/bin→~/.local/bin
    import glob, shutil
    p = os.environ.get("YTDLP")
    if p and os.path.exists(p): return p
    p = shutil.which("yt-dlp")
    if p: return p
    c = [x for x in glob.glob(os.path.expanduser("~/Library/Python/3.*/bin/yt-dlp")) + [os.path.expanduser("~/.local/bin/yt-dlp")] if os.path.exists(x)]
    return sorted(c, reverse=True)[0] if c else "yt-dlp"
YTDLP = _find_ytdlp()
TIMINGS = os.path.join(RES, "timings.jsonl")

def log(stream, phase):
    try:
        with open(TIMINGS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"stream": stream, "phase": phase, "ts": time.time()}) + "\n")
    except Exception: pass

# 查询词:优先从 relevance_spec.json 读(每选题的单一事实源);缺省回退到米卢示例。
# spec 里加:"bili_queries":["词",...]  "yt_queries":[["kw",N],...](N=ytsearch 条数)
_SPEC = {}
try:
    _SPEC = json.load(open(os.path.join(RES, "relevance_spec.json"), encoding="utf-8"))
except Exception:
    pass
# 收割前核心主体自愈(必经路径绕不过;幂等,AI 若已显式跑过则 no-op)。只增 autoheal_added,绝不改原 queries。
_BILI_BASE = _SPEC.get("bili_queries") or []
try:
    import autoheal_queries
    _ah = autoheal_queries.augment(RES)
    if _ah.get("added"):
        print("[harvest_net] autoheal 补查询词:", _ah["added"])
    _SPEC = json.load(open(os.path.join(RES, "relevance_spec.json"), encoding="utf-8"))  # 重载拿 autoheal_added
    _BILI_BASE = autoheal_queries.merged_queries(_SPEC, "bili")                            # base ∪ 自愈补的
except Exception as _e:
    print("[harvest_net] autoheal 跳过(不阻塞):", str(_e)[:120])
BILI_Q = _BILI_BASE or [
    "2002世界杯 中国队 集锦", "2002世界杯 国足 进球", "米卢 中国队 2002", "米卢 快乐足球",
    "2002韩日世界杯 中国队", "国足 五里河 出线", "范志毅 孙继海 国足 2002", "中国队 巴西 2002世界杯",
    "2002世界杯 中国队 纪录片", "奥克斯 米卢 广告", "国足 2002 世界杯 回顾", "李铁 杨晨 郝海东 2002",
    "中国队 哥斯达黎加 土耳其 2002", "空调 生产线 制造 实拍",
]
YT_Q = [tuple(x) for x in _SPEC.get("yt_queries", [])] or [
    ("2002 World Cup China national team", 15), ("米卢 中国队 2002世界杯", 12),
    ("China 2002 World Cup highlights", 12), ("2002世界杯 中国队 集锦", 12),
    ("Bora Milutinovic China football", 10),
]

def strip_em(s): return re.sub(r"</?em[^>]*>", "", _html.unescape(s or ""))
def parse_dur(s):  # B站搜索API的时长是不补零 M:SS(分钟可超60,如 '119:46');统一成整数秒
    if not s: return None
    parts = str(s).strip().split(":")
    if not parts or not all(p.strip().isdigit() for p in parts): return None
    sec = 0
    for p in parts: sec = sec * 60 + int(p)
    return sec

def harvest_bili():
    log("bili", "start"); out = []
    if os.environ.get("BROLL_NO_BILI") == "1":  # 本批不爬B站时,空返回(不回退默认词)
        log("bili", "skip(BROLL_NO_BILI=1)"); return out
    for kw in BILI_Q:
        for page in (1, 2):
            try:
                q = urllib.parse.urlencode({"keyword": kw, "page": page})
                req = urllib.request.Request(
                    "https://api.bilibili.com/x/web-interface/search/all/v2?" + q,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
                d = json.loads(urllib.request.urlopen(req, timeout=20).read())
            except Exception as ex:
                print("  ⚠️ bili", kw, page, ex); time.sleep(random.uniform(1.0, 3.0)); continue
            for g in (d.get("data") or {}).get("result") or []:
                if isinstance(g, dict) and g.get("result_type") == "video":
                    for v in g.get("data") or []:
                        bv = v.get("bvid");
                        if not bv: continue
                        pic = v.get("pic") or ""
                        if pic.startswith("//"): pic = "https:" + pic
                        url = f"https://www.bilibili.com/video/{bv}"
                        out.append({"platform": "B站/YT", "title": strip_em(v.get("title")),
                                    "url": url, "page": url, "cover": pic,
                                    "duration": parse_dur(v.get("duration"))})
            time.sleep(random.uniform(0.5, 2.5))
    log("bili", "end"); print(f"B站 收 {len(out)}"); return out

def harvest_yt():
    log("yt", "start"); out = []
    if os.environ.get("BROLL_NO_YT") == "1":  # 本批不爬YouTube时,空返回(不回退默认词)
        log("yt", "skip(BROLL_NO_YT=1)"); print("YouTube skip(BROLL_NO_YT=1)"); return out
    for kw, n in YT_Q:
        try:
            r = subprocess.run([YTDLP, f"ytsearch{n}:{kw}", "--flat-playlist",
                                "--print", "%(id)s|%(title)s|%(channel)s|%(duration)s", "--no-warnings"],
                               capture_output=True, text=True, timeout=120)
        except Exception as ex:
            print("  ⚠️ yt", kw, ex); continue
        for line in r.stdout.splitlines():
            p = line.split("|", 3)
            if len(p) < 2 or not p[0].strip(): continue
            vid = p[0].strip()
            url = f"https://www.youtube.com/watch?v={vid}"
            dur = None
            if len(p) >= 4 and re.match(r"^\d+(\.\d+)?$", p[3].strip()): dur = int(float(p[3].strip()))
            out.append({"platform": "B站/YT", "title": p[1].strip(),
                        "url": url, "page": url, "cover": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "duration": dur})
        time.sleep(random.uniform(0.5, 2.5))
    log("yt", "end"); print(f"YouTube 收 {len(out)}"); return out

log("net", "start")
with ThreadPoolExecutor(max_workers=2) as ex:
    fb = ex.submit(harvest_bili); fy = ex.submit(harvest_yt)
    bili, yt = fb.result(), fy.result()
merged = bili + yt
json.dump(merged, open(os.path.join(RES, "harvest_net.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
log("net", "end")
print(f"harvest_net.json 共 {len(merged)} (B站 {len(bili)} + YT {len(yt)})")
