# 方案(定稿):broll 三项增强

> 人像盯镜头硬过滤 / node2 过期清理核查 / **broll-auto 搜割→清洗→入库一条龙**
> 状态:**定稿(已纳入 3 位同事 review + 用户 2026-06-14 拍板)**,待用户过 spec 后进 writing-plans。
> 覆盖两 repo:`af-material-search`(broll/broll-auto、打分)与 `af-material-clean`(matclean 清洗+入库桥,`feat/kb-batch-upload`,kb 提交已在 main)。
> 地面真相 + 修正均经 4 个调研 agent + 3 个 review agent 实地核对,`file:line` 可复核。

## ✅ 用户拍板(2026-06-14·以此为准)
- **D1 = full-auto 全程不停**:搜割→清洗→入库一气呵成,**不设"入库前复核闸"**。但 5 道**安全闸照旧**(都是防不可逆伤害/防卡死,非复核):① 内容审查命中自动排除、**存疑则停下要人**;② 跨 run 去重;③ 库名 0/多 同名 → 停;④ 轮询硬超时;⑤ 登录态/验证码合规打断。大批量改为**记日志告警+继续**(不阻塞无人值守)。
- **D2 = 维持现 35 软降权,只加"盯镜头"硬过滤**:**不动** `person_penalty.dominant`(仍 35);只新增 `eye_contact` 硬丢——**正面 + 半身或全身 + 人脸清晰 + 眼睛盯镜头 → drop**。
- **D3 = node2 只确认现状,本期不加固**:3 天自清已在跑;3 个残留风险仅记录在案,出问题再议。
- D4 = 入库去重**必做**(不可逆 + 后端不去重);D5 = **不另存本地长期副本**。

## 0. 三项目标
1. **盯镜头硬过滤**:正面半身/全身人脸且眼睛盯镜头 → 直接丢弃(软降权力度不变)。
2. **核查 node2 过期自动清理是否已做**(答:已做)。
3. **新技能 `broll-auto`**:搜割→清洗→入库 真·一条龙;阶段一=broll,阶段二=两轮勾选后自动批量清洗→全部清洗完成→把清洗成功的素材自动入库。账号(email)+ 知识库名 用户提示词给。

## ⭐ 同事 review 抓到的硬伤(已修进定稿)
| 严重度 | 同事发现 | 定稿对策 |
|---|---|---|
| 🔴 BLOCKER | `kb-upload` 的 `items` 要传 **job.files 的 KEY(可读名 `build_name+.mp4`)**,不是清洗成片名 `out`;传 `out` → `files.get(out)` 全 None → 静默 0 入库(`app.py:583-593`) | 取 `GET /api/jobs/<job>` 的 `files` 里 `status=="done"` 的 **key** 传给 kb-upload |
| 🔴 | 轮询会挂死:`processing/queued` **无 reaper**(只 `loading` 有 TTL~1h,且可能仅 feature 分支);matclean 崩在 processing → job 永不终态 | broll-auto **自设轮询硬超时 + 无进展看门狗**,卡住即停、报 orphan、**不入库** |
| 🔴 | 入库**不可逆**(全仓无删除接口)+ node2 **无去重**(`app.py:588`);重跑=新 job_id=重复入库 | **跨 run 去重(D4)**:入库前查 `GET /api/kb/list` 跳过已存在 file_name + 本地 `_autorun_manifest.jsonl` 记 `(kb_id,stable_id)` |
| 🔴 | 去掉选片=去掉唯一内容审查闸(黄赌毒/政治,`SKILL.md:46`) | **强制内容审查门**:逐条 AI 审 → 命中排除+记台账 → **存疑停下要人**,非 pass 不入库 |
| 🟠 | `/clean` 的 `done` 只代表上传完;`kb-upload` 只 enqueue(返回 `{queued,count}`),真入库在 `_kb_worker` 异步跑 | 等清洗:轮询 node2 `status=="finished"`;入库后**再轮询** `files[key].kb_status` 到 `uploaded`/`failed` |
| 🟠 | resolve/kb-upload **按 kb_id 匹配,不抛 `AmbiguousKbName`**;名字→kb_id 消歧是客户端的活 | broll-auto 自己做 name→kb_id 精确匹配,**0 个或 >1 个同名 → 报错停**,绝不猜库 |
| 🟠 | dominant 35→55 **过狠**(双重惩罚误杀强匹配) | **用户拍板:维持 35**,只靠 (1b) 硬过滤,天然规避此问题 |
| 🟠 | eye_contact 用 0-4s 抽帧**系统性误判**(开头看镜头后切空镜的好素材被误杀) | 硬丢**双门控**:必须**真抽到帧** + **≥2/3 帧锁定**;`drop_eye_contact` 开关 + 首批跑 diff 自检 |
| ✅ 已实测 | 线上 `tool.alphafin.world` 的 `/api/kb/resolve`、`/api/jobs/<id>` **已部署存活**(curl 结构化 404) | phase-2 今天可落地 |
| ✅ 已核实 | `scores_part*.json` 是 AI 直接产、**无白名单重建** | 加 `eye_contact` **无** duration 那种"被白名单吞"风险 |

---

## Part 1 — 盯镜头硬过滤(软降权维持 35)

### 现状
- `person_primary∈{none,partial,dominant}` 由 AI 判分时看封面/抽帧判定(`SCORING.md:24-33`,`SKILL.md:32`);降权在 `apply_verdicts.py:42-49`:`dominant −35 / partial −12`,**软降权、封顶 0、永不硬丢**,力度由 `relevance_spec.json.person_penalty` 一处控。
- 全仓**无** eye_contact/盯镜头 标记或硬过滤。判定可复用 `need_frames`→`extract_early_frames.py`(抽开头 6s 的 0/2/4s 三帧)。

### 改动
**(1a) dominant 软降权:维持 35 不变(D2)。** `person_penalty.dominant` 不改;真正不可用的(正脸盯镜头)交 (1b) 硬过滤,避免软+硬双重惩罚误杀强匹配。(日后想加力度只改 `relevance_spec.json` 一处。)

**(1b) 新增"盯镜头"硬过滤(本次主改):**
- **新 AI 字段** `eye_contact`(布尔)。**判定 rubric(写进 SCORING.md/SKILL.md):**`eye_contact=true` ⟺ **真抽到帧** ∧ **正面** ∧ **半身或全身(人占画面主体)** ∧ **人脸清晰** ∧ **眼睛在 ≥2/3 帧锁定直视镜头**。across-frames 要求专治"开头 2-3s 看镜头然后切空镜"的好 B-roll 误杀;**抽不到帧则不置 true**(不拿单缩略图硬丢)。封面看不准 → `need_frames=true` 先抽帧再判。
- 这个标记本身已含"正面+半身/全身+人脸+盯镜头",故**硬丢直接按 `eye_contact` 触发,不再叠加 `dominant` 条件**(叠加只会漏掉 AI 把 person_primary 判成 partial 的边角,反而放过该滤的)。`person_primary` 仍独立做 −35 软降权。
- **新 drop 分支**,精确插在 `apply_verdicts.py` 的 `verdict()` 内 **`if not s:`(L40)之后、R1 penalty(L43)之前**(唯一既可达又不被 `need_cover` 早返回 L45 遮蔽的位置):
  ```python
  if s.get("eye_contact") and SPEC.get("drop_eye_contact", True):
      return "drop", "正面人像·眼神看镜头", s.get("score")
  ```
- **need_cover 契约变更(须显式知会)**:`need_cover` 项原本结构上只会 keep/review、永不 drop;此分支在 L45 之前 → 封面待定但被判 eye_contact 的也能被丢(**这是想要的**,但属行为变更)。
- **开关** `relevance_spec.json.drop_eye_contact`(默认 true,沿用 `max_duration_sec` gate 模式)。首批**跑 diff 自检**(apply_verdicts 跑两遍 diff 出它丢了哪些 keep),给用户看误杀率。

### 完整一致性改动清单
| 文件:行 | 改动 |
|---|---|
| `results/relevance_spec.json:77-80` | 加 `drop_eye_contact` 键(默认 true);`person_penalty.dominant` **维持 35 不改** |
| `apply_verdicts.py` L41(新)+ L42 注释 | 新 drop 分支(按 eye_contact)+ 注释 |
| `skills/broll/SKILL.md:32` | 步骤5指令加 `eye_contact` rubric + across-frames 注意;顺手修已 stale 的字段元组 |
| `SCORING.md:24-33, :43` | R1 段加 eye_contact 判定标准(含 0-4s 抽帧 caveat)+ schema 行 |
| `CLAUDE.md`(scores_part 契约行) | 加 `eye_contact` |
| `tests/test_verdict_r1r2.py` | 加 eye_contact drop 分支用例(现有 dominant=35 用例不变) |
| `extract_early_frames.py:2-3,94`(docstring) | 注明帧也用于判 eye_contact(脚本无需改码) |
| **无需改**:`merge_scored.py`/`score_candidates.py`/`harvest_*` — 它们动 `scored.json`/`candidates.json`,不碰 scores_part,eye_contact 不过白名单 |

---

## Part 2 — node2 素材过期自清:**已做**(本期不加固)

### 结论:已做
- matclean **进程内**清理(非 cron/systemd/MinIO lifecycle):每小时后台线程 `_cleanup_old()`(`app.py:225-239`)+ 启动(`:281`)+ 每次 `/api/upload`(`:296`);保留期 `MATCLEAN_RETENTION_DAYS` 默认 **3 天**(`config.py:204`);按 **`meta.json` 的 `created`** 判过期(`store.py:16-21`),过期 `shutil.rmtree(web_data/jobs/<id>)` 整删(`app.py:42-67`)。坏/缺 created 视为未过期,不误删。
- 存储:本地盘 `~/af-material-clean/web_data/jobs/<id>/{in,out}/`(原片+成片双份留 3 天),**不入 MinIO**。

### 残留风险(仅记录,D3 本期不动)
1. 清理**耦合 matclean 进程存活**:服务崩(共享 GPU、paddle 可能 OOM)→ 无独立兜底。
2. `processing/queued` 卡死 job 不回收(只 `loading` 有 TTL reaper)→ 泄漏盘 + 让 broll-auto 轮询挂死(故 Part 3 自带超时)。
3. in/+out/ 双份留满 3 天,稳态占盘翻倍。

### 加固选项(D3:本期不落地,留档)
- (i) systemd timer 兜底:**读 `meta.json` 的 `created`** 删超期(不用 `find -mtime`,目录 mtime 被 out/ 写入刷新会少删)。(ii) out/ done 即删对应 in/ 源片。(iii) `processing`-TTL reaper。
> 都动共享后端 matclean,须和清洗同事确认部署,故本期先只确认现状。

---

## Part 3 — broll-auto:搜割→清洗→入库 一条龙(新技能)

### 定位
- 新技能 `skills/broll-auto/SKILL.md`,**复用 broll 全部脚本/机器**,不重写。
- **输入**(用户提示词):一批口播稿/选题 + **入库账号 email** + **知识库名称**。

### 阶段一(= broll 步骤 0~6b;差别=不等人点选)
同 broll 全自动:同步/preflight → 解析稿 → spec → 四平台收割 → 预过滤 → 语义判分(**含 R1 + 新 eye_contact 硬丢**)→ 判决出页 → 预检 → 逐稿匹配**自动选片** → xhs masterURL 实测 + 补充勾选。
- **选择集 = `script_matches.json` 自动选好的两轮结果**,据此**直接构造 items**(`platform/page/url/title/verdict/score/script_name/persona`),无需点击。
- 仍出 `filtered.html` 留痕。
- **强制内容审查门**:逐条 AI 审(黄赌毒/政治),命中排除+记台账,**存疑停下要人**,非 pass 不进入库。登录态/验证码照旧停。

### 阶段二(清洗 + 入库)— 精确编排
1. **起下载端点**:douyin venv python + `MATCLEAN_CLEAN_URL/TOKEN` env + 端口自适应,run_in_background,确认 LISTEN(同 broll 第7步)。
2. **触发批量清洗**:**复用** download_server 的 `/clean`(保留其 start+逐条传+失败回报+inflight 兜底,别绕过直打 node2):POST `http://127.0.0.1:<dlport>/clean` `{"items":[...选中集...],"dir":<可写目录>}` → 读 NDJSON。**首行可能是 `{"event":"error"}`(node2 建任务失败/401,无 job_id)→ 整批失败,停下报**;正常则 `start` 事件给 **`job_id`(整批一个)**。边下边清,`max_workers=3`。注:即便"不落本地",过路件仍写 `dir/.clean_tmp/`,`dir` 须可写。
3. **等清洗完成(自带超时)**:轮询 `GET https://tool.alphafin.world/api/jobs/<job_id>`(或 `/stream` SSE;SSE 推**整 job 快照**、`kb_*` 嵌在 `files[key]`)直到 `status=="finished"`(所有 file 到 `done/failed/cancelled`)。**硬超时 + 无进展看门狗**:快照长时间不变且未全终态 → 判卡死,停轮询、报 orphan job_id、**不入库**。
4. **收集成功项**:取 `files` 中 `status=="done"` 的 **key**(= 可读名,不是 `out`!),用内嵌 `stable_id` 对账。
5. **解析账号→KB**:`GET /api/kb/resolve?email=<账号>` → `{kbs:[{kb_id,name,role}]}`;按用户给的库名**精确匹配** → kb_id;**0 个或 >1 个同名 → 报错停**(端点不替你消歧)。
6. **跨 run 去重(D4)**:`GET /api/kb/list` 取该库已存在 file_name + 本地 `_autorun_manifest.jsonl` 的 `(kb_id,stable_id)` → 已入库跳过。
7. **内容审查门**:对 done + 去重后的项逐条 AI 审,命中排除/存疑停;只留 pass。
8. **入库**:`POST /api/jobs/<job_id>/kb-upload {email,kb_id,items:[<done key,去重+审查后>]}`(**无需 clean token**,job_id 即凭证)。返回 `{queued,count}` 只是 enqueue。
9. **轮询入库结果**:再查 `files[key].kb_status` 直到 `uploaded`/`failed`,写 `_autorun_manifest.jsonl` 台账(每条 `平台/稿名/stable_id/clean_status/kb_status/key/audit/err`)。
10. **末尾统一报**:成功入库/清洗失败/入库失败/审查排除/去重跳过 清单。

### 自动化边界(D1)— **full-auto(用户拍板)**
- **默认 full-auto**:phase-1 → 自动清洗 → 等清洗完 → 内容审查 + 去重预检 → **直接 kb-upload**,全程不设复核闸。"一条龙带安全带"。
- **5 道安全闸照旧(防不可逆伤害/防卡死,非复核)**:① 内容审查命中自动排除+记台账、**存疑停下要人**;② 跨 run 去重;③ 库名消歧 0/多 则停;④ 轮询硬超时+看门狗;⑤ 登录态/验证码合规打断。
- **大批量**:超阈值(~30 条)→ **记日志告警 + 继续**(不阻塞无人值守;共享后端 `WORKER_N=4`,礼让同事靠串行单 job + 告警估时)。
- (日后要复核闸可加 `--gate` 选项切 review-then-ingest;本期默认 full-auto。)

### 不做(YAGNI)
- 不重写清洗/入库(全用 matclean 现成 HTTP API)。不引入新框架/守护进程。默认不另存本地长期副本(D5)。

---

## 决策点(已拍板)
| 编号 | 决策 | 定稿 |
|---|---|---|
| **D1** ✅ | broll-auto 自动化边界 | **full-auto 全程不停**;5 道安全闸照旧;大批量记日志告警+继续 |
| **D2** ✅ | 人像力度 | **维持 dominant 35**;只加 `eye_contact` 硬丢(正面+半身/全身+盯镜头,双门控:真抽到帧+≥2/3帧锁定);`drop_eye_contact` 默认开+首批 diff 自检 |
| **D3** ✅ | node2 加固 | **本期不加固**,仅确认现状+记录 3 风险 |
| **D4** ✅ | 入库去重 | 必做:KB file_name 预检 + 本地台账 `(kb_id,stable_id)` 跳过 |
| **D5** ✅ | 另存本地副本 | 否(clean→KB 即达成) |

## 实施顺序(交 writing-plans)
1. **Part 1**(小、独立):加 `eye_contact` 字段 + apply_verdicts drop 分支(维持 35)+ rubric/契约/测试 + 首批 diff 自检。
2. **Part 3**(核心):新建 broll-auto SKILL + 阶段二编排(/clean 触发→带超时轮询→去重→内容审查门→kb-upload→入库轮询→台账);阶段一直接复用 broll。
3. **Part 2**:本期仅确认现状(不动共享后端)。

## 待核实(writing-plans 阶段坐实,不阻塞设计)
- `GET /api/jobs/<job_id>` 精确 JSON 字段名(逐项 `status`/`out`/`stable_id`/`kb_status`)——实现期对端点实测一次。
- 线上是否已部署 `loading`/`processing` reaper(`da29d54` 是否上线)——无论如何 broll-auto 自带超时。
- af-streamlit 入库是否对重复 file_name 去重(决定 D4 兜松紧;当前按"不去重"设计最稳)。
- node2 清洗成片**不 honor** `name_prefix`(`app.py:377-381` 丢弃)——已确认;R4 前缀本期不指望成片带,台账靠 `stable_id`。

---

## 🔁 v-final 同事复审增量(全自动定稿·以下超越前文冲突处)
3 位同事以"无人值守 full-auto"为前提复审,以下为**最终对策**(冲突处以此为准):

**A. 消灭所有"等不在的人"的停点**
- **审查门改两态**:全文"存疑则停下要人"作废 → **存疑/命中(黄赌毒/政治/违法)= 排除该条 + 记台账(`audit=exclude`)+ 继续**,绝不 halt 整批(排除单条即达成"挡在库外",中止整批无收益)。唯一"AI 主观人审停"被消除。
- **broll-auto 覆盖 broll 的登录态/验证码停**:`captcha_playbook` 的"唯二打断点→AskUserQuestion"在 broll-auto 语境降级为"**时间盒重试 ≤1~2 次 → 仍不行则跳过该平台 / 降级到可得清晰度 + 记日志 + 用其余平台续跑**"。这是**记了日志的降级**(不违反"不偷偷降清",因为不偷偷)。有人盯的 broll 保持原"停下提醒"。
- **eye_contact 首批 diff 自检**:只记台账/汇报,**不等确认**,`drop_eye_contact` 默认 true 续跑。

**B. 防挂死(processing/kb_status 都无服务端 reaper → 客户端兜)**
- 所有出站请求带 `timeout=`。
- **清洗轮询**:无进展看门狗 `STALL=max(20min, 时长×4)` + 整 job 硬超时 `max(2h, 条数×单条预算)`;卡死时**仅卡住条记 orphan 不入库,已 done 条继续入库**。
- **kb_status 轮询(独立第二道超时)**:只轮 `kb-upload` 返回的 **`queued`** 列表;终态 `uploaded/failed`;硬超时 `max(30min, count×90s)`;超时记 `kb_status=timeout` 续报。
- **库名 resolve 前移到 run 最开头**(phase-1 之前 0 成本校验);0/>1 同名 → **立即 abort + 记台账**(`return/raise`,非 AskUserQuestion)。

**C. 建造期纠错(否则编译即错)**
- 🔴 **去重 `GET /api/kb/list` 列 KB 文件的路由不存在**(它是 node2→af-streamlit 的列"知识库"出站调用,非列文件)→ **删除该步**;跨 run 去重**仅靠本地 `_autorun_manifest.jsonl` 键 `(kb_id,stable_id)`**。caveat:只防本机历史重复;跨机/删本地台账仍可能重灌(入库不可逆)。
- 🔴 **`apply_verdicts.py` 用小写 `spec`**;草案 `SPEC.get(...)` 会 NameError → 仿 `MAXDUR` 在 `:12` 附近 hoist `DROP_EYE = spec.get("drop_eye_contact", True)`,`verdict()` 内用裸 `DROP_EYE`。
- **端口发现**:orchestrator 读 `RES/.dlport`(8788 可能被占),非硬编码。
- **key 从 `/api/jobs` 快照取勿客户端重构**(`_safe_name`/`_uniq_key` 会改名/加 `_2`),用每条内嵌 `stable_id` 对账。
- **`done`(download_server NDJSON)≠ `status=="finished"`(node2)**:job_id 来自 `start` 事件;入库前一律以 node2 `finished` 为准。
- **kb-upload 返回 `{queued,count}` 的 `count` 可 < 发送数**(被 dedup/非 done 跳过)→ 对账 `queued` vs 发送集。
- **manifest 每行加 `kb_id`**(D4 去重键)。

**D. 测试参数(end-to-end 真入库到测试库)**
- 测试账号 `2082202747@qq.com` → 库名「**测试知识库**」(kb_id `331f8c0b-a950-4bf1-895c-3a47cb9cdbd5`,唯一同名,消歧干净)。**绝不指向生产库**(入库不可逆无删除)。
- 测试批量 **3~5 条短片**(共享 GPU,`WORKER_N=4`,避 YT 长片);`MATCLEAN_WEB_FAKE=1` 可先无 VLM 干跑验编排。
- 通过判据:filtered.html 自动勾选正确 + eye_contact drop 可见(reason「正面人像·眼神看镜头」)+ 清洗 job 到 `finished` + `kb_status→uploaded` + 测试库可见新文件 + manifest 行数对 + 重跑去重不二次入库 + 全程无 hang(完成或具名 abort)。
