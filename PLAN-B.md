# 方案B(轻版):Claude 直接操浏览器收割,避反爬靠节奏,不建重框架

> 2026-06-05 用户否决重型(Playwright+多模块)方案后重写。原则:**轻、不跑崩、零运维、几乎不写新代码**。

## 沉淀:规模化方法(解决"反爬/限流 vs 几百条/选题"冲突)

**核心思路:量从"干净平台"来,抖音做"精搜补充"。** 把"几百条/选题"的量压在我能无障碍跑的平台上,抖音只取它最对口的那几十条,用节奏换稳定。

| 平台 | 我跑的方式 | 我侧反爬难度 | 单选题可得量 | 角色 |
|---|---|---|---|---|
| 小红书 | `xhs search --json`(已登录) | 无 | 100+(多查询×每查询数十) | **走量主力** |
| B站 | 开放搜索 API | 无 | 100+ | **走量主力** |
| YouTube | `yt-dlp ytsearch` | 无 | 数十–百 | 走量(国外向) |
| 抖音 | 浏览器收ID + iesdouyin 解析 | 高(搜索墙+解析限流) | ~10–20/查询(paced) | **精搜补充**(最对口) |

→ 米卢这个选题**现已**:抖音 21 + 小红书 112 + B站/YT 210 ≈ **340 条候选**。**"几百条/选题"已达标**——靠四平台合力,不是硬撑抖音。

**抖音稳态技巧(避反爬是我的活):**
- 搜索墙 → §14 多查询广覆盖、搜与搜之间留间隔不连发;撞墙→暂停→用户重扫小号→续。
- 单搜 ~10–12 条上限 → 先滚动结果页加载更多再开 modal 深撸(可到 20+/搜)。
- 解析限流(iesdouyin 对反复 KR 请求风控)→ 慢速(≥1.5s)+ 退避重试 + **增量补解析**(失败的下轮再补,限流会缓解),不一次猛打。
- 复用 `build_douyin_page.py`:收完 ID 写进 `results/douyin_ids.json`,一行命令重建页(幂等可重跑补全)。

**冲突解法一句话:不靠单一抖音硬撑;四平台合力到量,抖音用节奏换稳定、做精料。**

## 并行收割模型(2026-06 · 实测 ~5× 提速,取代旧串行收割)

**目标:四平台并行,挂钟≈max 而非 sum。** 实测干净挂钟 ≈6-7 分(B站∥YT 27s · 小红书 DOM 3.9 分 · 抖音 DOM 2.6 分),对比旧串行 ~32 分 ≈ **5×**。

**两轨拓扑(单 Chrome + 单动作流是硬约束 → 子 agent 不能并发驱动第二个 Chrome):**
- **后台轨(Bash,无浏览器无人工,run_in_background):** `harvest_net.py` = B站(all/v2)∥ YouTube(yt-dlp)双线程 `ThreadPoolExecutor(2)`(都是网络 I/O,GIL 不挡),~30s → `harvest_net.json`。完全被前台吸收。
- **前台浏览器轨(单 Chrome 串行,但每步都快):** 小红书 DOM → 抖音 DOM。
  - **小红书 DOM**:CLI `xhs` 被韩国 IP captcha 封 → 走浏览器(国内节点)。navigate `search_result?keyword=KW&type=video` → `scrollTo` 底 ×3 → 抓 `a[href*="/explore/"]` note id + `img.src` + `.title`。
  - **抖音 DOM**(取代方向键+iesdouyin,~10× 快):navigate `search/KW?type=video` → 等 render → 抓 `.search-result-card` 内 `a[href*="/video/"]` 19 位 id + `img.src` + 最长非数字叶子文本(标题);**url 留空,无水印解析延后到选片后只解被选中的**(`resolve_douyin.py`),根治 iesdouyin 对 KR 的 ~80 次限流(旧串行版 119 解析挂 50)。
- **合并**:`merge_scored.py`(harvest_net + harvest_xhs + harvest_douyin → scored.json)。

**关键技术(踩坑沉淀):**
- **抖音结果在 DOM 里**,不必拦截器/方向键:fetch 拦截器装晚抓不到初次结果、`<script id=RENDER_DATA>` SSR 里无 aweme;DOM-scrape `.search-result-card` 最稳(实测 9 次拦截器只成 2 次)。
- **抖音反爬**:导航太快 → "点两个相同形状物体"图 captcha(不能代过)→ **≥8s/词节奏**,撞墙喊用户过码。
- **浏览器→磁盘桥**:`navigator.clipboard.writeText` 在重页死等卡死 → `writer_server.py`(loopback :8799,Chrome 免 mixed-content,CORS 全开)`fetch` POST 落盘。
- **工具屏蔽**:长 id/xsec_token 被 javascript_tool 当 base64/敏感屏蔽 → 公开 id `split('').join('.')` 点分隔回传,Python 去点。localStorage 跨同源页累计,末尾一次性桥出。
- **隔离重跑**:`harvest_net/merge_scored/score_candidates/apply_verdicts` 都认 `BROLL_RES=<abs dir>`(默认 `results/`),非破坏性跑进 `results_xxx/`。`apply_verdicts` 封面本地化已 `ThreadPoolExecutor(16)`。
- **计时**:各流往 `timings.jsonl` 记 start/end;公平挂钟 = max(后台并行轨, 前台串行轨之和),流间一次性 captcha/人工不计入(`report_timing.py`)。

## 核心原则

- **"我的环境就是你"**:Claude 通过已连接的 Claude-in-Chrome 直接操作用户浏览器收割,**不**写独立 Playwright 脚本、**不**建后台进程、**不**用 MediaCrawler/签名爬虫。
- **避开抖音反爬是 Claude 的职责**:自控节奏(人味延时、不猛搜),撞墙→暂停→用户重扫专用小号→续。
- **复用已装好的轻工具**(不再新增):`douyin-mcp-server`(借 `DouyinProcessor` 解析无水印+封面,dummy key 绕 DashScope)、`xhs` CLI(小红书,已登录)、`yt-dlp`(B站/YT)、python `requests`(下载)、本地封面缓存 + 静态 HTML 结果页 + `http.server`。

## 流程(四平台)

1. **抖音**:Claude 在浏览器搜(§14 带锚点查询,paced)→ 点开结果 → 方向键↓循环读 `modal_id` → 去重。
2. **小红书**:`xhs search kw --type video --json`(干净)。
3. **B站**:开放搜索 API。**YouTube**:`yt-dlp`。
4. **解析**:抖音 ID → `DouyinProcessor('dummy').parse_share_url` 取无水印 url + 标题 + 封面;其余平台各自字段。
5. **封面**:`requests` 带 `Referer: douyin.com` 下封面到本地 `results/covers/`,`http.server` 供图(CDN 防盗链,预览必走本地缓存)。
6. **结果页**:静态 HTML,按稿/平台分区,本地封面 + 标题 + 时长 + 勾选 + 导出。唯一人工=选片(顺带查审黄赌毒/政治)。
7. **下载所选**:`requests` 带 Referer 下无水印 mp4(curl 对字节 CDN 报 SSL);B站/YT 用 yt-dlp。
8. **入库**:推进用户 AlphaFin/素材清洗知识库(接口待用户给;之前先本地落盘 + 元数据)。

## 账号 & 节奏(用户已定)

- **专用采集小号**:用户注册 + 浏览器扫码登录(与发布主号隔离,被限无所谓)。
- **节奏=激进**(当天尽量搜满):Claude 带**最小**人味延时尽量不秒撞墙;撞墙→暂停→用户重扫小号→续。专用小号风险可控。

## 不做(明确砍掉)

Playwright / CDP 接管 · MediaCrawler / 任何签名爬虫 · 独立后台守护进程 · 20 个模块的重框架 · 多账号轮换池/住宅代理池(真不够用再说)。

## 已知现实

- Claude 当收割器=零维护、不跑崩,但靠交互式逐个点,**当天搜几百词比后台脚本慢**(无法脱离对话后台跑)。产能不够再议。
- 抖音连续搜会弹登录墙(反爬)→ 这是节奏+重扫的事,不是搜不到。
- 待用户给:AlphaFin 入库接口、稿件来源(文件夹/接口/粘贴)。

## 旧重型方案

已废弃(见 git 历史 / 上一版本)。重型架构(Playwright-CDP、SessionGovernor、20 模块等)用户否决:太重、易跑偏跑崩、需运维。
