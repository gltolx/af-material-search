---
name: broll-auto
description: 口播稿 → B-roll 素材 全自动一条龙(af-material-search)。当用户给一批口播稿/选题 + **入库账号(email)+ 知识库名称**,要"从素材搜索→筛选→批量清洗→自动加入知识库"无人值守跑完整流程时,用本技能。与 /broll 的区别:broll 出页后由人选片/手点清洗;broll-auto **全自动、不等人**(只在登录态/验证码/卡死/库名歧义这种真·不可避免处降级或干净退出)。
---

# /broll-auto — 搜割→筛选→清洗→入库 全自动一条龙

项目根:`{{BROLL_HOME}}`。**复用 /broll 的全部脚本/机器,不重写**;本技能只加"全自动编排 + 清洗+入库阶段"。

## 输入(用户提示词给)
① 一批口播稿 / 选题(或稿链接);② **入库账号 email**;③ **知识库名称**。缺 ②③ → 仍可跑完阶段一出 `filtered.html`,但阶段二无法入库,跑到出页后**报告缺账号/库名**(这是配置缺失,不是内容打断)。

## 全自动铁律(与 /broll 的关键差别,务必照做)
- **全程不停**。唯一"干净退出"(带具名理由 abort,不挂等):库名 0/多 匹配、node2 建清洗任务失败。其余一切(登录态、验证码、单条失败、卡死)→ **降级/跳过/超时续跑**,绝不 `AskUserQuestion` 等不在的用户。
- **登录态/验证码 覆盖 /broll 的"停下提醒"**:broll 是停下让用户去 Chrome 登录/过码;**broll-auto 改为**:时间盒重试 ≤1~2 次 → 仍不行则**跳过该平台 / 降级到无 cookie 可得清晰度**(海外 IP 480P)+ **记日志**(`degraded`/`skipped_platform`)+ 用其余平台素材续跑。这是**记了日志的降级**(不偷偷),一个平台撞墙不冻结整条 run。
- **内容审查门(两态,绝不 halt)**:选片时 AI 逐条审(标题+封面+必要抽帧)黄赌毒/政治/违法;**命中或存疑 → 不选入 + 记日志(`audit=exclude`)+ 继续**,只让干净素材进清洗+入库。把坏内容挡在库外靠"排除单条"即达成,绝不为它中止整批。
- **入库不可逆**(无删除接口)→ 宁可漏不可重:跨 run 去重(见阶段二)。

## 阶段一:搜割 + 筛选 + 自动选片(= /broll 步骤 0~6b,全自动)
**执行 `{{BROLL_HOME}}/skills/broll/SKILL.md` 的步骤 0~6b**(同步/preflight → 解析稿 → relevance_spec → 四平台并行收割 → `score_candidates.py` 预过滤 → 语义判分含 **R1 人物主体 + R1b `eye_contact` 盯镜头硬丢** → `apply_verdicts.py` 判决出页 → 下前预检 → 逐稿匹配自动选片 + xhs masterURL 实测两轮勾选),**带这些 full-auto 覆盖**:
- 选择集 = `script_matches.json` 自动选好的两轮结果(**不等人勾**)。
- 登录态/验证码按上面铁律降级,不停下问用户。
- **判分时多给 `eye_contact`**(正面+半身/全身+人脸清晰+≥2/3帧锁定盯镜头;抽不到帧不置 true)——`apply_verdicts.py` 会据此硬丢(理由"正面人像·眼神看镜头")。详见 `SCORING.md` R1b。
- **内容审查**:逐稿匹配/选片这一步,AI 顺带审每条(黄赌毒/政治),命中/存疑的**不选入**(记日志)。

**产"自动选中集"`results/autorun_selected.json`**:
1. `BROLL_RES=<RES> python3 {{BROLL_HOME}}/build_autorun_selected.py`(system python3;从 verdicts+script_matches 机械拼出 `[{platform,page,url,title,verdict,score,script_name,persona,audit}]`)。
2. **AI 审查剔除**:复核该文件,把审查命中/存疑的项**删行或 `audit` 改非 pass**(autorun_kb 只清洗+入库本文件里的项)。

## 阶段二:批量清洗 + 自动入库(`autorun_kb.py` 一把梭)
1. **起下载端点**(douyin venv python + MATCLEAN env + 端口自适应,**run_in_background**,确认 LISTEN;同 /broll 第7步命令):
   ```
   MATCLEAN_CLEAN_URL=https://tool.alphafin.world MATCLEAN_CLEAN_TOKEN=14769e815e70a4be89ea98846ec2bf46 \
     BROLL_RES=<RES> ~/.local/share/uv/tools/douyin-mcp-server/bin/python {{BROLL_HOME}}/download_server.py
   ```
   确认 LISTEN:`lsof -iTCP -sTCP:LISTEN -P 2>/dev/null | grep -q ":$(cat <RES>/.dlport)"`(没起别往下)。
2. **跑编排**(douyin venv python;它读 `.dlport` 找端口、自动 清洗→轮询→去重→入库→台账):
   ```
   MATCLEAN_CLEAN_URL=https://tool.alphafin.world BROLL_RES=<RES> \
     ~/.local/share/uv/tools/douyin-mcp-server/bin/python {{BROLL_HOME}}/autorun_kb.py \
     --account <入库email> --kb "<知识库名>" --res <RES>
   ```
   `autorun_kb.py` 自动做:**①库名校验 fail-fast**(0/多同名→具名 abort,不猜库)→ **②跨run去重**(读 `_autorun_manifest.jsonl` 的 `(kb_id,stable_id)` 已 uploaded 跳过)→ **③ POST /clean 触发清洗**(边下边清,首行 error→abort)→ **④轮询 node2 到 finished**(无进展看门狗 25min + 整 job 硬超时;卡住条记 orphan 不入库、已 done 继续)→ **⑤ kb-upload**(只传 done 的 **key**)→ **⑥轮询 kb_status 到 uploaded/failed**(独立第二道硬超时)→ **⑦写 `_autorun_manifest.jsonl` + 打印汇总 + 进度页 URL**。
3. **盯全程**:run_in_background 跑 autorun_kb,持续 tail 它的 stdout(`[autorun] ...` 进度行);末尾会打印进度页 `https://tool.alphafin.world/?job=<job_id>`,可开浏览器看每条 待清洗→清洗中→完成。

## 用哪个 Python(铁律)
- `autorun_kb.py` / `download_server.py` → **douyin venv python**(`~/.local/share/uv/tools/douyin-mcp-server/bin/python`,有 requests)。
- `build_autorun_selected.py` / `apply_verdicts.py` / `score_candidates.py` / `merge_scored.py` → **system python3**(PIL/pHash)。

## 安全闸 / 防挂(全程不停的保障)
| 闸 | 行为(无人值守) |
|---|---|
| 库名 0/多 同名 | 立即 abort + 具名理由(不猜库) |
| node2 建任务失败/401 | 整批 abort(无 job 产生) |
| 清洗卡死(processing 无服务端 reaper) | 无进展看门狗 25min + 整 job 硬超时;**仅卡住条记 orphan 不入库,已 done 继续** |
| 入库卡死(kb_status 无 reaper) | 独立第二道硬超时;未终态记 `timeout` 续报 |
| 跨 run 重复入库(不可逆+后端不去重) | 本地台账 `(kb_id,stable_id)` 去重(caveat:仅防本机历史重复) |
| 内容审查命中/存疑 | 排除该条 + 记台账 + 继续 |
| 登录态/验证码 | 时间盒重试→跳过平台/降级清晰度 + 记日志 + 续跑 |
| 单条下载/清洗/入库失败 | 逐条 try,不停整批,末尾统一报 |

## 数据契约(新增)
- `results/autorun_selected.json`:阶段一产的自动选中集(审查后只留 pass);`[{platform,page,url,title,verdict,score,script_name,persona,audit}]`。
- `results/_autorun_manifest.jsonl`:阶段二台账 + 跨run去重依据;每行 `{ts,kb_id,platform,script_name,stable_id,key,clean_status,kb_status,audit,title,err}`。

## 详细参考
`/broll` SKILL(阶段一全流程)· `SCORING.md`(R1/R1b/三色)· `PLAN-B.md` · `ARCHITECTURE.md` · `download_server.py`(/clean)· `autorun_kb.py`(阶段二编排)· `build_autorun_selected.py`。
