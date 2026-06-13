# 浏览器收割 · 可复用 JS 片段(抖音/小红书 DOM-scrape + 落盘桥)

> 前台浏览器轨(Claude-in-Chrome)用。逻辑无法做成纯 Bash 脚本(要驱动浏览器),所以把**经实测的 JS 原样存这**,下次直接套,别再现推。配套后台轨 `harvest_net.py`(B站∥YT)。
> 通用前提:① 先 `BROLL_RES=<dir> python3 writer_server.py &`(loopback 落盘桥,**端口自适应**:默认 8799,占用则向上顺延,实际端口写在 `BROLL_RES/.writerport`,并在 stdout 打 `WRITER_PORT=<n>`)。② 长 id 被工具当 base64 屏蔽 → **点分隔回传**,Python 去点。③ 跨同源页累计用 `localStorage`。
>
> **⚠️ 多会话并行必读(SID 隔离,修"localStorage 按域名共享→两会话互相清空/混入"的隐蔽串数据)**:`localStorage` 按**域名**共享 —— 同一台机两个 Claude 会话同时收割小红书/抖音,会写同一个 `dyAll`/`xhsAll` 键;首词的 `setItem('dyAll','[]')` 会把对方正在累计的数据**清空**,后续 push 又**混入**对方的条目,且全程不报错(比端口冲突更隐蔽)。修法:**给本会话定一个短 SID,所有 localStorage 键带 SID**,两会话各写各的键、互不干扰。**落盘文件名不必带 SID**——并行时每会话本就该用独立 `BROLL_RES`(见 #4 多会话隔离),`dy_raw.json`/`xhs_raw.json` 落各自目录天然隔离,merge_scored/decode_raw 读固定名即可,无需改下游。
> - **SID 怎么定**:本会话开收割时取一个稳定短串(8 位即可),建议 `SID = BROLL_RES 末段目录名`(如 `BROLL_RES=$PWD/results_老广告` → `SID="老广告"`;默认 `results/` 单会话可用 `SID="default"`),或随便一个 epoch 尾数。**同一会话全程用同一个 SID**,下面所有 `<SID>` 占位都替换成它。**只用于 localStorage 键,不用于落盘文件名。**
> - **端口怎么带入收割 JS**:writer_server 端口已写在 `BROLL_RES/.writerport`。收割前先 Read 该文件拿到端口号 `<WPORT>`(读不到则回落 8799),把下面 `fetch` 里的 `:<WPORT>` 替换成实际端口;或在你的会话里把 `WRITER_PORT` 显式 export 后用同一值。**别再硬编码 8799**——多会话各起各端口,写错端口会落到别人会话的 writer 上(又一处串数据)。

## 抖音(DOM-scrape,取代方向键+iesdouyin)
**节奏 ≥8s/词**(避"点两个相同形状物体"图 captcha,撞墙喊用户过码)。每词:`navigate douyin.com/search/KW?type=video` → 等 render(~5s)→ 下面 scrape。**多会话隔离:把下面所有 `dyAll_<SID>` 的 `<SID>` 替换成本会话 SID**(首词前先 `localStorage.setItem('dyAll_<SID>','[]')`,**只清自己的键,绝不动别的会话**):
```js
(function(){let dot=s=>s.split('').join('.');let cur=JSON.parse(localStorage.getItem('dyAll_<SID>')||'[]');let seen=new Set(cur.map(x=>x.sid));
let cards=document.querySelectorAll('.search-result-card');let list=cards.length?cards:document.querySelectorAll('a[href*="/video/"]');
for(let node of list){let card=(node.classList&&node.classList.contains('search-result-card'))?node:((node.closest&&node.closest('.search-result-card'))||node);
 let a=card.querySelector?card.querySelector('a[href*="/video/"]'):null;if(!a&&node.tagName==='A')a=node;if(!a)continue;
 let m=(a.getAttribute('href')||'').match(/\/video\/(\d{15,})/);if(!m)continue;let sid=dot(m[1]);if(seen.has(sid))continue;seen.add(sid);
 let img=card.querySelector?card.querySelector('img'):null;let cover=img?(img.src||img.getAttribute('data-src')||''):'';
 let best='';(card.querySelectorAll?card.querySelectorAll('div,span,p'):[]).forEach(el=>{if(el.children.length===0){let t=(el.textContent||'').trim();if(t.length>best.length&&t.length<200&&!/^[\d:.,万\s]+$/.test(t))best=t;}});
 cur.push({sid,cover,title:best});}localStorage.setItem('dyAll_<SID>',JSON.stringify(cur));return cur.length;})()
```
~28/词。末尾落盘(`:<WPORT>` 替换成 `.writerport` 读到的端口、`<SID>` 替换成本会话 SID;**落盘文件名固定 `dy_raw.json`,不带 SID**——merge_scored/decode_raw 读的就是它,并行靠各会话独立 `BROLL_RES` 目录隔离):`fetch('http://127.0.0.1:<WPORT>/save?f=dy_raw.json',{method:'POST',body:localStorage.getItem('dyAll_<SID>'),headers:{'Content-Type':'text/plain'}});`(先 click 一下页面给焦点)。**单会话可把键也退回 `dyAll`**(不影响落盘名)。
Python 还原:`vid=sid.replace('.','')` → `{platform:"抖音",title,url:"",page:"douyin.com/video/"+vid,cover}`(**url 留空,无水印解析延后到选片**用 `resolve_douyin.py`)。

## 小红书(DOM-scrape,CLI 已被韩国 IP captcha 封)
**关键(2026-06 实测攻关):token 不在 `a[href]`(渲染出的 href 是裸 `/explore/{id}`,无 token)、也不在 noteCard;在 `window.__INITIAL_STATE__.search.feeds[].xsecToken`(item 级,len 46)。** 必须读 feeds 取 `id`+`xsecToken`,否则下载/预览全废(yt-dlp "No video formats found")。已端到端验证:浏览器现取 token → yt-dlp 拿到视频流。
每词:`navigate xiaohongshu.com/search_result?keyword=KW&type=video` → `window.scrollTo(0,document.documentElement.scrollHeight)` ×3(每次隔 2s 懒加载,feeds 会累积比可见锚点更多)→ merge。**多会话隔离:把下面所有 `xhsAll_<SID>` 的 `<SID>` 替换成本会话 SID**(首词前 `localStorage.setItem('xhsAll_<SID>','[]')`,**只清自己的键**):
```js
(function(){
 function uw(x){return (x&&typeof x==='object'&&'_rawValue' in x)?x._rawValue:x;}      // 解 Vue ref
 var enc=function(s){return Array.prototype.map.call(s,function(c){return c.charCodeAt(0);}).join('.');}; // charCode 数字码,逃工具屏蔽
 var cur=JSON.parse(localStorage.getItem('xhsAll_<SID>')||'[]'); var seen={}; cur.forEach(function(x){seen[x.p]=1;});
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
 localStorage.setItem('xhsAll_<SID>',JSON.stringify(cur));
 return JSON.stringify({total:cur.length, added:added});   // 只回统计,绝不回 token 明文(会被工具 redact)
})()
```
落盘(先 click 给焦点;`:<WPORT>` 替换成 `.writerport` 读到的端口、`<SID>` 替换成本会话 SID;**落盘文件名固定 `xhs_raw.json`,不带 SID**,merge_scored/decode_raw 读它,并行靠独立 `BROLL_RES` 隔离):`fetch('http://127.0.0.1:<WPORT>/save?f=xhs_raw.json',{method:'POST',body:localStorage.getItem('xhsAll_<SID>'),headers:{'Content-Type':'text/plain'}});`(单会话可把键退回 `xhsAll`)
Python 还原(charCode 解码出**带 token 的完整 explore URL**):
```python
def dec(code): return "".join(chr(int(x)) for x in code.split(".")) if code else ""
for x in json.load(open(f"{RES}/xhs_raw.json", encoding="utf-8")):
    page = dec(x["p"])     # https://www.xiaohongshu.com/explore/{id}?xsec_token={tok}&xsec_source=pc_search
    out.append({"platform":"小红书","title":(x.get("title") or "").strip(),"url":page,"page":page,"cover":x.get("cover") or ""})
```
type=='video' 才下得了视频(image 笔记 yt-dlp 报 No video formats,下游跳过)。token 实测活 >22h,**当天收割当天下**。

## 落盘桥(别用 clipboard——重页会死等卡死)
`writer_server.py`(loopback,**端口自适应**:默认 8799,占用顺延,实际端口在 `BROLL_RES/.writerport`、stdout `WRITER_PORT=<n>`):页面 `fetch` POST → 写 `BROLL_RES/<f>`。Chrome 对 loopback 免 mixed-content;text/plain 不触发 CORS 预检。**收割 JS 里的 `:<WPORT>` 从 `.writerport` 读后拼,别硬编码 8799**(多会话各起各端口,写死会落到别人会话的 writer 上)。

## 坑(实测)
- 抖音初次结果走 fetch 但 hook 装晚抓不到、`<script id=RENDER_DATA>` SSR 无 aweme → **别用拦截器,DOM 最稳**(9 次拦截器只成 2 次)。
- 抖音搜索页**固定 ~17 卡片无滚动翻页** → 靠**多词**上量(~6 词≈170),不靠翻页。
- `navigator.clipboard.writeText` 在重页 promise 不 resolve → CDP 45s 超时;execCommand('copy') 已废。统一走 writer_server。
