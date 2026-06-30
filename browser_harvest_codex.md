# Codex 收割轨 · 小红书/抖音(Chrome DevTools MCP 版)

> **谁用**:Codex 本机版客户端(没有 Claude-in-Chrome 工具时)。Claude Code 本机版走 `browser_harvest_snippets.md` 的 Claude-in-Chrome 轨;两条轨只在浏览器适配层不同,下游 raw schema / merge / score / apply 完全共用。
> **核心**:收割 JS 与 Claude Code 轨**同源**(就用 `browser_harvest_snippets.md` 里那几段),只把"数据怎么出页"换掉——Claude Code 轨经 writer_server,Codex 轨**直接由 `evaluate` 返回**(实测 chrome-devtools-mcp 的 evaluate 是 CDP 直通,46 位 xsec_token / 19 位 id 原样返回不被屏蔽)。所以 **Codex 轨不需要 writer_server**。

## 工具(chrome-devtools MCP,`--slim` 三件套)
- `navigate({url})` — 打开页面
- `evaluate({script})` — 在页内跑 JS,**直接返回结果字符串**(传 IIFE 表达式 `(function(){…})()` 即可)
- `take_screenshot` — 截屏(验证码检测/留证)

## 前置(每次开工)
1. **起采集 Chrome**:`bash {{BROLL_HOME}}/codex_chrome.sh`(带调试端口 9222 + 持久独立 profile;**首次**在弹出的窗口里登录 小红书/抖音/B站/YouTube 各一次)。MCP 已配 `--browserUrl http://127.0.0.1:9222` attach 它。健康检查是 `http://127.0.0.1:9222/json/version`;不要打开裸 http://127.0.0.1:9222/ 当用户页面。
2. **国内出口自检**(关键,别跳):`navigate` 到 `about:blank` → `evaluate`:
   ```js
   fetch('https://myip.ipip.net').then(r=>r.text())
   ```
   返回地域必须在**中国大陆**(不是韩国/港澳台)。不在 → 采集 Chrome 没走国内节点,改 `CODEX_HARVEST_PROXY` 指到落地大陆的代理再重起 `codex_chrome.sh`。(**别用 curl 验**——Bash 出口在韩国,只有 Chrome 内部 fetch 才反映采集链路的真实出口。)
3. writer_server **不用起**(Codex 轨直接 evaluate 返回)。

## 小红书收割(每词)
1. `navigate` → `https://www.xiaohongshu.com/search_result?keyword=KW&type=video`
2. **render 探活**(navigate 一返回 DOM 可能没齐):`evaluate` 轮询就绪信号,最多几秒:
   ```js
   (function(){try{return !!(window.__INITIAL_STATE__&&__INITIAL_STATE__.search&&(__INITIAL_STATE__.search.feeds&&(__INITIAL_STATE__.search.feeds._rawValue||__INITIAL_STATE__.search.feeds).length))}catch(e){return false}})()
   ```
   返回 false 就再等一发;就绪或超时再收割。
3. **懒加载**:`evaluate("window.scrollTo(0,document.documentElement.scrollHeight)")` 跑 3 次,**每次之间隔 ~2s**(分 3 次 evaluate 调用,别在一发里 busy-wait)。
4. **收割**:`evaluate` 跑 `browser_harvest_snippets.md` 小红书段那个 IIFE。先定一个稳定短串 `SID`(建议取 BROLL_RES 末段;默认 results 可用 `default`),把片段里的 `<SID>` 全部替换掉。**唯一改动是最后一行**——把
   ```js
   localStorage.setItem('xhsAll_<SID>',JSON.stringify(cur)); return JSON.stringify({total:cur.length, added:added});
   ```
   换成
   ```js
   localStorage.setItem('xhsAll_<SID>',JSON.stringify(cur)); return localStorage.getItem('xhsAll_<SID>');
   ```
   (charCode 编码、`feeds[].xsecToken`、imgs 那些**全部原样保留**——schema 和 Claude 轨完全一致,下游 `backfill_xhs_token.py`/`merge_scored.py` 零改动。)
5. **落盘**:把 evaluate 返回的整串(全量累计快照)**覆盖写** `BROLL_RES/xhs_raw.json`。因为 localStorage 跨词累计,每词覆盖写的都是"截至当前的全量",最后一次即完整。文件名固定不带 SID;并行隔离靠各会话独立 `BROLL_RES`。

## 抖音收割(每词)
同上,navigate `https://www.douyin.com/search/KW?type=video` → render 探活(轮询 `document.querySelectorAll('a[href*="/video/"]').length>0`)→ `evaluate` 跑 snippets 抖音段 IIFE,同一个 `SID` 替换 `<SID>`,最后一行换成 `return localStorage.getItem('dyAll_<SID>');` → 覆盖写 `BROLL_RES/dy_raw.json`。文件名固定不带 SID;并行隔离靠各会话独立 `BROLL_RES`。**节奏 ≥8s/词**(抖音对密集 navigate 最敏感,建议 ≥10s + 抖动)。

## 验证码(红线照旧:绝不自动解)
- **每词收割前先 `evaluate` 跑 `detect_captcha.js` 的检测段**,返回 `{blocked,platform,type,...}`。
- `blocked==true` → **停手**:`take_screenshot` 留证 → 提示用户「**去那个采集 Chrome 窗口手动过码**,过完告诉我继续」→ **等用户回话**(交互式 Codex 天然能停)→ 续跑时**先再 evaluate 一次 detect**,DOM 真没码了才收割;还 blocked 就再提示。
- evaluate **只读 DOM / 只截屏**,**绝不**跑任何点击、拖动、作答坐标。过码的唯一真人动作发生在那个真 Chrome 窗口里。

## 节奏(避码=治本,同 captcha_playbook)
- 抖音 **≥10s + 0~5s 抖动 / 词**,单轮 ~6 词;小红书 **≥6s + 0~3s 抖动 / 词**,单轮 8~12 词。词间隔在两发 navigate 之间用 `sleep` 拉开。
- 触发器是 navigate 密度(平台风控行为,与 agent 是谁无关)→ 配方照搬 Claude 轨。

## 收完之后(与 Claude 轨完全相同,共用脚本)
`python3 {{BROLL_HOME}}/merge_scored.py`(读 dy_raw/xhs_raw/harvest_net 三文件 → scored.json,canon 去重)→ 之后 score_candidates / 语义判分 / apply_verdicts / 选片 / 下载,全部与 Claude Code 轨**一字不差**(纯 Bash/Python,不碰浏览器)。
