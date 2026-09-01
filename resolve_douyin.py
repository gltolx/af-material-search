#!/usr/bin/env python3
"""把已收割的抖音 video_id 批量解析(无水印URL+封面+标题)→ 写 BROLL_RES/harvest_douyin.json(数据契约 5 字段)+ 本地封面 covers/{id}.jpg。
复用 build_douyin_page.py 的 iesdouyin share 解析(和搜索墙无关),带节奏+退避重试+增量落盘,避免 iesdouyin 对 KR 反复请求限流。
必须用 douyin venv python 跑:~/.local/share/uv/tools/douyin-mcp-server/bin/python resolve_douyin.py
"""
import re, json, os, time, requests
import douyin_mcp_server.server as S  # 借移动端 HEADERS

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
COV = os.path.join(RES, "covers"); os.makedirs(COV, exist_ok=True)
HDR = dict(S.HEADERS)
SLEEP = float(os.environ.get("RESOLVE_SLEEP", "1.8"))

def resolve_once(vid):
    r = requests.get(f"https://www.iesdouyin.com/share/video/{vid}", headers=HDR, timeout=25)
    r.raise_for_status()
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", r.text, re.DOTALL)
    if not m:
        raise ValueError("no _ROUTER_DATA (限流/改版)")
    ld = json.loads(m.group(1).strip())["loaderData"]
    key = "video_(id)/page" if "video_(id)/page" in ld else "note_(id)/page"
    data = ld[key]["videoInfoRes"]["item_list"][0]
    v = data.get("video", {})
    play = (v.get("play_addr", {}).get("url_list") or [None])[0]
    if play: play = play.replace("playwm", "play")  # 返回基址(默认ratio=720p);抬到1080p放在下载端
    # 抠不出 1080p 不能盲目把 ratio 改 1080p——约30%新闻片无1080p无水印转码,强抬反而被甩到576p实验流。
    # 正确做法:下载端 dl_douyin 试1080p→量真实分辨率→<1080则回退真720p(取较大者)。要求:有更高清不低于1080p。
    cov = (v.get("cover", {}).get("url_list") or v.get("origin_cover", {}).get("url_list") or [None])[0]
    dms = v.get("duration")  # 抖音时长在 video.duration,单位毫秒(顶层 duration 是 None)
    author = ((data.get("author") or {}).get("nickname") or "").strip()
    return {"id": vid, "title": (data.get("desc", "") or "").strip(), "author": author,
            "play": play or "", "cover_remote": cov or "", "duration": (round(dms / 1000) if dms else None)}

def resolve(vid, retries=3):
    for i in range(retries):
        try:
            return resolve_once(vid)
        except Exception as e:
            if i == retries - 1:
                return {"id": vid, "title": "", "author": "", "play": "", "cover_remote": "", "err": str(e)[:60]}
            time.sleep(SLEEP * (i + 2))

def cache_cover(vid, url):
    if not url: return ""
    p = os.path.join(COV, f"{vid}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 500:
        return f"covers/{vid}.jpg"
    try:
        cr = requests.get(url, headers={**HDR, "Referer": "https://www.douyin.com/"}, timeout=25)
        if cr.status_code == 200 and len(cr.content) > 500:
            open(p, "wb").write(cr.content); return f"covers/{vid}.jpg"
    except Exception: pass
    return ""

if __name__ == "__main__":  # 批量解析(被 download_server.py 复用 resolve/resolve_once 时不跑这段)
    groups = json.load(open(os.path.join(RES, "douyin_ids.json"), encoding="utf-8"))
    ids = []
    for q, lst in groups.items():
        for v in lst:
            if v not in ids: ids.append(v)

    out, ok_u, ok_c, fail = [], 0, 0, 0
    outf = os.path.join(RES, "harvest_douyin.json")
    for n, vid in enumerate(ids, 1):
        r = resolve(vid)
        lc = cache_cover(vid, r.get("cover_remote"))
        rec = {"platform": "抖音", "title": r.get("title") or "", "author": r.get("author") or "",
               "url": r.get("play") or "", "page": f"https://www.douyin.com/video/{vid}",
               "cover": lc or (r.get("cover_remote") or ""), "duration": r.get("duration")}
        out.append(rec)
        if rec["url"]: ok_u += 1
        if lc: ok_c += 1
        if r.get("err"): fail += 1
        if n % 10 == 0 or n == len(ids):
            json.dump(out, open(outf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  {n}/{len(ids)} 解析中 (无水印{ok_u} 封面{ok_c} 失败{fail})", flush=True)
        time.sleep(SLEEP)

    json.dump(out, open(outf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"抖音解析完:{len(out)} 条 → 无水印{ok_u} / 本地封面{ok_c} / 失败{fail} → harvest_douyin.json")
