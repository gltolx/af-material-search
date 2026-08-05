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
MAXDUR = int(SPEC.get("max_duration_sec", 1200))   # R2:已知时长>此值直接 kill,省 AI 打分(双保险,apply_verdicts 还会兜)
cands = json.load(open(os.path.join(RES, "scored.json"), encoding="utf-8"))

# ---- 中性垫片池(filler)平行轨:用 filler 自己的负面词表 + 时长上限,候选进独立桶 need_llm_filler ----
# 主题候选(pool!=filler)的分桶逻辑一行不动;filler 候选据 src_pool 走独立分支(由 merge_scored 标记)。
FILLER = SPEC.get("filler") or {}
FNEG = FILLER.get("negative_terms") or []
FMAX = int(FILLER.get("max_dur_sec", 25))

kill, need_enrich, need_llm, need_llm_filler = [], [], [], []
out = []
for i, c in enumerate(cands):
    plat = c.get("platform", "")
    t = (c.get("title") or "").strip()
    pool = "filler" if c.get("src_pool") == "filler" else "theme"
    rec = {"idx": i, "platform": plat, "title": t, "url": c.get("url", ""),
           "page": c.get("page", ""), "cover": c.get("cover", ""), "duration": c.get("duration"),
           "pool": pool}
    dur = c.get("duration")
    if pool == "filler":
        # filler:吃 filler 专属负面词 + 25s 上限;空标题不 need_enrich(靠封面判),统一进 need_llm_filler。
        fneg = next((n for n in FNEG if n in t), None)
        if fneg:
            rec["stage0"] = "kill"; rec["kill_reason"] = fneg; kill.append(rec)
        elif dur and dur > FMAX:
            rec["stage0"] = "kill"; rec["kill_reason"] = f"超{FMAX}秒({dur}s)"; kill.append(rec)
        else:
            rec["stage0"] = "need_llm"; need_llm_filler.append(rec)
        out.append(rec)
        continue
    neg = next((n for n in NEG if n in t), None)
    if neg:
        rec["stage0"] = "kill"; rec["kill_reason"] = neg; kill.append(rec)
    elif dur and dur > MAXDUR:
        rec["stage0"] = "kill"; rec["kill_reason"] = f"超20分钟({dur}s)"; kill.append(rec)
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
    # 中性垫片池待判分桶:AI 按【标题+封面】判"是否该稿那句话所指的那类干净中性空镜",写 scores_filler_part*.json(独立于主题 scores_part*.json)。
    "need_llm_filler": [{"idx": r["idx"], "platform": r["platform"], "text": r["title"]} for r in need_llm_filler],
}, open(os.path.join(RES, "prefilter.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

from collections import Counter
print(f"总候选 {len(out)}")
print(f"  kill(负面秒杀) {len(kill)}  |  need_enrich(小红书空标题) {len(need_enrich)}  |  need_llm(待语义判分) {len(need_llm)}  |  need_llm_filler(中性垫片待判) {len(need_llm_filler)}")
print("  kill 平台分布:", dict(Counter(r["platform"] for r in kill)))
print("  need_llm 平台分布:", dict(Counter(r["platform"] for r in need_llm)))
print("\nkill 样例(看负面词对不对):")
for r in kill[:12]:
    print(f"  [杀:{r['kill_reason']}] {r['platform']} | {r['title'][:36]}")

# ---- C. cite-or-don't-apply:把"生效的排除/降权约束 + 出处"显式打出来(每批必跑,杜绝临时自创无据规则)----
print("\n本批生效的排除/降权约束(均须有出处,引不出处=不准用):")
print(f"  negative_terms({len(NEG)}词)·max_duration_sec={MAXDUR}s·person_penalty={SPEC.get('person_penalty')}·drop_eye_contact={SPEC.get('drop_eye_contact')}  来源=relevance_spec.json")
# ---- A 兜底断言:核心主体在 queries 的覆盖(score_candidates 每批必跑,绕不过)----
try:
    import autoheal_queries
    try:
        _scripts = json.load(open(os.path.join(RES, "scripts.json"), encoding="utf-8"))
    except Exception:
        _scripts = []
    if _scripts:
        _miss = autoheal_queries.uncovered_core(SPEC, _scripts, include_healed=True)  # 只看品牌/产品本体;含自愈后仍漏=红灯
        if _miss:
            print("  🔴 品牌/产品核心主体仍未被 queries 覆盖(autoheal 没跑/没兜住,务必补搜):",
                  ", ".join(f"{w}({int(h*100)}%)" for w, h, _e in _miss))
        elif SPEC.get("autoheal_added"):
            print("  ✅ 核心主体已覆盖(含 autoheal 自愈补词):", SPEC.get("autoheal_added"))
        else:
            print("  ✅ 核心主体均已被 queries 覆盖")
except Exception:
    pass
