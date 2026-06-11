#!/usr/bin/env python3
"""把浏览器重搜小红书拿到的新鲜 xsec_token 回填进 scored.json 的裸 explore 链接(按 note id join)。
前置:在登录 Chrome 用 browser_harvest_snippets.md 的小红书段重搜原 keyword 集 → fetch POST 到 writer_server →
      生成 BROLL_RES/xhs_raw.json(每条 {p: charCode编码的完整 explore URL(含token), type, cover, title})。
本脚本:解码 xhs_raw.json → 建 note_id→带token URL 映射 → 改写 scored.json 里缺 token 的小红书条目 page/url。
之后重跑 score_candidates.py + apply_verdicts.py 出页,小红书即可下载/悬浮预览。system python3 跑:
  BROLL_RES=<dir> python3 backfill_xhs_token.py
"""
import json, os, re
import xhs_imgmap   # 共享:dec + build_imgmap(与 merge_scored.py 同一份,别重写)

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
RAW = os.path.join(RES, "xhs_raw.json")
SCORED = os.path.join(RES, "scored.json")

raw = json.load(open(RAW, encoding="utf-8"))
tokmap = {}                                          # note id -> 带 token 的完整 explore URL
for x in raw:
    url = xhs_imgmap.dec(x.get("p", ""))
    m = re.search(r"/explore/([0-9a-fA-F]{12,})", url)
    if m and "xsec_token=" in url:
        tokmap[m.group(1)] = url
print(f"重搜带 token 的唯一 note id:{len(tokmap)}")

# 顺带产 id->图片URL 映射(图文笔记下载图片用;download_server 读 RES/xhs_imgs.json)
imgmap = xhs_imgmap.build_imgmap(raw)                 # 含 type:video/normal,download_server 据此决定下视频还是图片
json.dump(imgmap, open(os.path.join(RES, "xhs_imgs.json"), "w", encoding="utf-8"), ensure_ascii=False)
print(f"图文图片映射 xhs_imgs.json:{len(imgmap)} 个 note(含 type;图文走图片下载)")

scored = json.load(open(SCORED, encoding="utf-8"))
xhs = [c for c in scored if c.get("platform") == "小红书"]
filled = already = nomatch = 0
for c in scored:
    if c.get("platform") != "小红书":
        continue
    m = re.search(r"/explore/([0-9a-fA-F]{12,})", (c.get("page") or "") + " " + (c.get("url") or ""))
    if not m:
        continue
    nid = m.group(1)
    if nid in tokmap:                                  # 总是用最新 token 刷新(防过期:旧 token 会让 video 下载失败)
        c["page"] = tokmap[nid]; c["url"] = tokmap[nid]; filled += 1
    elif "xsec_token=" in (c.get("page") or ""):
        already += 1
    else:
        nomatch += 1

json.dump(scored, open(SCORED, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tot = len(xhs)
print(f"小红书 {tot} 条 → 原已带token {already} · 本次回填 {filled} · 仍无匹配 {nomatch}")
print(f"现在带 token:{already + filled}/{tot} ({round((already + filled) / max(tot, 1) * 100)}%)")
print("再跑:python3 score_candidates.py && python3 apply_verdicts.py 出页(小红书即可下载/预览)")
print(f"仍无匹配的 {nomatch} 条:那些 note 今天没在搜索结果里出现 → 补几个针对性 keyword 重搜再跑本脚本")
