#!/usr/bin/env python3
"""Stage 5/7/9:合并语义分 → 三色判决 → ID去重 → 封面本地化(下载远端封面,顺带让页面能显示)→ pHash 感知去重(折叠搬运近重复)→ 出 filtered.html。
读 candidates.json + scores_part*.json + relevance_spec.json;出 filtered.html + verdicts.json + 本地封面 covers/。
"""
import json, os, glob, html, re, urllib.request, hashlib

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
COV = os.path.join(RES, "covers"); os.makedirs(COV, exist_ok=True)
spec = json.load(open(os.path.join(RES, "relevance_spec.json"), encoding="utf-8"))
PT = spec.get("plat_thresholds", {}); DEFT = {"keep_hi": 60, "review_lo": 40}
MAXDUR = int(spec.get("max_duration_sec", 1200))                    # R2:>此秒数(默认20分钟)直接弃
PEN = spec.get("person_penalty", {"dominant": 35, "partial": 12})   # R1:人物主体降分,spec 可覆盖
DROP_EYE = bool(spec.get("drop_eye_contact", True))                 # R1b:正面半身/全身人脸+眼神盯镜头 → 硬丢开关(默认开)
cands = json.load(open(os.path.join(RES, "candidates.json"), encoding="utf-8"))

# ==== 中性垫片池(filler)平行轨:加载后第一步就把 filler 候选劈出去 ====
# 之后主题轨的全部逻辑(判分/去重/排序/封面本地化/pHash/逐稿匹配/自动勾选)只见 theme 候选,
# 无 filler 时 cands 与今天逐字节一致 → 主题的搜索/召回/排序/打分/筛选/勾选/补勾选零影响。
FILLER = spec.get("filler") or {}
FPT = FILLER.get("plat_thresholds") or {"keep_hi": 55, "review_lo": 35}
FMAX = int(FILLER.get("max_dur_sec", 25))
fillers = [c for c in cands if c.get("pool") == "filler"]
cands = [c for c in cands if c.get("pool") != "filler"]

scores = {}
for f in sorted(glob.glob(os.path.join(RES, "scores_part*.json"))):
    try:
        for r in json.load(open(f, encoding="utf-8")):
            if "idx" in r: scores[r["idx"]] = r
    except Exception as ex: print("⚠️", f, ex)
# filler 判分独立文件 scores_filler_part*.json(AI 按标题+封面判中性场景匹配度,与主题 scores_part 完全分开)
scores_filler = {}
for f in sorted(glob.glob(os.path.join(RES, "scores_filler_part*.json"))):
    try:
        for r in json.load(open(f, encoding="utf-8")):
            if "idx" in r: scores_filler[r["idx"]] = r
    except Exception as ex: print("⚠️", f, ex)

# ---- R3/R4:逐稿匹配表(可选;由 Claude 匹配后产出)→ idx→{persona,name} ----
try:
    SM = json.load(open(os.path.join(RES, "script_matches.json"), encoding="utf-8"))
except Exception:
    SM = {}
IDX2SCRIPT = {}
for _sid, _info in (SM.items() if isinstance(SM, dict) else []):
    for _m in (_info.get("matched") or []):
        if "idx" in _m:
            IDX2SCRIPT[_m["idx"]] = {"persona": _info.get("persona", ""), "name": _info.get("name", "")}
UNDERMATCHED = [(_info.get("name") or "?") for _info in (SM.values() if isinstance(SM, dict) else []) if len((_info.get("matched") or [])) < 2]

# ---- filler 平行逐稿匹配表 filler_matches.json(每稿 ≤6,独立结构,绝不进 IDX2SCRIPT)----
# idx 必须与主题 matched 互斥(AI 匹配时分开列);命中 → filler 卡片自动勾选;某稿 0 条 → 报缺(不静默、不补)。
try:
    FM = json.load(open(os.path.join(RES, "filler_matches.json"), encoding="utf-8"))
except Exception:
    FM = {}
IDX2FILLER = {}
for _sid, _info in (FM.items() if isinstance(FM, dict) else []):
    for _m in (_info.get("matched") or []):
        if "idx" in _m:
            IDX2FILLER[_m["idx"]] = {"name": _info.get("name", ""), "src": _m.get("src_sentence", "")}
FILLER_MISSING = [(_info.get("name") or "?") for _info in (FM.values() if isinstance(FM, dict) else []) if not (_info.get("matched"))]

def verdict(c):
    s = scores.get(c["idx"]); pt = PT.get(c["platform"], DEFT)
    dur = c.get("duration")
    if dur and dur > MAXDUR:                                         # R2:超时长直接弃(透明显示在灰区)
        return "drop", f"超{MAXDUR // 60}分钟", (s.get("score") if s else None)
    if c["stage0"] == "kill": return "drop", "负面:" + c.get("kill_reason", ""), None
    if c["stage0"] == "need_enrich": return "review", "小红书空标题·待看封面", (s.get("score") if s else None)
    if not s: return "review", "未判分", None
    if s.get("eye_contact") and DROP_EYE:                           # R1b:正面半身/全身人脸+眼神盯镜头 → 硬丢(在软降权前短路,封面待定也照丢)
        return "drop", "正面人像·眼神看镜头", s.get("score")
    sc = s.get("score"); rsn = (s.get("reason") or "")[:40]
    pp = s.get("person_primary") or "none"                          # R1:none/partial/dominant
    if pp in ("dominant", "partial") and sc is not None:
        sc = max(0, sc - int(PEN.get(pp, 0))); rsn = (rsn + " ·人物主体")[:46]
    if s.get("need_cover"): return ("keep" if (sc or 0) >= pt["keep_hi"] else "review"), rsn + " ·待封面", sc
    if sc is None: return "review", rsn, None
    if sc >= pt["keep_hi"]: return "keep", rsn, sc
    if sc >= pt.get("review_lo", 40): return "review", rsn, sc
    return "drop", rsn, sc

def verdict_filler(c):
    """中性垫片判决:judge 的是"是否该稿那句话所指的那类干净中性空镜"(标题+封面),不是与主题痛点的关联度。
    保留 R1b 盯镜头硬丢 + person=dominant 丢(正脸/真人主体不是好垫片);25s 硬切;阈值放宽(越多越好但仍卡干净)。"""
    s = scores_filler.get(c["idx"]); dur = c.get("duration")
    if isinstance(dur, (int, float)) and dur > FMAX:          # 25s 硬上限(图文无 duration,永不触发)
        return "drop", f"超{FMAX}秒", (s.get("score") if s else None)
    if c["stage0"] == "kill": return "drop", "负面:" + c.get("kill_reason", ""), None
    if not s: return "review", "未判分(filler)", None
    if s.get("eye_contact") and DROP_EYE:                     # R1b:正面盯镜头 → 硬丢(垫片忌正脸)
        return "drop", "正面人像·眼神看镜头", s.get("score")
    pp = s.get("person_primary") or "none"
    if pp == "dominant":                                       # 真人占满画面=不中性 → 丢
        return "drop", "真人主体·非中性", s.get("score")
    sc = s.get("score"); rsn = ("中性·" + (s.get("reason") or ""))[:44]
    if pp == "partial" and sc is not None:
        sc = max(0, sc - int(PEN.get("partial", 12)))
    if sc is None: return "review", rsn, None
    if sc >= FPT["keep_hi"]: return "keep", rsn, sc
    if sc >= FPT.get("review_lo", 35): return "review", rsn, sc
    return "drop", rsn, sc

for c in cands:
    c["verdict"], c["vreason"], c["vscore"] = verdict(c)
for c in fillers:                                             # filler 各判各的,绝不与主题 cands 混
    c["verdict"], c["vreason"], c["vscore"] = verdict_filler(c)

RANK = {"keep": 2, "review": 1, "drop": 0}
def norm_title(t):
    t = re.sub(r"#\S+", "", t or ""); return re.sub(r"[\s\W_]+", "", t).lower()[:40]
def canon_id(c):
    u = (c.get("page") or "") + " " + (c.get("url") or ""); p = c["platform"]
    if p == "抖音":
        m = re.search(r"/video/(\d{10,})", u)
        if m: return "dy:" + m.group(1)
    if p == "小红书":
        m = re.search(r"/explore/([0-9a-fA-F]{12,})", u)
        if m: return "xhs:" + m.group(1)
    m = re.search(r"/video/(BV[0-9A-Za-z]{8,})", u)
    if m: return "bili:" + m.group(1)
    m = re.search(r"[?&]v=([\w-]{6,})", u) or re.search(r"youtu\.be/([\w-]{6,})", u)
    if m: return "yt:" + m.group(1)
    nt = norm_title(c.get("title")); return ("t:" + p + ":" + nt) if len(nt) >= 4 else ("idx:" + str(c["idx"]))

def stable_id(page, url):
    # 契约4:与 download_server.stable_id 两侧逐字一致(查 dl_precheck.json / 进 manifest / 投 node2)。
    # 兜底用 md5 而非内置 hash():hash() 受 PYTHONHASHSEED 随机化跨进程不同,本文件跑 system py3、
    # download_server 跑 douyin venv py,兜底分支用 hash() 会两侧对不上。
    import hashlib
    s = (page or "") + " " + (url or "")
    m = re.search(r"/video/(\d{10,})", s)
    if m: return "dy_" + m.group(1)
    m = re.search(r"/explore/([0-9a-fA-F]{12,})", s)
    if m: return "xhs_" + m.group(1)
    m = re.search(r"(BV[0-9A-Za-z]{8,})", s)
    if m: return m.group(1)
    m = re.search(r"[?&]v=([\w-]{6,})", s) or re.search(r"youtu\.be/([\w-]{6,})", s)
    if m: return "yt_" + m.group(1)
    return "id_" + hashlib.md5(s.encode("utf-8")).hexdigest()[:10]

# ---- 下前预检(契约3):RES/dl_precheck.json,键=stable_id,值={ok,reason,level};缺失=视全可下 ----
try:
    PRECHECK = json.load(open(os.path.join(RES, "dl_precheck.json"), encoding="utf-8"))
    if not isinstance(PRECHECK, dict): PRECHECK = {}
except Exception:
    PRECHECK = {}

# ---- ID 去重 ----
groups = {}
for c in cands: groups.setdefault(canon_id(c), []).append(c)
reps = []
for k, g in groups.items():
    g.sort(key=lambda x: (RANK[x["verdict"]], x["vscore"] or 0), reverse=True)
    g[0]["_dups"] = len(g) - 1; reps.append(g[0])
id_dups = len(cands) - len(reps)

# ---- 封面本地化(下载远端→本地,顺带让页面显示)----
REF = {"小红书": "https://www.xiaohongshu.com/", "B站/YT": "https://www.bilibili.com/", "抖音": "https://www.douyin.com/"}
def localize(c):
    cov = c.get("cover") or ""
    if cov.startswith("covers/"):
        p = os.path.join(RES, cov); return p if os.path.exists(p) else None
    if not cov.startswith("http"): return None
    p = os.path.join(COV, f"c{c['idx']}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 500:
        c["cover"] = f"covers/c{c['idx']}.jpg"; return p
    try:
        req = urllib.request.Request(cov, headers={"User-Agent": "Mozilla/5.0", "Referer": REF.get(c["platform"], "")})
        data = urllib.request.urlopen(req, timeout=20).read()
        if len(data) > 800:
            open(p, "wb").write(data); c["cover"] = f"covers/c{c['idx']}.jpg"; return p
    except Exception: pass
    return None
from concurrent.futures import ThreadPoolExecutor
def _loc(c):
    c["_cl"] = localize(c); return c["_cl"]
with ThreadPoolExecutor(max_workers=16) as _ex:
    list(_ex.map(_loc, reps))
dl_ok = sum(1 for c in reps if c.get("_cl"))

# ---- pHash 感知去重(折叠搬运近重复)----
ph_dups = 0
try:
    from PIL import Image
    def ahash(p):
        try:
            im = Image.open(p).convert("L").resize((8, 8)); px = list(im.getdata()); a = sum(px) / 64
            return sum(1 << i for i, v in enumerate(px) if v > a)
        except Exception: return None
    def ham(a, b): return bin(a ^ b).count("1")
    for c in reps: c["_ph"] = ahash(c["_cl"]) if c.get("_cl") else None
    reps.sort(key=lambda x: (RANK[x["verdict"]], x["vscore"] or 0), reverse=True)
    seeds = []; kept = []
    TH = 5
    for c in reps:
        if c.get("_ph") is None:
            kept.append(c); continue
        hit = next((s for s in seeds if ham(s["_ph"], c["_ph"]) <= TH), None)
        if hit:
            hit["_dups"] = hit.get("_dups", 0) + 1 + c.get("_dups", 0); ph_dups += 1
        else:
            seeds.append(c); kept.append(c)
    reps = kept
except ImportError:
    print("⚠️ 无 Pillow,跳过 pHash(只做了ID去重)。装:pip3 install --user Pillow")

# 可选后处理(env 开关,不影响默认/其它批次):
# BROLL_MAX_DUR=秒  → 时长超过它的整条从展示集剔除(无 duration 的不剔)
# BROLL_KEEP_TOP=N → keep 只留分数最高的前 N 条,其余降级 review(高分优先)
_maxdur = os.environ.get("BROLL_MAX_DUR")
if _maxdur:
    _md = int(_maxdur); _before = len(reps)
    reps = [c for c in reps if not (isinstance(c.get("duration"), (int, float)) and c["duration"] > _md)]
    print(f"时长 >{_md}s 剔除 {_before - len(reps)} 条")
_keeptop = os.environ.get("BROLL_KEEP_TOP")
if _keeptop:
    _n = int(_keeptop)
    _keep = [c for c in reps if c["verdict"] == "keep"]
    _keep.sort(key=lambda x: -(x["vscore"] or 0))
    for c in _keep[_n:]:
        c["verdict"] = "review"; c["vreason"] = (c.get("vreason") or "") + " ·超额降级"
    print(f"keep 收紧到前 {_n}(原 {len(_keep)} → keep {min(_n, len(_keep))},其余降 review)")

json.dump(reps, open(os.path.join(RES, "verdicts.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ==== filler 平行轨:独立 ID 去重(不与主题 groups 混)+ 独立封面本地化 + 跳 pHash(中性空镜低信息封面会被误折叠把"丰富"删没)====
filler_reps = []
if fillers:
    fgroups = {}
    for c in fillers: fgroups.setdefault(canon_id(c), []).append(c)
    for _k, g in fgroups.items():
        g.sort(key=lambda x: (RANK[x["verdict"]], x["vscore"] or 0), reverse=True)
        g[0]["_dups"] = len(g) - 1; filler_reps.append(g[0])
    with ThreadPoolExecutor(max_workers=16) as _fex:
        list(_fex.map(_loc, filler_reps))
    json.dump(filler_reps, open(os.path.join(RES, "verdicts_filler.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

from collections import Counter, defaultdict
allcnt = Counter(c["verdict"] for c in reps); platcnt = defaultdict(lambda: Counter())
for c in reps: platcnt[c["platform"]][c["verdict"]] += 1
def e(s): return html.escape(str(s), quote=True)
def vcolor(v): return {"keep": "#16a34a", "review": "#f59e0b", "drop": "#9ca3af"}[v]
def fmt(s):  # 秒 → mm:ss(<1h)/ h:mm:ss(≥1h)
    s = int(s); h = s // 3600; m = s % 3600 // 60; r = s % 60
    return f"{h}:{m:02d}:{r:02d}" if h else f"{m}:{r:02d}"
def disp_plat(c):  # 展示用平台标签:把合并的 "B站/YT" 按 URL host 拆成「B站」/「YouTube」分开显示
    u = (c.get("page") or "") + " " + (c.get("url") or "")
    if "youtube.com" in u or "youtu.be" in u: return "YouTube"
    if "bilibili.com" in u or re.search(r"/BV[0-9A-Za-z]{8,}", u): return "B站"
    return c.get("platform", "")
UNDL = []  # ⑥:不可下载条目汇总(供 header dllog 提示主因);[(平台, reason), ...]
def card(c):
    cov = e(c.get("cover") or "")
    thumb = (f'<img class="im" loading="lazy" referrerpolicy="no-referrer" src="{cov}">' if cov and cov.startswith("covers/") else (f'<img class="im" loading="lazy" referrerpolicy="no-referrer" src="{cov}">' if cov else '<div class="ph">无封面</div>'))
    sc = c["vscore"]; scb = f'<span class="badge sc" style="background:{vcolor(c["verdict"])}">{sc if sc is not None else "?"}</span>'
    dup = f'<span class="badge dup">×{c["_dups"]+1}</span>' if c.get("_dups") else ''
    dur = c.get("duration"); durb = f'<span class="badge dur">{fmt(dur)}</span>' if dur else ''
    link = e(c.get("page") or c.get("url") or "#")
    is_filler = c.get("pool") == "filler"
    sm = (IDX2FILLER if is_filler else IDX2SCRIPT).get(c["idx"])        # R3/R4:命中匹配 → 自动勾选+标签(filler 走 IDX2FILLER)
    # ⑥ 下前预检:按本卡 stable_id 查 dl_precheck.json;level==fail → 只提示(灰红角标),不禁勾(缺失/非 fail = 可下)
    sid = stable_id(c.get("page"), c.get("url")); pc = PRECHECK.get(sid)
    undl = bool(pc) and pc.get("level") == "fail"
    undlb = ''
    if undl:
        _rsn = (pc.get("reason") or "")[:24]
        UNDL.append((disp_plat(c), pc.get("reason") or ""))
        undlb = '<span class="badge undl">⚠无法下载' + ((' · ' + e(_rsn)) if _rsn else '') + '</span>'
    chk = " checked" if sm else ""                                      # 匹配到的仍自动勾(即使标了无法下载;只提示不禁选)
    dis = ""                                                            # 不禁选:无法下载项保持可勾选(真实可下性由选片后实测决定)
    if sm and is_filler:                                               # filler:稿名前缀照走(进该稿库),标 🎞 中性、带原句可追溯
        ds = ' data-script="' + e(sm.get("name") or "") + '" data-persona="" data-from-script="' + e(sm.get("src") or "") + '"'
        mtag = '<div class="mtag">🎞 中性 ｜ 📄' + e(sm.get("name") or "") + '</div>'
    elif sm:
        ds = ' data-script="' + e(sm["name"]) + '" data-persona="' + e(sm.get("persona") or "") + '"'
        per = e(sm.get("persona") or "")
        mtag = '<div class="mtag">' + (('👤' + per + ' ｜ ') if per else '') + '📄' + e(sm["name"]) + '</div>'
    else:
        ds = ''; mtag = ''
    return ('<div class="card' + (' undl' if undl else '') + '" data-id="' + e(sid) + '">'
            '<a class="thumb" href="' + link + '" target="_blank" rel="noopener"'
            ' data-plat="' + e(c["platform"]) + '" data-page="' + e(c.get("page") or "") + '" data-url="' + e(c.get("url") or "")
            + '" data-cover="' + cov + '">'
            + thumb + scb + f'<span class="badge plat">{e(disp_plat(c))}</span>{dup}{durb}{undlb}'
            '<label class="chk" onclick="event.stopPropagation()"><input type="checkbox" class="sel" data-id="' + e(sid) + '"'
            ' data-plat="' + e(c["platform"]) + '" data-page="' + e(c.get("page") or "") + '" data-url="' + e(c.get("url") or "")
            + '" data-title="' + e(c.get("title") or "") + '" data-verdict="' + e(c["verdict"])
            + '" data-score="' + e(c["vscore"] if c["vscore"] is not None else "") + '" data-pool="' + ("filler" if is_filler else "theme") + '"' + ds + chk + dis + '></label></a>'
            '<div class="meta"><a href="' + link + '" target="_blank" rel="noopener">' + e(c.get("title") or "(无标题)") + '</a>' + mtag + '<div class="sub">' + e(c["vreason"]) + '</div></div></div>')
def zone(items): return "".join(card(c) for c in sorted(items, key=lambda x: -(x["vscore"] or 0)))
def zone_filler(items):  # filler 区按 duration 升序(越短越靠前);无 duration(图文)排最后
    def k(x):
        d = x.get("duration"); return d if isinstance(d, (int, float)) else 1e9
    return "".join(card(c) for c in sorted(items, key=k))
def zsel(zid):  # 标题行内联:一个三态全选复选框(全选✓/部分=横线/空)+ 本区已选计数;stopPropagation 防 summary 折叠
    return (f'<label class="zsel" onclick="event.stopPropagation()">'
            f'<input type="checkbox" class="zall" data-zone="{zid}">'
            f'<span class="zcount" id="cnt-{zid}">已选 0</span></label>')
keep = [c for c in reps if c["verdict"] == "keep"]; review = [c for c in reps if c["verdict"] == "review"]; drop = [c for c in reps if c["verdict"] == "drop"]
filler_use = [c for c in filler_reps if c["verdict"] in ("keep", "review")]; filler_drop = [c for c in filler_reps if c["verdict"] == "drop"]
CSS = ("*{box-sizing:border-box}body{margin:0;font-family:-apple-system,'PingFang SC','Microsoft YaHei',Arial,sans-serif;background:#f6f7f9;color:#1a1a1a}header{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid #e5e7eb;padding:10px 18px;box-shadow:0 1px 4px rgba(0,0,0,.05);display:flex;align-items:center;gap:16px;flex-wrap:wrap}header h1{font-size:15px;margin:0}.note{font-size:12px;color:#6b7280;margin-top:4px}main{padding:16px;max-width:1480px;margin:0 auto}details{margin:10px 0}summary{cursor:pointer;font-size:15px;font-weight:700;padding:8px 0}h2{font-size:16px;margin:14px 0 8px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}.card{position:relative;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;display:flex;flex-direction:column}.card:hover{box-shadow:0 3px 12px rgba(0,0,0,.10)}.thumb{position:relative;display:block;width:100%;aspect-ratio:3/4;background:#000;overflow:hidden}.im{width:100%;height:100%;object-fit:cover;display:block}.ph{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#888;background:#111;font-size:13px}.badge{position:absolute;color:#fff;font-size:12px;padding:2px 7px;border-radius:5px;font-weight:700}.sc{left:6px;top:6px}.plat{right:6px;top:6px;background:rgba(0,0,0,.7);font-weight:400;font-size:11px}.dup{right:6px;bottom:6px;background:#7c3aed;font-size:11px}.chk{position:absolute;left:6px;bottom:6px;z-index:7;background:rgba(255,255,255,.88);border-radius:4px;padding:2px;line-height:0}.chk input{width:18px;height:18px;cursor:pointer;display:block}.meta{padding:8px 10px}.meta a{font-size:13px;color:#111;text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.35;min-height:2.7em}.sub{font-size:11px;color:#9ca3af;margin-top:4px}.mtag{font-size:11px;color:#3730a3;background:#eef2ff;border:1px solid #c7d2fe;border-radius:4px;padding:1px 6px;margin-top:4px;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
       ".dlbtn{background:#2563eb;color:#fff;border:0;border-radius:7px;padding:7px 14px;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap}.dlbtn:hover{background:#1d4ed8}.dlbtn:disabled{background:#9ca3af;cursor:default}"
       ".hleft{flex:1;min-width:0}.hleft .note{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.hright{display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end;max-width:60%}"
       ".dlbtn2{background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;border-radius:7px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}.dlbtn2:hover{background:#e0e7ff}.dlbtn2:disabled{opacity:.6;cursor:default}.pickwrap{display:flex;flex-direction:column;align-items:flex-start;gap:3px;margin-top:8px}.dirshow{font-size:11px;color:#6b7280;font-family:ui-monospace,Menlo,monospace;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left}"
       ".dllog{font-size:12px;color:#374151;font-family:ui-monospace,Menlo,monospace;flex-basis:100%;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
       ".dur{left:50%;bottom:6px;transform:translateX(-50%);background:rgba(0,0,0,.78);font-weight:400;font-size:11px;font-family:ui-monospace,Menlo,monospace}"
       ".card.undl{opacity:.6}.card.undl .thumb{filter:grayscale(.5)}.badge.undl{left:6px;top:30px;background:#b91c1c;color:#fff;font-size:11px;font-weight:600;max-width:88%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.card.undl .chk{opacity:.5}"
       ".zsel{display:inline-flex;align-items:center;gap:6px;font-weight:400;font-size:13px;cursor:pointer;user-select:none;vertical-align:middle}.zsel input{width:16px;height:16px;cursor:pointer}.zcount{font-size:12px;color:#6b7280}"
       ".play{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent;z-index:5;object-fit:cover;opacity:0;transition:opacity .25s ease}.play.ready{opacity:1}")
CSS += (".selected-workspace[hidden],#allWorkspace[hidden]{display:none}.selected-workspace{padding-bottom:18px}"
        ".selection-restoring input.sel,.selection-restoring input.zall,.selection-restoring #showSelected{pointer-events:none;opacity:.55}"
        ".selected-empty{padding:42px 16px;text-align:center;color:#6b7280;background:#fff;border:1px dashed #cbd5e1;border-radius:10px}.selected-empty[hidden]{display:none}"
        ".card.picked{border-color:#6366f1;box-shadow:0 0 0 1px rgba(99,102,241,.2)}"
        ".card.focus-hit{outline:3px solid #f97316;outline-offset:4px;box-shadow:0 0 0 7px rgba(249,115,22,.24),0 10px 28px rgba(0,0,0,.2);animation:selectionPulse .8s ease-out 1}"
        "@keyframes selectionPulse{0%{transform:scale(.97)}55%{transform:scale(1.015)}100%{transform:scale(1)}}")
psum = " | ".join(f"{p} 留{platcnt[p]['keep']}/审{platcnt[p]['review']}/弃{platcnt[p]['drop']}" for p in platcnt)
_topic = re.sub(r'[\\/:*?"<>|·()（）【】\s]+', "", spec.get("topic", "") or "")[:24] or "未命名选题"
_topic_disp = (spec.get("topic", "") or "").strip()[:40] or "未命名选题"  # 出页标题用的可读选题(随 spec 走,不再写死米卢/2002)
DEFAULT_DL_DIR = "~/Downloads/af素材/" + _topic  # 下载默认落点(按选题归类);用户可在页面顶部改
DLPORT = os.environ.get("DOWNLOAD_PORT", "8788")  # 与 download_server.py 同一端口(可 env 覆盖;出页 JS 据此连端点)
SELECTION_NS = hashlib.sha256(os.path.abspath(RES).encode("utf-8")).hexdigest()[:16]
JS = r"""
document.documentElement.classList.add("selection-restoring");
(async function(){
 /* ④ 端口运行期发现:同源读 ./.dlport(download_server 启动写真实端口),读不到/异常回落默认 __DLPORT__ */
 var BASE="http://127.0.0.1:__DLPORT__";
 try{ var pr=await fetch("./.dlport",{cache:"no-store"}); var pt=(await pr.text()).trim(); if(/^\d+$/.test(pt)) BASE="http://127.0.0.1:"+pt; }catch(_){}
 var EP=BASE+"/download", PV=BASE+"/preview", SELECTION_NS="__SELECTION_NS__";
 var SELECTION_KEY="af_selection:__SELECTION_NS__", selectionRevision=0, selectionServerRevision=0, saveEpoch=0, saveChain=Promise.resolve();
 function fetchWithTimeout(url,options,ms){
  if(typeof AbortController==="undefined")return fetch(url,options);
  var controller=new AbortController(),timer=setTimeout(function(){controller.abort();},ms);
  var opts=Object.assign({},options||{},{signal:controller.signal});
  return fetch(url,opts).finally(function(){clearTimeout(timer);});
 }
 function allSels(){return Array.prototype.slice.call(document.querySelectorAll("input.sel"));}
 /* selection-overrides:start — 409 后只重放尚未由服务端确认的逐素材人工操作 */
 var pendingOverrides={},overrideSeq=0;
 function recordOverride(id,checked){if(id)pendingOverrides[id]={checked:!!checked,seq:++overrideSeq};}
 function captureOverrides(){var out={};Object.keys(pendingOverrides).forEach(function(id){var x=pendingOverrides[id];out[id]={checked:x.checked,seq:x.seq};});return out;}
 function replayOverrides(){allSels().forEach(function(c){var x=pendingOverrides[c.dataset.id];if(x)c.checked=x.checked;});}
 function clearConfirmed(sent){Object.keys(sent).forEach(function(id){var current=pendingOverrides[id];if(current&&current.seq===sent[id].seq)delete pendingOverrides[id];});}
 function recordStateDiff(base,desired){
  var before={},after={},desiredKnown={};base.selected_ids.forEach(function(id){before[id]=1;});desired.selected_ids.forEach(function(id){after[id]=1;});desired.known_ids.forEach(function(id){desiredKnown[id]=1;});
  allSels().forEach(function(c){var id=c.dataset.id;if(desiredKnown[id]&&!!before[id]!==!!after[id])recordOverride(id,!!after[id]);});
 }
 /* selection-overrides:end */
 function normalizeState(raw){
  if(!raw||raw.namespace!==SELECTION_NS||raw.version!==1||!Number.isInteger(raw.revision)||raw.revision<0||!Array.isArray(raw.known_ids)||!Array.isArray(raw.selected_ids))return null;
  var known=[],ks={},selected=[],ss={};
  for(var i=0;i<raw.known_ids.length;i++){var id=raw.known_ids[i];if(typeof id!=="string"||!id||id.length>200)return null;if(!ks[id]){ks[id]=1;known.push(id);}}
  for(var j=0;j<raw.selected_ids.length;j++){var sid=raw.selected_ids[j];if(typeof sid!=="string"||!ks[sid])return null;if(!ss[sid]){ss[sid]=1;selected.push(sid);}}
  return {namespace:SELECTION_NS,version:1,revision:raw.revision,known_ids:known,selected_ids:selected,updated_at:raw.updated_at||""};
 }
 function applyState(state){
  var known={},selected={}; state.known_ids.forEach(function(id){known[id]=1;}); state.selected_ids.forEach(function(id){selected[id]=1;});
  allSels().forEach(function(c){if(known[c.dataset.id])c.checked=!!selected[c.dataset.id];}); selectionRevision=state.revision;
 }
 function snapshot(bump){
  if(bump)selectionRevision++;
  var known=[],selected=[],seen={},picked={};
  allSels().forEach(function(c){var id=c.dataset.id;if(!id)return;if(!seen[id]){seen[id]=1;known.push(id);}if(c.checked)picked[id]=1;});
  known.forEach(function(id){if(picked[id])selected.push(id);});
  return {namespace:SELECTION_NS,version:1,revision:selectionRevision,known_ids:known,selected_ids:selected,updated_at:new Date().toISOString()};
 }
 function readLocal(){try{return normalizeState(JSON.parse(localStorage.getItem(SELECTION_KEY)||"null"));}catch(_){return null;}}
 function writeLocal(state){try{localStorage.setItem(SELECTION_KEY,JSON.stringify(state));}catch(_){}}
 function enqueueState(state){
  var epoch=saveEpoch,sentOverrides=captureOverrides();
  saveChain=saveChain.catch(function(){}).then(async function(){
   if(epoch!==saveEpoch)return;
   var payload=Object.assign({},state,{base_revision:selectionServerRevision});
   var resp=await fetchWithTimeout(BASE+"/selection",{method:"POST",headers:{"Content-Type":"text/plain"},body:JSON.stringify(payload)},1800);
   var data=null;try{data=await resp.json();}catch(_){}
   if(resp.status===409&&data&&data.state){
    var latest=normalizeState(data.state);if(!latest)throw new Error("选择状态冲突响应无效");
    saveEpoch++;selectionServerRevision=latest.revision;applyState(latest);replayOverrides();
    var currentIds={};allSels().forEach(function(c){currentIds[c.dataset.id]=1;});
    var serverKnown={};latest.known_ids.forEach(function(id){serverKnown[id]=1;});
    var hasNew=allSels().some(function(c){return !serverKnown[c.dataset.id];});
    var hasRemoved=latest.known_ids.some(function(id){return !currentIds[id];});
    var needsRetry=Object.keys(pendingOverrides).length>0||hasNew||hasRemoved;
    var reconciled=snapshot(needsRetry);writeLocal(reconciled);refresh();
    var el=document.getElementById("dllog");if(el)el.textContent="⚠ 另一个页面已更新选择，已载入最新状态";
    if(needsRetry)setTimeout(function(){enqueueState(reconciled);},0);
    return;
   }
   if(!resp.ok){
    if(resp.status===409)throw new Error("当前下载服务属于另一个选题，未写入选择状态");
    throw new Error("HTTP "+resp.status);
   }
   if(!data||data.namespace!==SELECTION_NS||!Number.isInteger(data.revision))throw new Error("选择状态保存响应无效");
   selectionServerRevision=data.revision;clearConfirmed(sentOverrides);
  }).catch(function(err){var el=document.getElementById("dllog");if(el)el.textContent=String(err&&err.message||err).indexOf("另一个选题")>=0?"⚠ 当前下载服务属于另一个选题，选择仅保存在浏览器":"⚠ 选择已保存在浏览器，下载服务恢复后会继续同步";console.warn("selection save failed",err);});
  return saveChain;
 }
 function queueSave(bump){var state=snapshot(bump);writeLocal(state);return enqueueState(state);}
 async function restoreSelection(){
  var local=readLocal(),server=null,serverInitialized=false,serverReadOk=false;
  var initialPageState=snapshot(false);
  try{
   var resp=await fetchWithTimeout(BASE+"/selection",{cache:"no-store"},1800);
   if(!resp.ok)throw new Error("HTTP "+resp.status);
   var raw=await resp.json();if(raw.namespace!==SELECTION_NS)throw new Error("selection namespace mismatch");
   serverInitialized=!!raw.initialized;selectionServerRevision=raw.revision;if(serverInitialized)server=normalizeState(raw);
   if(serverInitialized&&!server)throw new Error("invalid server selection state");
   serverReadOk=true;
  }catch(err){var warn=document.getElementById("dllog");if(warn)warn.textContent="⚠ 选择状态服务暂不可读，当前使用浏览器备份";console.warn("selection restore failed",err);}
  var chosen=null;
  if(local&&server)chosen=local.revision>server.revision?local:server;else chosen=local||server;
  if(chosen===local&&local)recordStateDiff(server||initialPageState,local);
  if(chosen)applyState(chosen);
  var oldKnown={};if(chosen)chosen.known_ids.forEach(function(id){oldKnown[id]=1;});
  var currentIds={};allSels().forEach(function(c){currentIds[c.dataset.id]=1;});
  var hasNew=allSels().some(function(c){return !oldKnown[c.dataset.id];});
  var hasRemoved=!!chosen&&chosen.known_ids.some(function(id){return !currentIds[id];});
  var state=snapshot(!chosen||hasNew||hasRemoved);writeLocal(state);
  if(serverReadOk&&(!serverInitialized||!server||(local&&server&&local.revision>server.revision)||hasNew||hasRemoved))enqueueState(state);
 }
 var allCards=Array.prototype.slice.call(document.querySelectorAll(".card"));
 var selectionHomes=new Map(),selectedMode=false,focusedCard=null;
 var allWorkspace=document.getElementById("allWorkspace"),selectedWorkspace=document.getElementById("selectedWorkspace");
 var selectedGrid=document.getElementById("selectedGrid"),selectedEmpty=document.getElementById("selectedEmpty");
 var showSelected=document.getElementById("showSelected"),selectedTotal=document.getElementById("selectedTotal");
 allCards.forEach(function(card){var marker=document.createComment("selection-home");card.parentNode.insertBefore(marker,card);selectionHomes.set(card,marker);});
 function restoreCard(card){var marker=selectionHomes.get(card);if(marker&&marker.parentNode)marker.parentNode.insertBefore(card,marker.nextSibling);}
 function clearFocused(){if(focusedCard)focusedCard.classList.remove("focus-hit");focusedCard=null;}
 function syncSelectedWorkspace(){
  if(!selectedMode)return;
  allCards.forEach(function(card){var c=card.querySelector("input.sel");if(c&&c.checked&&!c.disabled)selectedGrid.appendChild(card);else restoreCard(card);});
  if(focusedCard&&focusedCard.parentNode!==selectedGrid)clearFocused();
 }
 function setSelectedMode(on){
  stopPlay();clearFocused();selectedMode=!!on;
  if(selectedMode){allWorkspace.hidden=true;selectedWorkspace.hidden=false;syncSelectedWorkspace();}
  else{allCards.forEach(restoreCard);selectedWorkspace.hidden=true;allWorkspace.hidden=false;}
  refresh();
 }
 function selectedCardsInView(){
  var source=selectedMode?Array.prototype.slice.call(selectedGrid.querySelectorAll(".card")):allCards;
  return source.filter(function(card){var c=card.querySelector("input.sel");return c&&c.checked&&!c.disabled;});
 }
 function cardLocatorTop(card){
  var d=card.closest("details"),closed=null;
  while(d){if(!d.open)closed=d;d=d.parentElement?d.parentElement.closest("details"):null;}
  var node=closed?(closed.querySelector("summary")||closed):card;
  return node.getBoundingClientRect().top;
 }
 function openCardDetails(card){
  var d=card.closest("details");
  while(d){d.open=true;d=d.parentElement?d.parentElement.closest("details"):null;}
 }
 function focusNextSelected(){
  var cards=selectedCardsInView(),log=document.getElementById("dllog");
  if(!cards.length){clearFocused();if(log)log.textContent="当前没有已选素材";return;}
  var idx=focusedCard?cards.indexOf(focusedCard):-1,target;
  if(idx>=0)target=cards[(idx+1)%cards.length];
  else{
   var header=document.querySelector("header"),edge=header?header.getBoundingClientRect().bottom:0;
   target=cards.find(function(card){return cardLocatorTop(card)>=edge-2;})||cards[0];
  }
  clearFocused();focusedCard=target;openCardDetails(target);target.classList.add("focus-hit");
  var pos=cards.indexOf(target)+1;if(log)log.textContent="已选 "+pos+" / "+cards.length+"（F1 下一条）";
  requestAnimationFrame(function(){target.scrollIntoView({behavior:"smooth",block:"center"});});
 }
 showSelected.addEventListener("click",function(){setSelectedMode(!selectedMode);});
 document.addEventListener("keydown",function(e){if(e.key==="F1"&&!e.altKey&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey){e.preventDefault();focusNextSelected();}});
 /* ⑥ 仅统计/操作可下(未 disabled)的 checkbox */
 function sels(z){return Array.prototype.slice.call(document.querySelectorAll('.grid[data-zone="'+z+'"] input.sel')).filter(function(c){return !c.disabled;});}
 function refresh(){
  ["keep","review","drop","filler","filler-drop"].forEach(function(z){
   var arr=sels(z), n=arr.filter(function(c){return c.checked;}).length;
   var el=document.getElementById("cnt-"+z); if(el)el.textContent="已选 "+n;
   var za=document.querySelector('.zall[data-zone="'+z+'"]');
   if(za){ za.checked=n>0&&n===arr.length; za.indeterminate=n>0&&n<arr.length; }
  });
  var checked=allSels().filter(function(c){return c.checked&&!c.disabled;}),tot=checked.length;
  allCards.forEach(function(card){var c=card.querySelector("input.sel");card.classList.toggle("picked",!!(c&&c.checked&&!c.disabled));});
  if(selectedMode)syncSelectedWorkspace();
  showSelected.textContent=(selectedMode?"返回全部素材（":"展示已选（")+tot+"）";
  selectedTotal.textContent=tot;selectedEmpty.hidden=tot>0;
  var b=document.getElementById("dlSel"); if(b){b.textContent="⬇ 下载选中 ("+tot+")"; b.disabled=tot===0;}
  var cb=document.getElementById("clnSel"); if(cb){cb.textContent="🧹 批量清洗选中 ("+tot+")"; cb.disabled=tot===0;}
 }
 function handleSelectionChange(e){
  var t=e.target;
  if(t.classList&&t.classList.contains("zall")){
   var z=t.getAttribute("data-zone"), arr=sels(z);
   var allOn=arr.length>0&&arr.every(function(c){return c.checked;});
   var on=!allOn; arr.forEach(function(c){c.checked=on;recordOverride(c.dataset.id,on);}); refresh(); queueSave(true); return;   /* 三态:原本全选才取消,空/部分都→全选 */
  }
  if(t.classList&&t.classList.contains("sel")){
   allSels().forEach(function(c){if(c!==t&&c.dataset.id===t.dataset.id)c.checked=t.checked;});
   recordOverride(t.dataset.id,t.checked);
   if(focusedCard===t.closest(".card")&&!t.checked)clearFocused();
   refresh(); queueSave(true);
  }
 }
 /* 悬浮自动播放:全局单例 + 300ms 防抖 + 事件委托(329 卡不逐个绑,移开即停) */
 var playing=null, hoverTimer=null;
 function stopPlay(){ if(playing){ var p=playing.querySelector(".play"); if(p){ try{p.pause&&p.pause();}catch(_){}; p.remove(); } playing=null; } }
 function embedUrl(host){
  var m;
  if(m=host.match(/[?&]v=([\w-]{6,})/)) return "https://www.youtube.com/embed/"+m[1]+"?autoplay=1&mute=1&playsinline=1";
  if(m=host.match(/youtu\.be\/([\w-]{6,})/)) return "https://www.youtube.com/embed/"+m[1]+"?autoplay=1&mute=1&playsinline=1";
  if(m=host.match(/(BV[0-9A-Za-z]{8,})/)) return "//player.bilibili.com/player.html?bvid="+m[1]+"&autoplay=1&muted=1&danmaku=0&high_quality=0";
  return null;
 }
 function startPlay(thumb){
  if(playing===thumb)return; stopPlay();
  var d=thumb.dataset, host=(d.page||"")+" "+(d.url||""), plat=d.plat||"", el=null;
  var viaProxy = plat.indexOf("小红书")>=0 || plat.indexOf("抖音")>=0 || /douyin\.com|xiaohongshu\.com/.test(host);
  if(viaProxy){                             /* 小红书/抖音:经 /preview 同源代理喂 <video>;封面当 poster→出帧前显封面、绝不黑屏;playing 才淡入 */
   el=document.createElement("video"); el.className="play"; el.muted=true; el.loop=true; el.autoplay=true; el.setAttribute("playsinline","");
   if(d.cover) el.poster=d.cover;
   el.addEventListener("playing",function(){el.classList.add("ready");});
   el.src=PV+"?page="+encodeURIComponent(d.page||d.url||"");
  } else {                                  /* B站/YT:官方 iframe;load 后稍候淡入(盖在封面上,不黑屏);抖音以外拿不到的不注入 */
   var u=embedUrl(host); if(!u) return;
   el=document.createElement("iframe"); el.className="play"; el.allow="autoplay; encrypted-media"; el.setAttribute("frameborder","0");
   el.addEventListener("load",function(){ setTimeout(function(){ if(el.parentNode) el.classList.add("ready"); },600); });
   el.src=u;
  }
  thumb.appendChild(el); playing=thumb;
 }
 document.addEventListener("mouseover",function(e){
  var th=e.target.closest&&e.target.closest(".thumb"); if(!th)return;
  clearTimeout(hoverTimer); hoverTimer=setTimeout(function(){startPlay(th);},120);
 });
 document.addEventListener("mouseout",function(e){
  var th=e.target.closest&&e.target.closest(".thumb"); if(!th)return;
  if(e.relatedTarget&&th.contains(e.relatedTarget))return;   /* 仍在卡内(移到角标)不算移出 */
  clearTimeout(hoverTimer); if(playing===th)stopPlay();
 });
 /* 下载 */
 var dl=document.getElementById("dlSel"), log=document.getElementById("dllog");
 dl.addEventListener("click",async function(){
  if(picking) return;
  var checked=Array.prototype.slice.call(document.querySelectorAll('input.sel')).filter(function(c){return c.checked&&!c.disabled;});
  if(!checked.length){log.textContent="先勾选要下载的素材";return;}
  dl.disabled=true; picking=true; log.textContent="请选择下载文件夹…";   /* 先弹文件夹选择框,确定后再下 */
  var dir;
  try{ var pr=await fetch(BASE+"/pickdir"); dir=(await pr.text()).trim(); }
  catch(_){ log.textContent="✗ 连不上下载端点,请先起 download_server.py(douyin venv python)"; dl.disabled=false; picking=false; return; }
  picking=false;
  if(!dir){ log.textContent="已取消(未选择文件夹)"; dl.disabled=false; return; }   /* 取消=不下载 */
  lastDir=dir;   /* ① 记下本次下载目录;批量清洗复用它当 .clean_tmp 的归属目录 */
  var items=checked.map(function(c){var d=c.dataset;return {platform:d.plat,page:d.page,url:d.url,title:d.title,verdict:d.verdict,score:d.score,script_name:d.script||"",persona:d.persona||"",pool:d.pool||"theme",from_script:d.fromScript||""};});
  log.textContent="开始下载 "+items.length+" 个 → "+dir;
  var ok=0,skip=0,fail=0;
  var CN={bilibili:"B站",youtube:"YouTube",xiaohongshu:"小红书",douyin:"抖音"};
  var ST={ok:"✓ 已下",skip:"⏭ 已存在",fail:"✗ 失败"};
  try{
   var resp=await fetch(EP,{method:"POST",headers:{"Content-Type":"text/plain"},body:JSON.stringify({dir:dir,items:items})});
   var reader=resp.body.getReader(),dec=new TextDecoder(),buf="";
   while(true){
    var r=await reader.read(); if(r.done)break;
    buf+=dec.decode(r.value,{stream:true}); var lines=buf.split("\n"); buf=lines.pop();
    lines.forEach(function(ln){ if(!ln.trim())return; var o; try{o=JSON.parse(ln);}catch(_){return;}
     if(o.event==="item"){ if(o.status==="ok")ok++; else if(o.status==="skip")skip++; else fail++;
      log.textContent="正在下载 ["+o.i+"/"+o.total+"] "+(CN[o.platform]||o.platform)+" "+(ST[o.status]||o.status)+(o.mb?(" "+o.mb+"MB"):"")+(o.warn?(" ⚠"+o.warn):"")+(o.error?(" — "+o.error):"")+"   |   成功 "+ok+" · 跳过 "+skip+" · 失败 "+fail;
     } else if(o.event==="done"){ log.textContent="✅ 完成:成功 "+o.ok+" · 跳过 "+o.skip+"(已存在) · 失败 "+o.fail+(o.fail?" · 失败项见下载目录 _manifest.jsonl":"")+" · 已打开文件夹"; }
    });
   }
  }catch(err){ log.textContent="✗ 连不上下载端点,请先起 download_server.py(用 douyin venv python 跑)。"; }
  dl.disabled=false; refresh();
 });
 /* 批量清洗:不下到本地、不弹文件夹;POST /clean → 秒回 job_id → 跳清洗页 */
 var cln=document.getElementById("clnSel");
 cln.addEventListener("click",async function(){
  var checked=Array.prototype.slice.call(document.querySelectorAll('input.sel')).filter(function(c){return c.checked&&!c.disabled;});
  if(!checked.length){log.textContent="先勾选要清洗的素材";return;}
  cln.disabled=true; var prev=cln.textContent; cln.textContent="清洗登记中…";
  var items=checked.map(function(c){var d=c.dataset;return {platform:d.plat,page:d.page,url:d.url,title:d.title,verdict:d.verdict,score:d.score,script_name:d.script||"",persona:d.persona||"",pool:d.pool||"theme",from_script:d.fromScript||""};});
  var jobUrl=null;
  try{
   /* ① 带 dir(复用下载目录框,与「下载选中」同源)→ .clean_tmp 落对选题、目录归类正确 */
   var resp=await fetch(BASE+"/clean",{method:"POST",headers:{"Content-Type":"text/plain"},body:JSON.stringify({dir:lastDir,items:items})});
   var reader=resp.body.getReader(),dec=new TextDecoder(),buf="";
   while(true){
    var r=await reader.read(); if(r.done)break;
    buf+=dec.decode(r.value,{stream:true}); var lines=buf.split("\n"); buf=lines.pop();
    lines.forEach(function(ln){ if(!ln.trim())return; var o; try{o=JSON.parse(ln);}catch(_){return;}
     if(o.event==="start"){ jobUrl=o.url; try{localStorage.setItem("mc_last_job",o.job_id);}catch(_){}
      log.innerHTML="✅ 任务已建("+o.total+"条)task_id="+o.job_id+" → <a href='"+o.url+"' target='_blank' style='color:#2563eb;font-weight:700;text-decoration:underline'>打开清洗进度页</a>"; }
     else if(o.event==="item"){ log.textContent="["+o.i+"/"+o.total+"] "+(o.title||"")+" — "+o.status+(o.warn?(" ⚠"+o.warn):"")+(o.error?(" "+o.error):""); }
     else if(o.event==="done"){ if(jobUrl) window.open(jobUrl,"_blank"); log.innerHTML="✅ 本批已全部投递,清洗在后台进行 → <a href='"+jobUrl+"' target='_blank' style='color:#2563eb;font-weight:700;text-decoration:underline'>打开清洗进度页</a>"; }
     else if(o.event==="error"){ log.textContent="✗ "+o.error; }
    });
   }
  }catch(err){ log.textContent="✗ 连不上下载端点,请先起 download_server.py(douyin venv python)。"; }
  cln.textContent=prev; refresh();
 });
 /* 选择下载文件夹:点击唤起本机原生文件夹选择器(download_server 跑 osascript choose folder),拿真实路径回填 */
 var picking=false;   /* 文件夹选择框互斥(由「下载选中」触发) */
 var lastDir="__DEFAULT_DL_DIR__";   /* ① 下载目录(与「下载选中」同源);批量清洗复用它当 .clean_tmp 归属;下载选中后会被真实选中目录覆盖 */
 /* 滚动预解析:小红书卡片进视野就让 /preview 后台现解+缓存 CDN,悬浮时秒开(抖音不预解,省 KR 解析额度;B站/YT iframe 无需) */
 var warmed={}, warmActive=0, warmQ=[];
 function pumpWarm(){
  while(warmActive<3 && warmQ.length){
   var pg=warmQ.shift(); warmActive++;
   fetch(BASE+"/preview?warm=1&page="+encodeURIComponent(pg)).catch(function(){}).then(function(){ warmActive--; pumpWarm(); });
  }
 }
 if("IntersectionObserver" in window){
  var io=new IntersectionObserver(function(es){
   es.forEach(function(en){ if(!en.isIntersecting)return; var d=en.target.dataset;
    if((d.plat||"").indexOf("小红书")>=0 && d.page && !warmed[d.page]){ warmed[d.page]=1; warmQ.push(d.page); pumpWarm(); }
    io.unobserve(en.target);
   });
  },{rootMargin:"400px"});
  Array.prototype.slice.call(document.querySelectorAll('.thumb')).forEach(function(t){io.observe(t);});
 }
 try{await restoreSelection();}finally{document.documentElement.classList.remove("selection-restoring");}
 document.addEventListener("change",handleSelectionChange);
 refresh();
})();
"""
# 先渲染三区(card 内会填充 UNDL),再据此算不可下汇总 + 占位替换
_zk = zone(keep); _zr = zone(review); _zd = zone(drop)
_zf = zone_filler(filler_use); _zfd = zone_filler(filler_drop)
_filler_html = ''
if filler_reps:
    _fmiss = (f'<div class="note" style="color:#b45309">⚠ 这些稿据稿句没派生出中性垫片(0条·不静默不补):{e("、".join(FILLER_MISSING))}</div>' if FILLER_MISSING else '')
    _filler_html = (
        f'<details open><summary>🎞 中性垫片池 {len(filler_use)}(每稿≤{FILLER.get("per_script_cap", 6)} · 按时长升序 · {FMAX}s内) {zsel("filler")}</summary>'
        + _fmiss
        + f'<div class="grid" data-zone="filler">{_zf}</div>'
        + (f'<details><summary>⚪ 垫片·弃 {len(filler_drop)}(可展开查误杀)</summary><div class="grid" data-zone="filler-drop">{_zfd}</div></details>' if filler_drop else '')
        + '</details>')
# ⑥ header dllog 汇总不可下条数与主因(如"小红书 N 条 token过期/缺映射,建议重收割")
def _undl_summary():
    if not UNDL: return ""
    from collections import Counter as _C
    plat_cnt = _C(p for p, _r in UNDL); reasons = _C(r for _p, r in UNDL)
    parts = []
    for _p, _n in plat_cnt.most_common():
        _rs = [r for pp, r in UNDL if pp == _p and r]
        main = _C(_rs).most_common(1)[0][0] if _rs else ""
        tip = "(建议重收割)" if (_p == "小红书" and ("token" in main or "映射" in main or "缺" in main)) else ""
        parts.append(f"{_p} {_n} 条" + (f":{main[:20]}" if main else "") + tip)
    return f"⚠ {len(UNDL)} 条预检无法下载(仍可勾选,真实可下性以实测为准):" + " · ".join(parts)
_dllog_init = e(_undl_summary())
JS = (JS.replace("__DLPORT__", DLPORT)
        .replace("__DEFAULT_DL_DIR__", DEFAULT_DL_DIR.replace("\\", "\\\\").replace('"', '\\"'))
        .replace("__SELECTION_NS__", SELECTION_NS))  # 端口/默认目录/当前 BROLL_RES 选择状态命名空间

# 本批查询词展示(纯 CSS hover 面板;单一事实源=relevance_spec.json 的四组 queries + autoheal_added)
CSS += (".qwrap{position:relative;display:inline-block}"
        ".qbtn{background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;border-radius:7px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}"
        ".qpanel{display:none;position:absolute;top:100%;right:0;z-index:30;background:#fff;border:1px solid #e5e7eb;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.14);padding:12px 14px;width:440px;max-height:62vh;overflow:auto;text-align:left;margin-top:2px}"
        ".qwrap:hover .qpanel{display:block}"
        ".qgroup{margin-bottom:10px;font-size:12px;line-height:1.9}"
        ".qchip{display:inline-block;background:#f3f4f6;border-radius:5px;padding:2px 8px;margin:2px 4px 2px 0;color:#374151;font-size:12px}"
        ".qheal{color:#fff;background:#f59e0b;border-radius:4px;font-size:10px;margin-left:4px;padding:0 4px;font-weight:700}")

def _qpanel():
    groups = [("抖音", "douyin"), ("小红书", "xhs"), ("B站", "bili"), ("YouTube", "yt")]
    base = {"douyin": spec.get("douyin_queries") or [], "xhs": spec.get("xhs_queries") or [],
            "bili": spec.get("bili_queries") or [],
            "yt": [(x[0] if isinstance(x, (list, tuple)) else x) for x in (spec.get("yt_queries") or [])]}
    heal = spec.get("autoheal_added") or {}
    rows, total = [], 0
    for label, key in groups:
        adds = heal.get(key) or [] if key != "yt" else []
        allq = list(base.get(key, [])) + [a for a in adds if a not in base.get(key, [])]
        if not allq:
            continue
        total += len(allq)
        hset = set(adds)
        chips = "".join('<span class="qchip">' + e(w) + ('<span class="qheal">自动补</span>' if w in hset else '') + '</span>' for w in allq)
        rows.append(f'<div class="qgroup"><b>{e(label)} ({len(allq)})</b> {chips}</div>')
    inner = "".join(rows) or '<div class="qgroup">(本批无查询词记录)</div>'
    return (f'<div class="qwrap"><button class="qbtn">🔎 本批搜索词 ({total})</button>'
            f'<div class="qpanel">{inner}</div></div>')

doc = ('<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + e("素材关联度筛选 · " + _topic_disp) + '</title><style>' + CSS + '</style></head><body>'
       f'<header><div class="hleft"><h1>素材关联度语义筛选 · {e(_topic_disp)}</h1><div class="note">共{len(reps)}条 · 🟢留{len(keep)}/🟡审{len(review)}/⚪弃{len(drop)} · {e(psum)}</div>'
       + (f'<div class="note" style="color:#b45309">⚠ 欠匹配(&lt;2条,建议补搜):{e("、".join(UNDERMATCHED))}</div>' if UNDERMATCHED else '')
       + (f'<div class="note" style="color:#3730a3">🎞 中性垫片池:{len(filler_use)} 条可用(增量·不影响主题池)</div>' if filler_reps else '')
       + '</div>'
       '<div class="hright">' + _qpanel() + '<button id="showSelected" class="dlbtn2">展示已选（0）</button>'
       '<button id="dlSel" class="dlbtn" disabled>⬇ 下载选中 (0)</button>'
       '<button id="clnSel" class="dlbtn" disabled>🧹 批量清洗选中 (0)</button>'
       f'<span id="dllog" class="dllog">{_dllog_init}</span></div></header>'
       f'<main><section id="selectedWorkspace" class="selected-workspace" hidden><h2>✓ 已选素材 <span id="selectedTotal">0</span></h2><div id="selectedEmpty" class="selected-empty">当前没有已选素材</div><div id="selectedGrid" class="grid"></div></section>'
       f'<div id="allWorkspace"><h2>🟢 KEEP {len(keep)} {zsel("keep")}</h2><div class="grid" data-zone="keep">{_zk}</div>'
       f'<details open><summary>🟡 REVIEW {len(review)} {zsel("review")}</summary><div class="grid" data-zone="review">{_zr}</div></details>'
       f'<details><summary>⚪ DROP {len(drop)}(可展开查误杀) {zsel("drop")}</summary><div class="grid" data-zone="drop">{_zd}</div></details>'
       + _filler_html + '</div></main>'
       '<script>' + JS + '</script></body></html>')
open(os.path.join(RES, "filtered.html"), "w", encoding="utf-8").write(doc)
print(f"原{len(cands)} → ID去重 -{id_dups} → 封面pHash去重 -{ph_dups} → 剩 {len(reps)}")
print(f"封面本地化 {dl_ok}/{len(reps)} 张(其余下载失败/无封面)")
print(f"三色: 🟢{allcnt['keep']} / 🟡{allcnt['review']} / ⚪{allcnt['drop']}")
print("各平台:", {p: f"{platcnt[p]['keep']}/{platcnt[p]['review']}/{platcnt[p]['drop']}" for p in platcnt})
if filler_reps:
    print(f"🎞 中性垫片池:{len(filler_use)} 可用 / {len(filler_drop)} 弃(共 {len(filler_reps)} 去重后,按时长升序" + (f";欠匹配稿 {len(FILLER_MISSING)}" if FILLER_MISSING else "") + ")")
