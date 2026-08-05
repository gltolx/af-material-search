#!/usr/bin/env python3
"""一次性:把用户手给的 v.douyin.com 短链跟随重定向 → 抽 19 位 aweme_id,再用 resolve_douyin 拿标题/时长,
拼出 autorun_selected.json(platform=douyin,page=/video/<id>,audit=pass,单库)。
必须用 douyin venv python 跑(有 requests + douyin_mcp_server)。
短链清单从 stdin 读(每行一个 v.douyin.com URL)。
"""
import re, sys, json, os, time, requests
import douyin_mcp_server.server as S
from resolve_douyin import resolve as dy_resolve

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(RES, exist_ok=True)
HDR = dict(S.HEADERS)

KB_NAME = os.environ.get("KB_NAME", "")

def short_to_awemeid(short):
    """跟随 v.douyin.com 重定向,从最终 URL / Location 链抽 19 位 aweme_id。"""
    try:
        r = requests.get(short, headers=HDR, timeout=25, allow_redirects=True)
    except Exception as e:
        return None, "请求失败:" + str(e)[:80]
    # 收集整条重定向链 + 最终 URL 文本
    chain = " ".join([h.url for h in r.history] + [r.url])
    m = re.search(r"/video/(\d{15,})", chain) or re.search(r"/share/video/(\d{15,})", chain) \
        or re.search(r"(\d{19})", chain)
    if m:
        return m.group(1), ""
    # 兜底:最终页面正文里找 aweme_id
    m2 = re.search(r'"aweme_id"\s*:\s*"?(\d{15,})', r.text) or re.search(r"/video/(\d{15,})", r.text)
    if m2:
        return m2.group(1), ""
    return None, "重定向链/正文里抽不到 aweme_id(链=%s)" % chain[:120]

def main():
    shorts = [ln.strip() for ln in sys.stdin if ln.strip()]
    out, fails = [], []
    seen = set()
    for n, s in enumerate(shorts, 1):
        vid, err = short_to_awemeid(s)
        if not vid:
            fails.append((s, err)); print("  [%d/%d] FAIL %s :: %s" % (n, len(shorts), s, err), flush=True);
            time.sleep(2.5); continue
        if vid in seen:
            print("  [%d/%d] dup aweme_id=%s 跳过" % (n, len(shorts), vid), flush=True); continue
        seen.add(vid)
        meta = dy_resolve(vid)
        title = (meta.get("title") or "").strip()
        dur = meta.get("duration")
        out.append({
            "platform": "douyin",
            "page": "https://www.douyin.com/video/%s" % vid,
            "url": "",                       # 留空,dl_douyin 下载时现解(缓存 play 会过期)
            "title": title,
            "verdict": "keep",
            "score": "",
            "script_name": "",
            "persona": "",
            "source_link": "",
            "account": "",
            "kb_name": "",
            "audit": "pass",
            "_short": s,
            "_aweme_id": vid,
            "_duration": dur,
        })
        print("  [%d/%d] OK aweme_id=%s dur=%ss  %s" % (n, len(shorts), vid, dur, title[:40]), flush=True)
        time.sleep(3.0)   # 节奏抖动地板,抗 KR 限流

    sel_path = os.path.join(RES, "autorun_selected.json")
    json.dump(out, open(sel_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n解析完:成功 %d / 失败 %d → %s" % (len(out), len(fails), sel_path))
    if fails:
        print("失败清单(需重试或手动):")
        for s, e in fails:
            print("  -", s, "::", e)

if __name__ == "__main__":
    main()
