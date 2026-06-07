#!/usr/bin/env python3
"""小红书撞 captcha 后的耐心收割:慢节奏 + 撞墙退避重试。写 BROLL_RES/harvest_xhs.json(不碰 scored.json,末尾统一并)。
节奏:初始冷却 → 每查询间隔 GAP → 命中 captcha 则 BACKOFF 退避重试 RETRY 次。
"""
import json, os, subprocess, time

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
XHS = os.path.expanduser("~/.local/share/uv/tools/xiaohongshu-cli/bin/xhs")
INIT_COOL, GAP, BACKOFF, RETRY = 70, 7, 22, 3

XHS_Q = [
    "老式电视机 怀旧", "90年代 看电视 回忆杀", "复古 电风扇 年代感", "老吊扇 怀旧",
    "夏天 凉席 西瓜 童年", "小卖部 怀旧 童年", "那些年 看球 回忆", "老电视 雪花屏 回忆",
    "80后 90后 童年 夏天", "老式落地扇 复古", "怀旧 夏天 风扇 空镜", "2002世界杯 回忆杀",
    "童年 夏天 闷热 老房子", "复古 客厅 老电视 年代感", "一起看球 那年夏天", "怀旧 老物件 年代感",
    "老式蒲扇 夏天 童年", "九十年代 夏天 回忆",
]

def one(kw):
    """返回 video items 或 None(captcha/失败)。"""
    try:
        r = subprocess.run([XHS, "search", kw, "--type", "video", "--json"],
                           capture_output=True, text=True, timeout=70)
        d = json.loads(r.stdout or "{}")
    except Exception as ex:
        print(f"  ⚠️ ex {kw}: {ex}", flush=True); return None
    if not d.get("ok"):
        return None  # captcha / 限流
    items = (d.get("data") or {}).get("items") or []
    return [it for it in items if (it.get("note_card") or {}).get("type") == "video"]

out, ok_q, fail_q = [], 0, 0
print(f"初始冷却 {INIT_COOL}s …", flush=True)
time.sleep(INIT_COOL)
for kw in XHS_Q:
    vids = None
    for attempt in range(RETRY + 1):
        vids = one(kw)
        if vids is not None:
            break
        print(f"  captcha [{kw}] 退避 {BACKOFF}s (try {attempt+1}/{RETRY})", flush=True)
        time.sleep(BACKOFF)
    if vids is None:
        fail_q += 1
        print(f"✗ 放弃 [{kw}]", flush=True)
        time.sleep(GAP)
        continue
    n0 = len(out)
    for it in vids:
        nc = it.get("note_card") or {}
        nid = it.get("id"); tok = it.get("xsec_token") or ""
        if not nid:
            continue
        page = f"https://www.xiaohongshu.com/explore/{nid}"
        if tok:
            page += f"?xsec_token={tok}&xsec_source=pc_search"
        out.append({"platform": "小红书", "title": (nc.get("display_title") or "").strip(),
                    "url": page, "page": page,
                    "cover": (nc.get("cover") or {}).get("url_default") or ""})
    ok_q += 1
    print(f"✓ [{kw}] +{len(out)-n0} (累计 {len(out)})", flush=True)
    # 增量落盘(防中断丢失)
    json.dump(out, open(os.path.join(RES, "harvest_xhs.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    time.sleep(GAP)

json.dump(out, open(os.path.join(RES, "harvest_xhs.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"\n小红书耐心收割完:成功 {ok_q} 查询 / 放弃 {fail_q} / 共 {len(out)} 条 → harvest_xhs.json", flush=True)
