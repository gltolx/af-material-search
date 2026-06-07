#!/usr/bin/env python3
"""Stage-0 预过滤(只杀不打分)。把 v1 的关键词累加打分废掉,改成:
- 命中硬负面词 → kill(不进语义判分)
- 小红书空标题/"(无标题)" → need_enrich(后续 xhs read 取正文,或封面兜底)
- 其余 → need_llm(交 stage1 Claude 语义判分)
**关键:删掉 v1 的"没命中关键词=0分""空标题=0分"——那是全部假阴的总根。**
读 results/scored.json(候选源)+ relevance_spec.json;出 results/candidates.json(带 idx/stage0)+ results/prefilter.json + 控制台计数。
"""
import json, os

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
SPEC = json.load(open(os.path.join(RES, "relevance_spec.json"), encoding="utf-8"))
NEG = SPEC["negative_terms"]
cands = json.load(open(os.path.join(RES, "scored.json"), encoding="utf-8"))

kill, need_enrich, need_llm = [], [], []
out = []
for i, c in enumerate(cands):
    plat = c.get("platform", "")
    t = (c.get("title") or "").strip()
    rec = {"idx": i, "platform": plat, "title": t, "url": c.get("url", ""),
           "page": c.get("page", ""), "cover": c.get("cover", ""), "duration": c.get("duration")}
    neg = next((n for n in NEG if n in t), None)
    if neg:
        rec["stage0"] = "kill"; rec["kill_reason"] = neg; kill.append(rec)
    elif plat == "小红书" and (not t or t == "(无标题)" or len(t) < 2):
        rec["stage0"] = "need_enrich"; need_enrich.append(rec)
    else:
        rec["stage0"] = "need_llm"; need_llm.append(rec)
    out.append(rec)

json.dump(out, open(os.path.join(RES, "candidates.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump({
    "kill": kill,
    "need_enrich": need_enrich,
    "need_llm": [{"idx": r["idx"], "platform": r["platform"], "text": r["title"]} for r in need_llm],
}, open(os.path.join(RES, "prefilter.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

from collections import Counter
print(f"总候选 {len(out)}")
print(f"  kill(负面秒杀) {len(kill)}  |  need_enrich(小红书空标题) {len(need_enrich)}  |  need_llm(待语义判分) {len(need_llm)}")
print("  kill 平台分布:", dict(Counter(r["platform"] for r in kill)))
print("  need_llm 平台分布:", dict(Counter(r["platform"] for r in need_llm)))
print("\nkill 样例(看负面词对不对):")
for r in kill[:12]:
    print(f"  [杀:{r['kill_reason']}] {r['platform']} | {r['title'][:36]}")
