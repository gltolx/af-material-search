#!/usr/bin/env python3
"""一次性回填 duration(整数秒)到 results/verdicts.json 的每条(现有米卢 329 条没有时长字段)。
- 抖音:douyin venv 的 resolve_once → video.duration(ms)/1000(**串行**,避 KR ~80次/轮限流)
- B站/YT/小红书:yt-dlp --skip-download --print "%(duration)s"(并发;B站带 --cookies-from-browser chrome 解 412)
补不到的留原值/None(卡片就不显角标)。已有 duration 的跳过(幂等)。
**用 douyin venv python 跑**(要 import resolve_once + subprocess yt-dlp):
  BROLL_RES=results ~/.local/share/uv/tools/douyin-mcp-server/bin/python backfill_duration.py
"""
import os, re, json, subprocess
from concurrent.futures import ThreadPoolExecutor

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
VF = os.path.join(RES, "scored.json")  # 回填到源头 scored.json:经 score_candidates(已带 duration)→ apply_verdicts 出页才不丢

def _find_ytdlp():  # 版本无关地定位 yt-dlp(换机 system python ≠3.9 也不坏):env→PATH→任意 ~/Library/Python/3.*/bin→~/.local/bin
    import glob, shutil
    p = os.environ.get("YTDLP")
    if p and os.path.exists(p): return p
    p = shutil.which("yt-dlp")
    if p: return p
    c = [x for x in glob.glob(os.path.expanduser("~/Library/Python/3.*/bin/yt-dlp")) + [os.path.expanduser("~/.local/bin/yt-dlp")] if os.path.exists(x)]
    return sorted(c, reverse=True)[0] if c else "yt-dlp"
YTDLP = _find_ytdlp()
items = json.load(open(VF, encoding="utf-8"))


def host_platform(c):
    u = (c.get("page") or "") + " " + (c.get("url") or "")
    if c.get("platform") == "抖音" or "douyin.com" in u: return "douyin"
    if "xiaohongshu.com" in u: return "xiaohongshu"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "bilibili.com" in u or re.search(r"/BV[0-9A-Za-z]{8,}", u): return "bilibili"
    return "other"


def ytdlp_dur(c):
    url = c.get("page") or c.get("url")
    if not url: return None
    cmd = [YTDLP, "--skip-download", "--no-warnings", "--no-playlist", "--socket-timeout", "30", "--print", "%(duration)s"]
    if host_platform(c) == "bilibili":
        cmd += ["--cookies-from-browser", "chrome"]
    cmd += [url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
        for line in out.splitlines():
            line = line.strip()
            if re.match(r"^\d+(\.\d+)?$", line):
                return int(float(line))
    except Exception:
        pass
    return None


try:
    from resolve_douyin import resolve_once
except Exception as e:
    resolve_once = None
    print("⚠️ 没 import 到 resolve_once(没用 douyin venv python 跑?):", str(e)[:80])

net = [c for c in items if host_platform(c) in ("bilibili", "youtube", "xiaohongshu") and not c.get("duration")]
dy = [c for c in items if host_platform(c) == "douyin" and not c.get("duration")]

print(f"待回填:B站/YT/小红书 {len(net)} 条(并发)+ 抖音 {len(dy)} 条(串行)")

# --- 网络平台并发(yt-dlp)---
done = {"n": 0}
def fill_net(c):
    d = ytdlp_dur(c)
    if d: c["duration"] = d
    done["n"] += 1
    if done["n"] % 30 == 0: print(f"  yt-dlp 进度 {done['n']}/{len(net)}", flush=True)
with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(fill_net, net))

# --- 抖音串行(resolve_once,避限流)---
if resolve_once:
    import time
    for i, c in enumerate(dy, 1):
        m = re.search(r"/video/(\d{10,})", (c.get("page") or "") + " " + (c.get("url") or ""))
        if not m: continue
        try:
            c["duration"] = resolve_once(m.group(1)).get("duration")
        except Exception:
            pass
        print(f"  抖音 {i}/{len(dy)}", flush=True)
        time.sleep(1.8)

json.dump(items, open(VF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
filled = sum(1 for c in items if c.get("duration"))
from collections import Counter
byp = Counter(host_platform(c) for c in items if c.get("duration"))
print(f"✅ 回填完成:{filled}/{len(items)} 条有时长 → {dict(byp)}")
print("   再跑:python3 score_candidates.py && python3 apply_verdicts.py(把 duration 带到卡片)")
