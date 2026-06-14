#!/usr/bin/env python3
"""broll-auto:从 verdicts.json + script_matches.json 拼出"自动选中集" → results/autorun_selected.json。
= 逐稿匹配自动选好的两轮结果(独占·最佳匹配),供 autorun_kb.py 清洗+入库。
字段对齐 filtered.html 的 /clean payload:{platform,page,url,title,verdict,score,script_name,persona,audit}。

**内容审查(黄赌毒/政治)由 AI 在选片时做**:命中/存疑的素材不写进本文件(或事后把该项 audit 改 exclude / 删行),
只留 audit=pass 的进清洗+入库。本脚本只做机械拼装,审查是 AI 的活。
用 system python3 跑(纯标准库)。
"""
import json, os

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
V = json.load(open(os.path.join(RES, "verdicts.json"), encoding="utf-8"))
by_idx = {c["idx"]: c for c in V}
try:
    SM = json.load(open(os.path.join(RES, "script_matches.json"), encoding="utf-8"))
except Exception:
    SM = {}

out, seen = [], set()
for _sid, info in (SM.items() if isinstance(SM, dict) else []):
    for m in (info.get("matched") or []):
        idx = m.get("idx")
        c = by_idx.get(idx)
        if not c or idx in seen:
            continue
        seen.add(idx)
        out.append({
            "platform": c.get("platform", ""),
            "page": c.get("page", "") or "",
            "url": c.get("url", "") or "",
            "title": c.get("title", "") or "",
            "verdict": c.get("verdict", ""),
            "score": c.get("vscore", ""),
            "script_name": info.get("name", "") or "",
            "persona": info.get("persona", "") or "",
            "audit": "pass",
        })

json.dump(out, open(os.path.join(RES, "autorun_selected.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("autorun_selected.json:", len(out), "条(来自", len(SM), "稿匹配)→ 接着 AI 审查剔除黄赌毒/政治项,再跑 autorun_kb.py")
