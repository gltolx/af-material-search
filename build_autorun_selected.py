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

# 口播稿链接 → 知识库 路由(可选;AI 接稿时按"链接+账号+库名"产 results/kb_routing.json)。
# 结构:{links:{<链接URL>:{account,kb_name,script_ids:[...]}}} → 反查 script_id → {source_link,account,kb_name}。
# 缺该文件 = 不分库:所有项 source_link/account/kb_name 留空,autorun_kb 走兜底 --account/--kb(向后兼容)。
sid2route = {}
try:
    ROUTE = json.load(open(os.path.join(RES, "kb_routing.json"), encoding="utf-8"))
    for link, meta in (ROUTE.get("links") or {}).items():
        for sid in (meta.get("script_ids") or []):
            sid2route[str(sid)] = {
                "source_link": link,
                "account": meta.get("account", "") or "",
                "kb_name": meta.get("kb_name", "") or "",
            }
except Exception:
    pass

out, seen, n_routed = [], set(), 0
for _sid, info in (SM.items() if isinstance(SM, dict) else []):
    rt = sid2route.get(str(_sid), {})           # _sid 即 script_id(SM 顶层键)
    for m in (info.get("matched") or []):
        idx = m.get("idx")
        c = by_idx.get(idx)
        if not c or idx in seen:
            continue
        seen.add(idx)
        if rt.get("source_link"):
            n_routed += 1
        out.append({
            "platform": c.get("platform", ""),
            "page": c.get("page", "") or "",
            "url": c.get("url", "") or "",
            "title": c.get("title", "") or "",
            "verdict": c.get("verdict", ""),
            "score": c.get("vscore", ""),
            "script_name": info.get("name", "") or "",
            "persona": info.get("persona", "") or "",
            "pool": "theme",
            "source_link": rt.get("source_link", ""),   # 分库维度:该稿所属口播稿链接(空=走兜底库)
            "account": rt.get("account", ""),           # 该链接的入库账号(空=用 autorun_kb --account)
            "kb_name": rt.get("kb_name", ""),           # 该链接对应知识库名(空=用 autorun_kb --kb)
            "audit": "pass",
        })

# ---- 中性垫片池(filler):读 verdicts_filler.json + filler_matches.json,搭【该稿自己的库】路由 + pool=filler ----
# filler 跟该稿主题素材【完全相同的 source_link/account/kb_name】→ autorun_kb 按 source_link 自动并进同一组同一库(autorun_kb 零改动)。
try:
    VF = json.load(open(os.path.join(RES, "verdicts_filler.json"), encoding="utf-8"))
except Exception:
    VF = []
by_idx_f = {c["idx"]: c for c in VF}
try:
    FM = json.load(open(os.path.join(RES, "filler_matches.json"), encoding="utf-8"))
except Exception:
    FM = {}
n_filler = 0
for _sid, info in (FM.items() if isinstance(FM, dict) else []):
    rt = sid2route.get(str(_sid), {})
    for m in (info.get("matched") or []):
        idx = m.get("idx")
        c = by_idx_f.get(idx)
        if not c or idx in seen:                    # seen 复用主题集:filler/theme idx 全局互斥,防任何双发
            continue
        seen.add(idx); n_filler += 1
        if rt.get("source_link"):
            n_routed += 1
        out.append({
            "platform": c.get("platform", ""),
            "page": c.get("page", "") or "",
            "url": c.get("url", "") or "",
            "title": c.get("title", "") or "",
            "verdict": c.get("verdict", ""),
            "score": c.get("vscore", ""),
            "script_name": info.get("name", "") or "",   # 稿名前缀照走 → 文件名带前缀 + 进该稿库
            "persona": info.get("persona", "") or "",
            "pool": "filler",
            "from_script": m.get("src_sentence", "") or "",   # 可追溯:这条中性垫片锚的稿句
            "source_link": rt.get("source_link", ""),
            "account": rt.get("account", ""),
            "kb_name": rt.get("kb_name", ""),
            "audit": "pass",
        })

json.dump(out, open(os.path.join(RES, "autorun_selected.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
_route_note = ("·已按链接路由 %d/%d 条→%d 个库" % (n_routed, len(out), len({r["source_link"] for r in out if r.get("source_link")}))) if sid2route else "·未分库(无 kb_routing.json)"
_filler_note = (f"·含中性垫片 {n_filler} 条(pool=filler,搭各稿自己的库)") if n_filler else ""
print("autorun_selected.json:", len(out), "条(来自", len(SM), "稿匹配)", _route_note, _filler_note, "→ 接着 AI 审查剔除黄赌毒/政治项,再跑 autorun_kb.py")
