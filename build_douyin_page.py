#!/usr/bin/env python3
"""轻量复用脚本:把抖音 video_id 批量解析(无水印+封面+标题)、本地缓存封面、生成结果页。
用法(用 douyin-mcp-server 的 venv python 跑,它有 requests + 解析依赖):
  ~/.local/share/uv/tools/douyin-mcp-server/bin/python build_douyin_page.py
输入: results/douyin_ids.json  形如 {"查询词": ["video_id", ...], ...}
输出: results/douyin.html + results/covers/{id}.jpg
特点: 解析走 iesdouyin share 接口(和搜索墙无关),带节奏(默认1.5s)+ 失败重试(3次,退避),避免限流。
不碰浏览器、不碰反爬、不是 Playwright/后台守护——纯批处理已收割的 ID。
"""
import re, json, os, sys, time, html as H, requests
import douyin_mcp_server.server as S  # 仅借其移动端 HEADERS

ROOT = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(ROOT, "results")
COV = os.path.join(RES, "covers")
os.makedirs(COV, exist_ok=True)
HDR = dict(S.HEADERS)
SLEEP = float(os.environ.get("RESOLVE_SLEEP", "1.5"))

def resolve_once(vid):
    r = requests.get(f"https://www.iesdouyin.com/share/video/{vid}", headers=HDR, timeout=25)
    r.raise_for_status()
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", r.text, re.DOTALL)
    if not m:
        raise ValueError("no _ROUTER_DATA (可能被限流/改版)")
    ld = json.loads(m.group(1).strip())["loaderData"]
    key = "video_(id)/page" if "video_(id)/page" in ld else "note_(id)/page"
    data = ld[key]["videoInfoRes"]["item_list"][0]
    v = data.get("video", {})
    play = (v.get("play_addr", {}).get("url_list") or [None])[0]
    if play:
        play = play.replace("playwm", "play")
    cov = (v.get("cover", {}).get("url_list") or v.get("origin_cover", {}).get("url_list") or [None])[0]
    return {
        "id": vid,
        "title": (data.get("desc", "") or "").strip() or f"douyin_{vid}",
        "url": play or "",
        "cover": cov or "",
        "dur": round((v.get("duration", 0) or 0) / 1000),
    }

def resolve(vid, retries=3):
    for i in range(retries):
        try:
            return resolve_once(vid)
        except Exception as e:
            if i == retries - 1:
                return {"id": vid, "title": "(解析失败)", "url": "", "cover": "", "dur": 0, "err": str(e)[:60]}
            time.sleep(SLEEP * (i + 2))  # 退避

def cache_cover(it):
    if not it["cover"]:
        it["lc"] = ""; return
    p = os.path.join(COV, f"{it['id']}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 500:
        it["lc"] = f"covers/{it['id']}.jpg"; return
    try:
        cr = requests.get(it["cover"], headers={**HDR, "Referer": "https://www.douyin.com/"}, timeout=25)
        if cr.status_code == 200 and len(cr.content) > 500:
            open(p, "wb").write(cr.content); it["lc"] = f"covers/{it['id']}.jpg"
        else:
            it["lc"] = ""
    except Exception:
        it["lc"] = ""

def e(s): return H.escape(str(s), quote=True)
def fmt(d): return f"{d//60}:{d%60:02d}" if d else ""

def card(it):
    page = f"https://www.douyin.com/video/{it['id']}"
    thumb = f'<img class="im" loading="lazy" src="{e(it["lc"])}">' if it.get("lc") else '<div class="ph">无封面</div>'
    dur = f'<span class="dur">{fmt(it["dur"])}</span>' if it["dur"] else ''
    return ('<div class="card"><label class="chk"><input type="checkbox" class="sel" data-url="' + e(it["url"]) + '"></label>'
            '<a class="thumb" href="' + e(page) + '" target="_blank" rel="noopener">' + thumb + dur + '<span class="pi">▶ 抖音打开</span></a>'
            '<div class="meta"><a href="' + e(page) + '" target="_blank" rel="noopener">' + e(it["title"]) + '</a>'
            '<div class="sub">抖音 · ' + ('✅无水印就绪' if it["url"] else '⚠️待重试') + '</div></div></div>')

def main():
    groups = json.load(open(os.path.join(RES, "douyin_ids.json"), encoding="utf-8"))
    seen = set(); out = []
    for q, ids in groups.items():
        items = []
        for vid in ids:
            if vid in seen: continue
            seen.add(vid)
            it = resolve(vid); cache_cover(it); items.append(it)
            time.sleep(SLEEP)
        out.append((q, items))
    tot = sum(len(i) for _, i in out)
    okc = sum(1 for _, its in out for i in its if i.get("lc"))
    oku = sum(1 for _, its in out for i in its if i["url"])
    sec = ""
    for q, items in out:
        sec += f'<h2>🔍 {e(q)} · 抖音 · {len(items)} 条</h2><div class="grid">' + "".join(card(i) for i in items) + "</div>"
    CSS = "*{box-sizing:border-box}body{margin:0;font-family:-apple-system,'PingFang SC','Microsoft YaHei',Arial,sans-serif;background:#f6f7f9;color:#1a1a1a}header{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid #e5e7eb;padding:10px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;box-shadow:0 1px 4px rgba(0,0,0,.05)}header h1{font-size:15px;margin:0}.note{font-size:12px;color:#9ca3af}.sp{flex:1}button{cursor:pointer;border:1px solid #d1d5db;background:#fff;border-radius:8px;padding:6px 12px;font-size:13px}button.primary{background:#fe2c55;color:#fff;border-color:#fe2c55}.cnt{font-weight:700;color:#fe2c55}main{padding:16px;max-width:1480px;margin:0 auto}h2{font-size:15px;margin:18px 0 10px;border-left:4px solid #fe2c55;padding-left:8px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}.card{position:relative;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;display:flex;flex-direction:column}.card:hover{box-shadow:0 3px 12px rgba(0,0,0,.10)}.thumb{position:relative;display:block;width:100%;aspect-ratio:3/4;background:#000;overflow:hidden}.im{width:100%;height:100%;object-fit:cover;display:block}.ph{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#888;background:#111;font-size:13px}.dur{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.75);color:#fff;font-size:11px;padding:1px 5px;border-radius:3px}.pi{position:absolute;left:6px;bottom:6px;background:rgba(254,44,85,.9);color:#fff;font-size:11px;padding:2px 7px;border-radius:4px}.chk{position:absolute;right:8px;top:8px;z-index:6}.chk input{width:20px;height:20px;cursor:pointer}.meta{padding:8px 10px}.meta a{font-size:13px;color:#111;text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.35;min-height:2.7em}.meta a:hover{color:#fe2c55}.sub{font-size:11px;color:#9ca3af;margin-top:4px}#out{display:none;width:100%;height:120px;margin:10px 0;font-family:monospace;font-size:12px;padding:8px}"
    JS = "function upd(){document.getElementById('cnt').textContent=document.querySelectorAll('.sel:checked').length}document.addEventListener('change',e=>{if(e.target.classList&&e.target.classList.contains('sel'))upd()});function selAll(){document.querySelectorAll('.sel').forEach(c=>c.checked=true);upd()}function clr(){document.querySelectorAll('.sel').forEach(c=>c.checked=false);upd()}function expo(){var s=[...document.querySelectorAll('.sel:checked')].map(c=>c.dataset.url).filter(Boolean);var t=document.getElementById('out');t.style.display='block';t.value=s.join('\\n');t.focus();t.select();try{navigator.clipboard.writeText(s.join('\\n'))}catch(e){}}"
    doc = ('<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>抖音结果·米卢2002</title><style>' + CSS + '</style></head><body>'
           '<header><h1>抖音 · 米卢/2002世界杯</h1><span class="note">封面=本地缓存(必显示)·点封面在抖音看原片·勾选即下载就绪</span><span class="sp"></span>'
           f'<span>共 {tot} · 封面{okc} · 无水印{oku} · 已选 <span class="cnt" id="cnt">0</span></span>'
           '<button onclick="selAll()">全选</button><button onclick="clr()">清空</button><button class="primary" onclick="expo()">导出所选(无水印URL)</button></header>'
           '<main><textarea id="out" readonly></textarea>' + sec + '</main><script>' + JS + '</script></body></html>')
    open(os.path.join(RES, "douyin.html"), "w", encoding="utf-8").write(doc)
    print(f"douyin.html | 共{tot} 封面{okc} 无水印{oku}")

if __name__ == "__main__":
    main()
