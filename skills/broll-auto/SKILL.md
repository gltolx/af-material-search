---
name: broll-auto
description: 口播稿 → B-roll 素材 全自动一条龙(af-material-search)。当用户给一批口播稿/选题 + **入库账号(email)+ 知识库名称**,要"从素材搜索→筛选→批量清洗→自动加入知识库"无人值守跑完整流程时,用本技能。支持**按口播稿链接分库**:多个口播稿链接一起搜,但每个链接的素材入各自的知识库(每链接一套 账号+库 = 一个清洗任务)。与 /broll 的区别:broll 出页后由人选片/手点清洗;broll-auto **全自动、不等人**(只在登录态/验证码/卡死/库名歧义这种真·不可避免处降级或干净退出)。
---

# /broll-auto — 搜割→筛选→清洗→入库 全自动一条龙

项目根:`{{BROLL_HOME}}`。**复用 /broll 的全部脚本/机器,不重写**;本技能只加"全自动编排 + 清洗+入库阶段"。运行时适配继承 `/broll`:Claude Code 走 Claude-in-Chrome + `browser_harvest_snippets.md`,Codex 走 `codex_chrome.sh` + chrome-devtools MCP + `browser_harvest_codex.md`;两边产物 schema 一致。

## 输入(用户提示词给)
① 一批口播稿 / 选题(或稿链接);② **入库账号 email**;③ **知识库名称**。缺 ②③ → 仍可跑完阶段一出 `filtered.html`,但阶段二无法入库,跑到出页后**报告缺账号/库名**(这是配置缺失,不是内容打断)。

### 按口播稿链接分库(可选;多链接各入各库)
用户若想"**同一个口播稿链接下所有稿的素材进同一个知识库,不同链接进不同库**",会**按链接把三样东西捆在一起给**:每个**口播稿链接 URL + 该链接用的入库账号 email + 账号下的知识库名**(不写箭头,AI 自己识别配对)。例:
```
【链接1】https://docs.feishu.cn/xxxAAA   账号 a@x.com   知识库 量化策略-2026Q2
  <这个链接下的几条口播稿全文…>
【链接2】https://www.notion.so/yyyBBB    账号 b@x.com   知识库 宏观大类资产
  <这个链接下的口播稿…>
```
- **搜索仍是合并一趟**:把所有链接里的稿子合起来跑阶段一(收割/打分/筛选/逐稿匹配全程不分链接),分库只在最末端入库时发生。
- 解析稿时,AI 给每条稿一个 `script_id`,并产 `results/kb_routing.json`(见下);**单链接/不分库的老用法照旧**——不给链接捆绑、只给一个账号+库,走兜底单库路径,零改动。
- **决策(用户拍板 2026-06-17)**:某链接库名写错/重名/查无 → **只跳过该链接、其余照常入库**(autorun_kb 记日志+汇总,改对了重跑会补上);多链接的清洗任务**串行逐个起**(不并发)。

## 全自动铁律(与 /broll 的关键差别,务必照做)
- **全程不停**。唯一"干净退出"(带具名理由 abort,不挂等):库名 0/多 匹配、node2 建清洗任务失败。其余一切(登录态、验证码、单条失败、卡死)→ **降级/跳过/超时续跑**,绝不等待不在场用户。
- **登录态/验证码 覆盖 /broll 的"停下提醒"**:broll 是停下让用户去 Chrome 登录/过码;**broll-auto 改为**:时间盒重试 ≤1~2 次 → 仍不行则**跳过该平台 / 降级到无 cookie 可得清晰度**(海外 IP 480P)+ **记日志**(`degraded`/`skipped_platform`)+ 用其余平台素材续跑。这是**记了日志的降级**(不偷偷),一个平台撞墙不冻结整条 run。
- **爬取节奏随机抖动(反爬·用户拍板 2026-06-18)**:阶段一收割/API/实测/下载的**所有节奏一律随机化,绝不固定常数**——抖音每词 `random.uniform(8,12)` 秒;小红书/B站/YouTube 的 API 间隔·搜索词间·滚动懒加载·masterURL 实测·下载/重试节奏都在**文档规则值上 ±2s 随机浮动**;随机区间**下限 ≥ 文档地板**(别向下约等于——血泪:曾把 ≥8s 压成 6s 撞码)。**撞码不缩量**:过码后把该平台的词搜全。详见 /broll「节奏铁律」。
- **内容审查门(两态,绝不 halt)**:选片时 AI 逐条审(标题+封面+必要抽帧)黄赌毒/政治/违法;**命中或存疑 → 不选入 + 记日志(`audit=exclude`)+ 继续**,只让干净素材进清洗+入库。把坏内容挡在库外靠"排除单条"即达成,绝不为它中止整批。
- **入库不可逆**(无删除接口)→ 宁可漏不可重:跨 run 去重(见阶段二)。

## 阶段一:搜割 + 筛选 + 自动选片(= /broll 步骤 0~6b,全自动)
**执行 `{{BROLL_HOME}}/skills/broll/SKILL.md` 的步骤 0~6b**(同步/preflight → 解析稿 → relevance_spec → 四平台并行收割 → `score_candidates.py` 预过滤 → 语义判分含 **R1 人物主体 + R1b `eye_contact` 盯镜头硬丢** → `apply_verdicts.py` 判决出页 → 下前预检 → 逐稿匹配自动选片 + xhs masterURL 实测两轮勾选),**带这些 full-auto 覆盖**:
- 选择集 = `script_matches.json` 自动选好的两轮结果(**不等人勾**)。
- 登录态/验证码按上面铁律降级,不停下问用户。
- **判分时多给 `eye_contact`**(正面+半身/全身+人脸清晰+≥2/3帧锁定盯镜头;抽不到帧不置 true)——`apply_verdicts.py` 会据此硬丢(理由"正面人像·眼神看镜头")。详见 `SCORING.md` R1b。
- **内容审查**:逐稿匹配/选片这一步,AI 顺带审每条(黄赌毒/政治),命中/存疑的**不选入**(记日志)。
- **(用户要"干净中性空镜垫片"时)中性垫片池 filler**:在阶段一**额外跑 /broll「中性垫片池」节**——逐稿从稿句派生 `relevance_spec.json.filler.queries`(`enable:true`、只投抖音+小红书、可追溯到稿句、命中 hard_anchors∪core_subjects 的剔除)→ filler 单独一轮收割(小红书带 imageList)落 `harvest_{douyin,xhs}_filler.json` → 对 `prefilter.json.need_llm_filler` 按**标题+封面**判中性场景匹配度 → `scores_filler_part*.json` → 逐稿选 ≤`per_script_cap`(默认6)→ `filler_matches.json`(idx 与主题 `script_matches.json` 互斥;**某稿 0 条报缺不补、记日志、不 halt**)。filler 与主题**物理隔离**(主题七项零影响,已冒烟验证),**全自动照跑不停**;filler 单独收割轮=请求量净增 → 节奏抖动照「节奏铁律」,撞码降级不缩量。

**产"自动选中集"`results/autorun_selected.json`**:
0. **(仅分库时)产 `results/kb_routing.json`**:AI 把用户给的"链接+账号+库名"捆绑解析成
   `{"links":{"<链接URL>":{"account":"<email>","kb_name":"<库名>","script_ids":["s1","s2"]}}}`,
   `script_ids` 填该链接下各口播稿的 `script_id`(与 scripts.json/script_matches.json 顶层键一致)。**不分库则跳过本步。**
1. `BROLL_RES=<RES> python3 {{BROLL_HOME}}/build_autorun_selected.py`(system python3;从 verdicts+script_matches 机械拼出
   `[{platform,page,url,title,verdict,score,script_name,persona,pool,source_link,account,kb_name,audit}]`;有 kb_routing.json 则
   按 script_id 给每条盖 `source_link/account/kb_name`,缺则留空走兜底库)。**若有 `filler_matches.json`/`verdicts_filler.json`,自动并入 filler 选中项**(`pool=filler`+`from_script`,搭该稿**同一** source_link/账号/库 → 与该稿主题素材进同一个库;autorun_kb 零改动按 source_link 并组清洗入库)。
2. **AI 审查剔除**:复核该文件,把审查命中/存疑的项**删行或 `audit` 改非 pass**(autorun_kb 只清洗+入库本文件里的项)。

## 阶段二:批量清洗 + 自动入库(`autorun_kb.py` 一把梭)
1. **起下载端点**(douyin venv python + MATCLEAN env + 端口自适应,**run_in_background**,确认 LISTEN;同 /broll 第7步命令):
   ```
   MATCLEAN_CLEAN_URL=https://tool.alphafin.world MATCLEAN_CLEAN_TOKEN=14769e815e70a4be89ea98846ec2bf46 \
     BROLL_RES=<RES> ~/.local/share/uv/tools/douyin-mcp-server/bin/python {{BROLL_HOME}}/download_server.py
   ```
   确认 LISTEN:`lsof -iTCP -sTCP:LISTEN -P 2>/dev/null | grep -q ":$(cat <RES>/.dlport)"`(没起别往下)。
2. **跑编排**(douyin venv python;它读 `.dlport` 找端口、自动 分组→清洗→轮询→去重→入库→台账):
   ```
   MATCLEAN_CLEAN_URL=https://tool.alphafin.world BROLL_RES=<RES> \
     ~/.local/share/uv/tools/douyin-mcp-server/bin/python {{BROLL_HOME}}/autorun_kb.py --res <RES> \
     [--account <兜底email>] [--kb "<兜底库名>"]
   ```
   - **多链接分库**:per-link 的 账号/库 已在选中集里(kb_routing.json 盖章),`--account/--kb` 可省;只对没路由的项当兜底。
   - **单库老用法**:不产 kb_routing.json,照旧 `--account <email> --kb "<库名>"`,整批进一个库。
   `autorun_kb.py` 自动做:**①按 source_link 分组**(一链接=一套账号+库=一个清洗任务)→ **②逐链接校验账号+库名**(某链接 0/多/查无 → **只跳过该链接**记日志,其余照入;全部失败才整批 abort)→ **③每链接内跨run去重**(读 `_autorun_manifest.jsonl` 的 `(kb_id,stable_id)` 已 uploaded 跳过)→ **④ POST /clean 触发清洗**(边下边清,首行 error→该链接 abort)→ **⑤交织轮询·边洗边传**(每条清洗 done 即刻 kb-upload 入对应库,逐条不等全批;退出=node2 finished 且无 kb 在途,照抄 app.py:539)→ **⑥三道防线**(清洗 stall 看门狗 25min + kb stall 看门狗 12min + 整 job 硬超时;卡住条记非 uploaded 不入库、已入库/已 done 继续)→ **⑦逐链接串行跑完,写 `_autorun_manifest.jsonl` + 打印每链接汇总/进度页 + 总汇总 + 跳过链接清单**。
3. **盯全程**:run_in_background 跑 autorun_kb,持续 tail 它的 stdout(`[autorun] ...` 进度行);**每个链接一个**进度页 `https://tool.alphafin.world/?job=<job_id>`(末尾按链接列出),可开浏览器看每条 待清洗→清洗中→完成。

## 用哪个 Python(铁律)
- `autorun_kb.py` / `download_server.py` → **douyin venv python**(`~/.local/share/uv/tools/douyin-mcp-server/bin/python`,有 requests)。
- `build_autorun_selected.py` / `apply_verdicts.py` / `score_candidates.py` / `merge_scored.py` → **system python3**(PIL/pHash)。

## 安全闸 / 防挂(全程不停的保障)
| 闸 | 行为(无人值守) |
|---|---|
| 某链接 库名 0/多 同名/查无 | **只跳过该链接**(记日志+汇总),其余链接照入;**全部链接都失败**才整批 abort |
| node2 建任务失败/401 | 该链接 abort(无 job 产生),不拖累其余链接 |
| 清洗卡死(processing 无服务端 reaper) | 无进展看门狗 25min + 整 job 硬超时;**仅卡住条记 orphan 不入库,已 done 继续** |
| 入库卡死(kb_status 无 reaper) | 独立第二道硬超时;未终态记 `timeout` 续报 |
| 跨 run 重复入库(不可逆+后端不去重) | 本地台账 `(kb_id,stable_id)` 去重(caveat:仅防本机历史重复) |
| 内容审查命中/存疑 | 排除该条 + 记台账 + 继续 |
| 登录态/验证码 | 时间盒重试→跳过平台/降级清晰度 + 记日志 + 续跑 |
| 单条下载/清洗/入库失败 | 逐条 try,不停整批,末尾统一报 |
| filler 小红书图文 + node2 图片直传未通 | 图片清洗大概率失败 → **按单条失败容错**(记日志续跑,不 halt);node2 图片"非清洗直传"通道通后重跑补入库。filler 视频不受影响 |
| filler 某稿派生 0 条中性画面 | **报缺不补**(记日志,页面/汇报列出),不拿通用空镜硬凑、不 halt |

## 数据契约(新增)
- `results/kb_routing.json`(可选,分库时产):口播稿链接 → 知识库 路由表。`{"links":{"<链接URL>":{"account":"<email>","kb_name":"<库名>","script_ids":["s1",...]}}}`。**单一事实源**(库名只存这一处,改名只改一处);缺它 = 不分库走兜底 `--kb`。
- `results/autorun_selected.json`:阶段一产的自动选中集(审查后只留 pass);`[{platform,page,url,title,verdict,score,script_name,persona,pool,from_script,source_link,account,kb_name,audit}]`(`source_link/account/kb_name` 由 kb_routing 盖章,空=兜底库;`pool∈{theme,filler}`,filler 搭该稿同一库,`from_script`=中性垫片锚的稿句,可追溯/RAG 隔离)。
- `results/_autorun_manifest.jsonl`:阶段二台账 + 跨run去重依据;每行 `{ts,kb_id,kb_name,account,source_link,platform,script_name,stable_id,key,clean_status,kb_status,audit,title,err}`(`kb_name/account/source_link` 为分库留痕,只加不改;去重仍按 `(kb_id,stable_id)`)。

## 详细参考
`/broll` SKILL(阶段一全流程)· `SCORING.md`(R1/R1b/三色)· `PLAN-B.md` · `ARCHITECTURE.md` · `download_server.py`(/clean)· `autorun_kb.py`(阶段二编排)· `build_autorun_selected.py`。
