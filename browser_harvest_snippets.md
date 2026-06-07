# 浏览器收割 · 可复用 JS 片段(抖音/小红书 DOM-scrape + 落盘桥)

> 前台浏览器轨(Claude-in-Chrome)用。逻辑无法做成纯 Bash 脚本(要驱动浏览器),所以把**经实测的 JS 原样存这**,下次直接套,别再现推。配套后台轨 `harvest_net.py`(B站∥YT)。
> 通用前提:① 先 `BROLL_RES=<dir> python3 writer_server.py &`(loopback :8799 落盘桥)。② 长 id 被工具当 base64 屏蔽 → **点分隔回传**,Python 去点。③ 跨同源页累计用 `localStorage`。

## 抖音(DOM-scrape,取代方向键+iesdouyin)
**节奏 ≥8s/词**(避"点两个相同形状物体"图 captcha,撞墙喊用户过码)。每词:`navigate douyin.com/search/KW?type=video` → 等 render(~5s)→ 下面 scrape(首词前先 `localStorage.setItem('dyAll','[]')`):
```js
(function(){let dot=s=>s.split('').join('.');let cur=JSON.parse(localStorage.getItem('dyAll')||'[]');let seen=new Set(cur.map(x=>x.sid));
let cards=document.querySelectorAll('.search-result-card');let list=cards.length?cards:document.querySelectorAll('a[href*="/video/"]');
for(let node of list){let card=(node.classList&&node.classList.contains('search-result-card'))?node:((node.closest&&node.closest('.search-result-card'))||node);
 let a=card.querySelector?card.querySelector('a[href*="/video/"]'):null;if(!a&&node.tagName==='A')a=node;if(!a)continue;
 let m=(a.getAttribute('href')||'').match(/\/video\/(\d{15,})/);if(!m)continue;let sid=dot(m[1]);if(seen.has(sid))continue;seen.add(sid);
 let img=card.querySelector?card.querySelector('img'):null;let cover=img?(img.src||img.getAttribute('data-src')||''):'';
 let best='';(card.querySelectorAll?card.querySelectorAll('div,span,p'):[]).forEach(el=>{if(el.children.length===0){let t=(el.textContent||'').trim();if(t.length>best.length&&t.length<200&&!/^[\d:.,万\s]+$/.test(t))best=t;}});
 cur.push({sid,cover,title:best});}localStorage.setItem('dyAll',JSON.stringify(cur));return cur.length;})()
```
~28/词。末尾落盘:`fetch('http://127.0.0.1:8799/save?f=dy_raw.json',{method:'POST',body:localStorage.getItem('dyAll'),headers:{'Content-Type':'text/plain'}});`(先 click 一下页面给焦点)。
Python 还原:`vid=sid.replace('.','')` → `{platform:"抖音",title,url:"",page:"douyin.com/video/"+vid,cover}`(**url 留空,无水印解析延后到选片**用 `resolve_douyin.py`)。

## 小红书(DOM-scrape,CLI 已被韩国 IP captcha 封)
**关键(2026-06 实测攻关):token 不在 `a[href]`(渲染出的 href 是裸 `/explore/{id}`,无 token)、也不在 noteCard;在 `window.__INITIAL_STATE__.search.feeds[].xsecToken`(item 级,len 46)。** 必须读 feeds 取 `id`+`xsecToken`,否则下载/预览全废(yt-dlp "No video formats found")。已端到端验证:浏览器现取 token → yt-dlp 拿到视频流。
每词:`navigate xiaohongshu.com/search_result?keyword=KW&type=video` → `window.scrollTo(0,document.documentElement.scrollHeight)` ×3(每次隔 2s 懒加载,feeds 会累积比可见锚点更多)→ merge(首词前 `localStorage.setItem('xhsAll','[]')`):
```js
(function(){
 function uw(x){return (x&&typeof x==='object'&&'_rawValue' in x)?x._rawValue:x;}      // 解 Vue ref
 var enc=function(s){return Array.prototype.map.call(s,function(c){return c.charCodeAt(0);}).join('.');}; // charCode 数字码,逃工具屏蔽
 var cur=JSON.parse(localStorage.getItem('xhsAll')||'[]'); var seen={}; cur.forEach(function(x){seen[x.p]=1;});
 var feeds=[]; try{feeds=uw(uw(window.__INITIAL_STATE__.search).feeds)||[];}catch(e){}
 var added=0;
 feeds.forEach(function(it){
  if(!it||it.modelType!=='note'||!it.id||!it.xsecToken)return;
  var nc=it.noteCard||{};
  var page='https://www.xiaohongshu.com/explore/'+it.id+'?xsec_token='+it.xsecToken+'&xsec_source=pc_search';
  var ep=enc(page); if(seen[ep])return; seen[ep]=1;                                     // 整条 URL(含 id+token)charCode 编码
  var cov=(nc.cover&&(nc.cover.urlDefault||nc.cover.urlPre||nc.cover.url))||'';
  var imgs=(nc.imageList||[]).map(function(im){var inf=im.infoList||[];var d=inf.filter(function(x){return x.imageScene==='WB_DFT';})[0]||inf[inf.length-1]||inf[0];return (d&&d.url)||im.urlDefault||im.url||'';}).filter(Boolean);  // 图文下载用:每图取 WB_DFT(原图)URL
  cur.push({p:ep, type:nc.type||'', cover:cov, title:(nc.displayTitle||'').trim(), imgs:imgs}); added++;
 });
 localStorage.setItem('xhsAll',JSON.stringify(cur));
 return JSON.stringify({total:cur.length, added:added});   // 只回统计,绝不回 token 明文(会被工具 redact)
})()
```
落盘(先 click 给焦点):`fetch('http://127.0.0.1:8799/save?f=xhs_raw.json',{method:'POST',body:localStorage.getItem('xhsAll'),headers:{'Content-Type':'text/plain'}});`
Python 还原(charCode 解码出**带 token 的完整 explore URL**):
```python
def dec(code): return "".join(chr(int(x)) for x in code.split(".")) if code else ""
for x in json.load(open(f"{RES}/xhs_raw.json", encoding="utf-8")):
    page = dec(x["p"])     # https://www.xiaohongshu.com/explore/{id}?xsec_token={tok}&xsec_source=pc_search
    out.append({"platform":"小红书","title":(x.get("title") or "").strip(),"url":page,"page":page,"cover":x.get("cover") or ""})
```
type=='video' 才下得了视频(image 笔记 yt-dlp 报 No video formats,下游跳过)。token 实测活 >22h,**当天收割当天下**。

## 落盘桥(别用 clipboard——重页会死等卡死)
`writer_server.py`(loopback :8799):页面 `fetch` POST → 写 `BROLL_RES/<f>`。Chrome 对 loopback 免 mixed-content;text/plain 不触发 CORS 预检。

## 坑(实测)
- 抖音初次结果走 fetch 但 hook 装晚抓不到、`<script id=RENDER_DATA>` SSR 无 aweme → **别用拦截器,DOM 最稳**(9 次拦截器只成 2 次)。
- 抖音搜索页**固定 ~17 卡片无滚动翻页** → 靠**多词**上量(~6 词≈170),不靠翻页。
- `navigator.clipboard.writeText` 在重页 promise 不 resolve → CDP 45s 超时;execCommand('copy') 已废。统一走 writer_server。
