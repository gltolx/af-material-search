# 验证码应对手册(af-material-search)

> **铁律(不可逾越):Claude 绝不自动识别答案/绕过/通过任何验证码或 bot-detection,即使用户授权。** Claude 只做:**检测撞码 → 截屏存档 → 识别类型/读问题(仅用于路由与提示)→ 暂停 → 交用户秒解 → 用户解完后无损续跑**;以及**降低验证码触发频率**(避免=治本)。
>
> **唯二合规打断点(只有这两类才停下喊用户,其余一律自愈/记警告继续)**:① **验证码**(本手册全篇,Claude 永不代解);② **登录态缺失**(B站/YT 缺 Chrome cookie → 高清需登录,按 SKILL.md 第 0 步停下提醒用户去 Chrome 登录,**不自动降级**)。两者都用 `AskUserQuestion` 打断,各自唯一。其它(缺工具/坏依赖/收割量不足/单条下载失败)都不停。
> 为何这条线对用户也有利:自动过码会把采集小号坐实成机器人 → 更狠封号/风控(适得其反),并踩平台 ToS/反作弊与第三方打码灰产风险。安全姿势 = **避免为主 + 撞码即人工秒接,Claude 永不代解**。

## 一、已知验证码类型(实测记录,2026-06)
| 平台 | 触发点 | 类型 | 文案/特征 | 判别 signature(检测用) | 备注 |
|---|---|---|---|---|---|
| 小红书 CLI(`xhs`) | API 请求(Bash 韩国出口 IP) | 服务端码墙(无图) | stderr `Captcha triggered (count=1), cooling down 5s` | JSON `ok:false` + stderr 含 `Captcha` | 持续,退避无效 → 弃 CLI,改浏览器(国内节点) |
| 小红书 浏览器 | 搜索结果页 | 选图(选2张) | "安全验证 / 请选择最符合描述的两张图片 / 橙色的蔬果" | 页面含「安全验证」节点 / 选图容器 | 偶发,有时自动过期清掉 |
| 抖音 浏览器 | 连续快速 navigate 搜索 | 点选同形状(图) | tab 标题「验证码中间页」/「点击两个形状相同的物体」 | `document.title.includes('验证码')` | 节奏过快必触发;≥8s/词可避 |
| B站 API / YouTube | — | 无 | KR 直连干净 | — | 未撞码(开放接口/无登录) |

## 二、检测层(撞码第一时间确定性识别,别把空结果误当"没素材")
撞码 ≠ 没结果 ≠ 未登录墙。每次 navigate/scrape 后跑 `detectCaptcha()`(浏览器用 `javascript_tool` 探 DOM;CLI 用 stderr 正则)→ 返回 `{blocked, platform, type, prompt, tabId}`,只有 `blocked` 才进交接。
- **抖音**:`document.title.includes('验证码') || /点击.*形状|拖动|滑块/.test(document.body.innerText)`;且 `document.querySelectorAll('.search-result-card,a[href*="/video/"]').length===0`(有卡片=没撞)。
- **小红书**:页面含「安全验证」节点 / 选图弹窗,且 `a[href*="/explore/"]`.length===0。注意区分"没找到相关内容"(真空结果)——后者有空态文案、无验证弹窗。
- **小红书 CLI**:`json.ok===false` 或 stderr 含 `Captcha`。
- **B站/YT**:目前无;保留 detectCaptcha 钩子以防改道。
检测到 `blocked` → **立即 `computer` 截屏存档**到 `BROLL_RES/captcha_shots/{platform}_{type}_{kw}_{epoch}.png` + 追加 `manifest.jsonl`(给人核验,不是给机器解)。
> **⚠️ 必做·立即在本机弹真窗叫人(2026-06-16 用户硬要求,实测可用)**:检测到验证码的**那一刻**就跑下面这条 Bash,在 Mac 上弹实体窗 + 响铃。**只在对话里打字"请去过码"=用户看不到=等于没实现**。
> ```bash
> osascript -e 'display notification "检测到验证码拦截,请到 Chrome 处理" with title "⚠ broll 验证码拦截" sound name "Glass"'
> osascript -e 'display dialog "检测到 抖音/小红书 验证码拦截。\n请切到 Chrome 当前标签页手动过码,完成后回来告诉我继续。" with title "⚠ broll 验证码拦截 — 需你手动过码" buttons {"知道了"} default button 1 giving up after 30 with icon caution'
> ```
> `giving up after 30` 让窗 30s 自动消失,不卡住 Bash;按钮返回值可忽略。弹完再走 `AskUserQuestion` 等用户。**登录态缺失打断点(B站/YT 缺 cookie)同理弹窗。**
**三态判别(防把撞码/空态误落 0):** `ok=true && items/cards=[]` = 真没结果(不退避);`ok=false` 或撞码文案命中 = 撞码(暂停+喊人);其余 0 结果 = 软失败(下轮重试)。
> ⚠️ 上面 DOM 选择器是**宽松匹配**(基于实测文案 + 通用风控特征;仓库暂无真实撞码 HTML 现场)。**下次真撞码时顺手存一张截图 + 当时的码弹窗 DOM 节点,据真实结构把选择器收窄一次**,会更准。

## 三、避免=治本(降低触发频率 · 会诊B)
| 平台 | 走法 | 词间隔 | 单轮词数 | 要点 |
|---|---|---|---|---|
| 抖音 | 浏览器 DOM-scrape | **≥10s + 0~5s 抖动** | ~6 | navigate(非词数)才是触发器;一屏即收;排流程末尾。实测 5s 必码、8s 安全。 |
| 小红书 | **浏览器·国内节点**(CLI 韩国 IP 必码,放慢救不了) | ≥6s + 0~3s | 8~12 | scroll 到底 ×3 即止 |
| B站/YT | Bash·开放接口/yt-dlp·无登录 | ≥3s | 基本不限 | 走量主力,不撞码 |
- 专用小号扫码后**保活 cookie**,别频繁重登;量尽量压到"无登录开放接口"路线。
- DOM-scrape(读已渲染页)> 主动翻页/连发 XHR;**别短时间大量 navigate、别并发开多标签猛搜**。

## 四、人工接管闭环(Claude 只呈现+续跑,绝不代解 · 会诊C)
1. **检测** → blocked 才进。
2. **一条消息给全**:截图 + 平台 + 码类型 + 原文问题 + "请在 **tabId X** 手动解,解完回我一声",紧跟 `AskUserQuestion`(✅解完了 / 跳过此词 / 换小号重扫)。
3. **自动确认已清**:用户回"解完"后轮询 `detectCaptcha()`(每 ~3s,≤6 次)直到 `blocked=false` 或出结果卡片才续;**不连环追打扰,不"轮询到验证消失"变相暴力重试**。
4. **撞码即跳、不阻塞(平台隔离)**:单个词撞码 **立即跳到别的平台/别的词继续跑**,撞码词记 `pending_captcha[]`;**B站/YT 后台轨完全不受影响,继续跑**(它们走开放接口/yt-dlp,不撞码)。
5. **攒批触发条件(同平台连续 ≥2 词)**:同一平台**连续 ≥2 个词撞码**才立即发一条消息列清单(各带平台 + tabId + 码图)让用户**一次性解**——**不等整轮跑完**(连撞 2 词说明该平台进了码墙,继续只会徒增 pending)。单个词偶发撞码不打断、不立即喊人,先跑别的。
5. **计时**:人工解码时段记 `timings.jsonl` 区间,`report_timing.py` 从挂钟扣除(公平计时)。

## 五、断点续跑(撞码暂停绝不丢进度 · 会诊D,零 Python 改动)
1. **增量落盘**:把 snippet 末尾"一次性 POST"**挪进每词循环内**——每词 merge 进 localStorage 后立刻 POST `dy_raw.json`/`xhs_raw.json`(localStorage 存累计全量,每词=全量快照覆盖写),撞码**最多丢当前一词**。
2. **进度清单**:每词再 POST `_progress.json` = `{平台:{done:[词...],status:running|pending|complete}}`(BROLL_RES,续跑唯一事实源)。续跑读它从下一未完成词接着搜,**别把 localStorage 重置为空**;`merge_scored.py` 的 `canon()` 按 id 幂等去重,重叠词自动并掉。
3. **平台隔离**:后台 B站/YT 解耦照跑;前台一平台撞码先做另一平台,撞码平台标 `status:pending`;`merge_scored.py` 缺文件返 `[]` → 任一平台 pending 也能先出阶段性 scored.json,解码后重跑 merge 补全。

## 六、允许 vs 禁止(硬边界 · 会诊E,不因授权松动)
| ✅ 允许 | ⛔ 禁止 |
|---|---|
| 检测撞码 | 自动识别答案 |
| 截屏存档 | 坐标自动点选 |
| 识别码型/读题**做路由与提示** | 滑块自动拖动 |
| 暂停 + 提示用户 | 调第三方打码 API |
| 用户解完 → 断点续跑 | 伪造 bot-detection 信号 / 轮换设备指纹规避风控 |
| 降频 / 限速 / 会话复用 | 任何"替用户过验证"的行为 |
**界线一句话:读题/分类/路由/暂停/续跑可以,作答(出答案或出动作)绝不可以。** 安全姿势 = **避免为主 + 撞码即人工秒接,Claude 永不代解**;续跑信号须来自用户解完后的真实页面状态。

## 七、落地工具 + 运行步骤(已接,不用实时盯)
**工具(repo 根):**
- `detect_captcha.js` —— 每次 navigate/scrape 后用 `javascript_tool` 跑,返回 `{blocked,platform,type,prompt,cards,url}`。
- **`captcha_alert.py`(强提醒,首选)** —— **置顶模态弹窗(带声、不点不消、超时重弹·3 轮后放缓 90s)+ Preview 打开并置前显示验证码截图 + 一键 AppleScript 聚焦到【真】验证码标签页**。(⚠️ 图务必用 `open -a Preview <png>` + `tell application "Preview" to activate`;**别用 `qlmanage -p`**——从后台子进程拉起常不出窗,实测有弹窗无图。)三按钮 {稍后 / 去解·聚焦标签页 / 我已解决};点「我已解决」→ 写 `captcha_solved.flag`。**由收割主循环 run_in_background 起,不阻塞流水线。** 用法:`BROLL_RES=<dir> python3 captcha_alert.py <平台> <类型> <问题> <截图路径> <tab_url_子串>`。
- `captcha_notify.py`(轻量备选)—— 只发普通系统通知 + 入队,不弹模态。
- 截图存 `BROLL_RES/captcha_shots/`;待办队列 `captcha_queue.jsonl`;断点 `_progress.json`;解决信号瞬时 `captcha_solved.flag`(流转完即删)。
- **红线**:弹窗只"看图 + 一键到真页面",**绝不收答案/回填/relay 坐标**——relay 的点击无鼠标轨迹、瞬时几何整齐,比真人亲点更像机器人,会让号被风控标记得更狠。真页面亲解既合规又最安全。

**收割主循环每词照此跑(Claude 全程不解码;Claude 非常驻——靠每回合开头 check flag,不靠后台死等):**
0. **回合开头先 check `captcha_solved.flag`**:有 → 跑 `detect_captcha.js` 复核已清 → 删 flag → 从 `_progress.json` 该平台未完成词续跑。
1. navigate/scrape 某词 → 跑 `detect_captcha.js`。
2. `cards>0` 正常 → **立即增量落盘**(每词 POST 全量快照到 writer_server)+ 记 `_progress.json` done(撞码最多只丢当前词)。
3. `blocked` → `computer` 截屏存 `captcha_shots/` → **run_in_background 起 `captcha_alert.py`(强弹窗+图+一键到真页面)**→ 该平台标 `pending`,**跳过、继续别的平台/词**(不阻塞、不丢已收)。
4. 用户看到强弹窗 → 点「去解·聚焦标签页」一键到真页面 → **本人**点/输解掉(只有这一下是真人)→ 点「我已解决」(写 flag)或回 Claude 一声。
5. 下一回合走步骤 0 自动续;人工解码时段记 `timings.jsonl` 由 `report_timing.py` 扣除。

→ 效果:**多数轮次靠"避免"根本不撞码;偶尔撞了 = 强弹窗+图怼到你脸前 + 一键到真页面秒解 + 流水线自动续。你不必实时盯。** 唯一真人动作 = 真页面点那一下验证,Claude 不代、不 relay。
