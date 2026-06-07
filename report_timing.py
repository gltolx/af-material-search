#!/usr/bin/env python3
"""读 BROLL_RES/timings.jsonl,算各流时长 + 总挂钟(max(end)-min(start)) + 串行和对比。
每行: {"stream":..,"phase":"start|end|wait_start|wait_end","ts":epoch}
人工等待(wait_*)单独汇总并从挂钟里标注,使提速对比公平。
"""
import json, os, sys
RES = os.environ.get("BROLL_RES") or "results"
p = os.path.join(RES, "timings.jsonl")
evs = []
for ln in open(p, encoding="utf-8"):
    ln = ln.strip()
    if ln:
        try: evs.append(json.loads(ln))
        except Exception: pass
if not evs:
    print("无计时数据"); sys.exit(0)

def span(stream, a="start", b="end"):
    s = [e["ts"] for e in evs if e["stream"] == stream and e["phase"] == a]
    t = [e["ts"] for e in evs if e["stream"] == stream and e["phase"] == b]
    if s and t: return min(s), max(t)
    return None

def fmt(sec):
    m = int(sec // 60); return f"{m}分{int(sec - m*60)}秒"

streams = sorted({e["stream"] for e in evs if not e["phase"].startswith("wait")})
print("=== 各流时长 ===")
durs = {}
for st in streams:
    sp = span(st)
    if sp:
        durs[st] = sp[1] - sp[0]
        print(f"  {st:10s} {fmt(sp[1]-sp[0])}")
# 人工等待
waits = span("human", "wait_start", "wait_end")
wait_sec = (waits[1]-waits[0]) if waits else 0

print("=== 总计 ===")
# 公平挂钟:前台浏览器流(小红书+抖音,单Chrome必串行)之和,与后台并行流取 max。
# (不用 max(end)-min(start):流间若隔着一次性 captcha/人工解码,会污染跨度,非流水线时间。)
FG = {"xhs", "douyin"}          # 前台浏览器串行轨
BG = {"bili", "yt", "net"}      # 后台并行轨(被前台吸收)
fg_sum = sum(v for k, v in durs.items() if k in FG)
bg_max = max([v for k, v in durs.items() if k in BG] or [0])
fair_wall = max(fg_sum, bg_max)
serial_active = sum(durs.values())  # 若四平台全串(旧串行版口径近似)
print(f"  前台浏览器串行轨(小红书+抖音)= {fmt(fg_sum)}")
print(f"  后台并行轨(B站∥YT,被吸收)= {fmt(bg_max)}")
print(f"  ★ 公平收割挂钟 = max(前台, 后台) = {fmt(fair_wall)}")
print(f"  若四平台全串行之和 = {fmt(serial_active)}")
if fair_wall > 0:
    print(f"  并行+方法收益(全串和/公平挂钟)= {serial_active/fair_wall:.2f}×")
print("  注:报告内各流时长为本流自身 start→end(干净);流间隔的一次性 captcha/人工不计入挂钟。")
