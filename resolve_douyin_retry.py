#!/usr/bin/env python3
"""增量补解析:只对 harvest_douyin.json 里 url 为空(上轮限流失败)的抖音条目重解析,慢节奏(冷却+长退避)避开 iesdouyin 对 KR 的限流。
用 douyin venv python 跑。"""
import re, json, os, time, requests
import douyin_mcp_server.server as S

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
COV = os.path.join(RES, "covers"); os.makedirs(COV, exist_ok=True)
HDR = dict(S.HEADERS)
SLEEP = float(os.environ.get("RESOLVE_SLEEP", "3.5"))
COOL = float(os.environ.get("RESOLVE_COOL", "60"))

def resolve_once(vid):
    r = requests.get(f"https://www.iesdouyin.com/share/video/{vid}", headers=HDR, timeout=25)
    r.raise_for_status()
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", r.text, re.DOTALL)
    if not m: raise ValueError("no _ROUTER_DATA")
    ld = json.loads(m.group(1).strip())["loaderData"]
    key = "video_(id)/page" if "video_(id)/page" in ld else "note_(id)/page"
    data = ld[key]["videoInfoRes"]["item_list"][0]
    v = data.get("video", {})
    play = (v.get("play_addr", {}).get("url_list") or [None])[0]
    if play: play = play.replace("playwm", "play")
    cov = (v.get("cover", {}).get("url_list") or v.get("origin_cover", {}).get("url_list") or [None])[0]
    return (data.get("desc", "") or "").strip(), play or "", cov or ""

def cache_cover(vid, url):
    if not url: return ""
    p = os.path.join(COV, f"{vid}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 500: return f"covers/{vid}.jpg"
    try:
        cr = requests.get(url, headers={**HDR, "Referer": "https://www.douyin.com/"}, timeout=25)
        if cr.status_code == 200 and len(cr.content) > 500:
            open(p, "wb").write(cr.content); return f"covers/{vid}.jpg"
    except Exception: pass
    return ""

f = os.path.join(RES, "harvest_douyin.json")
recs = json.load(open(f, encoding="utf-8"))
todo = [r for r in recs if not r.get("url")]
print(f"待补解析 {len(todo)} 条;冷却 {COOL}s …", flush=True)
time.sleep(COOL)
fixed = 0
for n, r in enumerate(todo, 1):
    vid = r["page"].rsplit("/", 1)[-1]
    title = play = cov = ""
    for i in range(4):
        try:
            title, play, cov = resolve_once(vid); break
        except Exception:
            time.sleep(SLEEP * (i + 1))
    if play:
        r["title"] = title or r.get("title", ""); r["url"] = play
        lc = cache_cover(vid, cov); r["cover"] = lc or r.get("cover", "") or cov
        fixed += 1
    if n % 5 == 0 or n == len(todo):
        json.dump(recs, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  {n}/{len(todo)} 补解析 (已修 {fixed})", flush=True)
    time.sleep(SLEEP)
json.dump(recs, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
nu = sum(1 for x in recs if x.get("url"))
print(f"补解析完:本轮修复 {fixed} | 现共有无水印 {nu}/{len(recs)}", flush=True)
