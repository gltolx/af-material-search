#!/usr/bin/env python3
"""核心主体覆盖断言 + 查询词自愈(2026-06-20,格力60稿批次血泪后加)。

为什么:relevance_spec 的 concepts/hard_anchors 可能写了核心主体(品牌/产品,如"格力空调"),
却没翻进真正决定召回的 queries → 该类素材从源头 0 进池(召回100%靠queries,concepts只用于召回后打分)。
本脚本在【收割前】无条件自查:口播稿正文高频(≥80%篇)或显式声明(core_subjects)的核心主体,
若在四组 queries 0 覆盖 → 自动补 2~4 条该主体的"语境化"查询词,记日志,继续(绝不停机报错)。

铁律落地(吸收3人review):
- 不依赖 AI 自觉:频率扫描【无条件】跑(不是 core_subjects 空才跑;同一个判断失误的 AI 填 core_subjects 也会漏)。
- 候选池 = scripts正文高频 ∪ hard_anchors ∪ concepts.terms(不限定 hard_anchors——"空调"这次就不在 hard_anchors)。
- 永不就地改写原 queries 数组:补的词写进独立字段 spec["autoheal_added"],读时合并(harvest_net/AI/apply_verdicts 各自 base∪added)。
  → "换选题只改一处"契约不破;也绝不把 bili_queries 写空触发 harvest_net 的"空list回退米卢默认词"炸弹。
- 封顶防撞码:抖音≤2、小红书≤1、B站≤2;YT(BROLL_NO_YT)跳过。语境锚点取主体所在概念组+通用场所词,不乱配氛围词。
- 幂等:每次重算覆盖、整段覆盖写 autoheal_added(不 append 累加)。
- 可见性:写 autoheal.log + 打印摘要到 stdout(skill/AI 看)+ apply_verdicts 把补的词显著标在 filtered 页。

用 system python3 跑(纯标准库)。既可独立 `python3 autoheal_queries.py` 跑,也被 harvest_net.py import 调用(必经路径自愈)。
"""
import json, os, re, time

PLATS = [("douyin", "douyin_queries", 2), ("xhs", "xhs_queries", 1), ("bili", "bili_queries", 2)]
# 通用"场所/语境"词:把品牌产品锚到使用场景,避免裸搜出测评/带货(§14 描述≠召回)
PLACE = ["餐厅", "门店", "店里", "餐饮店", "店面", "小店", "后厨"]


def _res_dir(res=None):
    return res or os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def _all_query_words(spec, include_healed=True):
    out = list(spec.get("douyin_queries") or [])
    out += list(spec.get("xhs_queries") or [])
    out += list(spec.get("bili_queries") or [])
    out += [(x[0] if isinstance(x, (list, tuple)) else x) for x in (spec.get("yt_queries") or [])]
    if include_healed:                       # 外部覆盖检查含已补;augment 自检时传 False(只看 base,真幂等)
        for v in (spec.get("autoheal_added") or {}).values():
            out += list(v or [])
    return [w for w in out if w]


def find_missing(spec, scripts, hit_ratio=0.8, include_healed=False):
    """返回漏掉的核心主体:[(词, 命中率, 是否显式声明)]。无条件双轨:显式 core_subjects ∪ 高频候选。"""
    texts = [(s.get("text") or "") for s in scripts]
    n = len(texts) or 1
    explicit = set(spec.get("core_subjects") or [])
    cand = set(explicit) | set(spec.get("hard_anchors") or [])
    for c in (spec.get("concepts") or []):
        cand |= set(c.get("terms") or [])
    qwords = _all_query_words(spec, include_healed=include_healed)
    missing = []
    for w in cand:
        if not w or len(w) < 2:
            continue
        hit = sum(1 for t in texts if w in t) / n
        is_exp = w in explicit
        covered = any(w in q for q in qwords)
        if (is_exp or hit >= hit_ratio) and not covered:   # 显式无条件 / 高频;且 queries 0 覆盖
            missing.append((w, round(hit, 3), is_exp))
    anchors = spec.get("hard_anchors") or []
    aidx = {a: i for i, a in enumerate(anchors)}
    # 确定化排序:品牌锚(hard_anchors 靠前)优先 → 命中率高 → 字典序(消除 set 迭代不定性)
    missing.sort(key=lambda x: (aidx.get(x[0], 999), -x[1], x[0]))
    return missing


def _concept_group_of(subject, spec):
    for c in (spec.get("concepts") or []):
        if subject in (c.get("terms") or []):
            return c
    return None


def _clusters(missing, spec):
    """把漏掉的主体按"概念组"聚类(格力+空调同组→一簇'格力空调';老板/门店各自一簇,绝不乱拼)。
    品牌/产品簇(含 hard_anchors[0] / topic 主体 / 显式声明的词)排最前,优先占额。"""
    anchors = spec.get("hard_anchors") or []
    topic = spec.get("topic", "") or ""
    brand_hint = set(anchors[:1])            # hard_anchors[0] 视为品牌锚(本批=格力)
    groups = {}
    for w, h, e in missing:
        g = _concept_group_of(w, spec)
        gname = g["name"] if g else "__misc__:" + w
        gd = groups.setdefault(gname, {"terms": (g["terms"] if g else [w]), "subjects": [], "maxhit": 0.0, "brand": False})
        gd["subjects"].append((w, h, e))
        gd["maxhit"] = max(gd["maxhit"], h)
        if e or w in brand_hint or (w and w in topic):
            gd["brand"] = True               # 显式声明 / 是品牌锚 / 出现在 topic → 品牌簇
    return sorted(groups.values(), key=lambda d: (0 if d["brand"] else 1, -d["maxhit"]))


def _cluster_phrase(cluster):
    """簇内短词(≤3字)拼成主体短语(格力+空调→格力空调);单词则用自身。"""
    shorts = [w for w, _, _ in cluster["subjects"] if len(w) <= 3][:2]
    if len(shorts) >= 2:
        return "".join(shorts)
    if shorts:
        return shorts[0]
    return cluster["subjects"][0][0]


def gen_queries(missing, spec):
    """按簇生成语境化补词池(品牌簇在前)。语境锚点=本簇概念组的多字术语 + 通用场所词,绝不跨簇乱拼。"""
    pool, seen = [], set()
    for cl in _clusters(missing, spec):
        phrase = _cluster_phrase(cl)
        subj = [w for w, _, _ in cl["subjects"]]
        brand_place = [f"{phrase} {p}" for p in PLACE]          # 格力空调 餐厅 / 格力空调 门店
        ctx = [t for t in cl["terms"] if len(t) >= 4 and t not in subj]  # 餐厅空调/商用空调… 作者手写的语境短语
        ctx.sort(key=lambda t: (0 if any(k in t for k in ("餐厅", "店", "墙")) else 1))
        for q in brand_place[:2] + ctx:
            if q not in seen:
                seen.add(q); pool.append(q)
    return pool


def _brand_phrase(missing, spec):
    cls = _clusters(missing, spec)
    return _cluster_phrase(cls[0]) if cls else (missing[0][0] if missing else "")


def augment(res=None, hit_ratio=0.8):
    """主入口:自查→自愈→写回独立字段→日志。返回 info dict。绝不抛异常打断流程。"""
    res = _res_dir(res)
    info = {"missing": [], "added": {}, "review": False, "note": ""}
    try:
        spec = json.load(open(os.path.join(res, "relevance_spec.json"), encoding="utf-8"))
    except Exception as e:
        info["note"] = "spec 读取失败(跳过自愈,不阻塞):" + str(e)[:120]
        return info
    try:
        scripts = json.load(open(os.path.join(res, "scripts.json"), encoding="utf-8"))
    except Exception:
        scripts = []
    if not scripts:
        info["note"] = "无 scripts.json,跳过自愈(不阻塞)"
        return info
    try:
        missing = find_missing(spec, scripts, hit_ratio)
        info["missing"] = missing
        if not missing:
            # 幂等:已覆盖→清掉可能的旧 autoheal_added(换选题残留),不补
            if spec.get("autoheal_added"):
                spec.pop("autoheal_added", None); spec.pop("autoheal_meta", None)
                _atomic_write(res, spec)
            info["note"] = "核心主体均已被 queries 覆盖,无需自愈"
            _log(res, info, missing); return info
        pool = gen_queries(missing, spec)
        no_yt = os.environ.get("BROLL_NO_YT") == "1"
        no_bili = os.environ.get("BROLL_NO_BILI") == "1"
        added = {}
        # 分发:各平台各取一段(尽量错开减少重叠),封顶
        slices = {"douyin": pool[0:2], "xhs": pool[2:3] or pool[0:1], "bili": pool[3:5] or pool[0:2]}
        for plat, _field, cap in PLATS:
            if plat == "bili" and no_bili:
                continue
            qs = [q for q in slices.get(plat, [])][:cap]
            if qs:
                added[plat] = qs
        # 心虚标记:若全靠频率兜底(没一个显式声明)且补的词里没一条直接含主体,提示复核
        if not any(exp for _, _, exp in missing):
            info["review"] = True
            info["note"] = "core_subjects 字段缺失/未声明,本次靠'正文高频'兜底补出——建议把核心主体显式写进 core_subjects"
        spec["autoheal_added"] = added
        spec["autoheal_meta"] = {
            "ts": int(time.time()),
            "subjects": [{"word": w, "hit": h, "explicit": e} for w, h, e in missing],
            "phrase": _brand_phrase(missing, spec), "review": info["review"],
        }
        _atomic_write(res, spec)
        info["added"] = added
        _log(res, info, missing)
        return info
    except Exception as e:                   # 绝不停机:任何异常记日志后照常返回
        info["note"] = "自愈异常(已跳过,不阻塞收割):" + str(e)[:160]
        _log(res, info, info.get("missing") or [])
        return info


def _atomic_write(res, spec):
    p = os.path.join(res, "relevance_spec.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)                        # 原子替换,避免 harvest_net 读到半写 JSON


def _log(res, info, missing):
    try:
        with open(os.path.join(res, "autoheal.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "missing": missing,
                                "added": info.get("added"), "review": info.get("review"),
                                "note": info.get("note")}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def uncovered_core(spec, scripts, hit_ratio=0.8, include_healed=True):
    """只返回【品牌/产品本体】里仍未覆盖的(给必经红灯断言用)。
    场景词(老板/门店等高频但已被'小店/烟火气/探店'语义覆盖)不算红灯,避免误报。
    品牌/产品 = 显式 core_subjects ∪ 品牌簇(含 hard_anchors[0]/topic 主体/显式声明的那一簇)成员。"""
    miss = find_missing(spec, scripts, hit_ratio, include_healed=include_healed)
    if not miss:
        return []
    brand = set(spec.get("core_subjects") or [])
    for c in _clusters(miss, spec):
        if c["brand"]:
            brand |= set(w for w, _, _ in c["subjects"])
    return [m for m in miss if m[0] in brand]


def merged_queries(spec, platform):
    """读时合并:原 queries ∪ autoheal_added[platform]。harvest_net/apply_verdicts/AI 都用它,原数组永不被改。
    platform ∈ douyin/xhs/bili/yt。yt 返回原样(list of [kw,N]),autoheal 不补 yt。"""
    field = {"douyin": "douyin_queries", "xhs": "xhs_queries", "bili": "bili_queries", "yt": "yt_queries"}[platform]
    base = list(spec.get(field) or [])
    if platform == "yt":
        return base
    add = list((spec.get("autoheal_added") or {}).get(platform) or [])
    out, seen = [], set()
    for q in base + add:
        if q not in seen:
            seen.add(q); out.append(q)
    return out


def _print_summary(info):
    if info.get("note") and not info.get("added"):
        print("[autoheal] " + info["note"]); return
    miss = ", ".join(f"{w}({int(h*100)}%{'·显式' if e else ''})" for w, h, e in info.get("missing") or [])
    print("[autoheal] 检出 queries 漏掉的核心主体:" + (miss or "无"))
    for plat, qs in (info.get("added") or {}).items():
        print(f"[autoheal]   补 {plat}: {qs}")
    if info.get("review"):
        print("[autoheal] ⚠️ " + info.get("note", "靠频率兜底,建议显式声明 core_subjects"))


if __name__ == "__main__":
    _info = augment()
    _print_summary(_info)
