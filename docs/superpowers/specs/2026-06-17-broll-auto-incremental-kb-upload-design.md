# broll-auto 边清洗边入库(每条清洗完成即入库)— 设计

日期:2026-06-17 · 状态:已评审(2 名同事 GO-with-changes,意见已并入)

## 1. 背景与问题

broll-auto 全自动流水线的「批量清洗 → 入库知识库」阶段由 `autorun_kb.py` 编排。当前 `_clean_round`(约 256–285 行)是**严格三段串行**:

1. `poll_clean` —— 等**整批**清洗到 node2 的 `status=="finished"`(所有文件 done/failed/cancelled);
2. `kb_upload` —— 一次性把所有 `done` 的 key 提交入库;
3. `poll_kb` —— 等全部 `uploaded`。

后果:批里**最慢的一条**清洗完之前,所有已洗好的素材都干等着,不入库。用户要求改为:**每当单条素材清洗完成,就立刻把它加入知识库**(逐条,不等全批)。

## 2. 目标 / 非目标

**目标**
- 单条素材清洗到 `done` 后,在下一轮轮询(≤~8s)内即被提交入库,不再等整批。
- 保持 broll-auto 铁律:**全程不停、绝不挂死、无人值守不冻结**。
- 返回值结构、`_autorun_manifest.jsonl` 台账契约、`main()` 重试轮次、跨 run 去重 —— **零改动**。
- **零后端(node2)改动**。

**非目标**
- 不改 `/broll` 手动流程(手动 filtered.html 的「批量清洗」+ 浏览器手点「加入知识库」不在范围)。
- 不引入 SSE / 新传输通道(见方案 C 否决)。
- 不优化"kb 失败后免重清洗"这类增益(维持现有"kb-failed → main 重试链路重投"语义)。

## 3. 关键后端事实(让改动几乎零风险)

读 `af-material-clean/src/matclean/web/app.py` 确认:

- **kb-upload 端点幂等去重**(`app.py:597`,`kb_upload`):整个入队循环在 `with _lock:`(628 行)内同步完成;逐 key 校验 `f.get("status")=="done"`(632,未 done 静默跳过、不报错),且 `kb_status ∈ {queued,uploading,uploaded}` 直接跳过(634)。→ 清洗途中**反复调用安全、不会重复入库**,且未 done 的 key 提交也安全(静默跳过)。
- 返回 `{queued}` **只含本次新入队的 key**,不含此前已 queued/uploading 的 → 客户端不能拿单次 `queued` 判全量终态,必须以 node2 快照的 `kb_status` 为准。
- **`status=="finished"` 不含 kb**(`app.py:87` `_recompute_finished`,93–95 行只看清洗终态)。真正的"全做完"判据见 SSE 收尾(`app.py:536–539`):`finished` **且** 无任何文件 `kb_status ∈ {queued,uploading}`。
- `_kb_worker`(`app.py:168`)上传线程与清洗线程**独立并行**,且**无服务端 reaper** —— 卡在 uploading 不会自愈,客户端轮询/看门狗是唯一防线。
- kb-upload 走 MinIO presign+PUT+register(网络/IO),清洗走 GPU/ffmpeg —— **不同资源,边洗边传争抢极低**;并发上限由 node2 `KB_UPLOAD_CONCURRENCY` worker 池兜住,客户端只管提交。

## 4. 方案(Approach A:交织轮询)

把 `_clean_round` 的三段合成**一个交织轮询循环**(吸收原 `poll_clean` / `poll_kb` 两个函数体),每 ~8s `GET /api/jobs/<job_id>`:

```
每 tick:
  body = GET /api/jobs/<job_id>
  files = body.files
  # (a) 边洗边传:新 done 且未 submitted 的 key → 立即 kb-upload
  new_done = [k for k,f in files if f.status=="done" and k not in submitted]
  if new_done and not kb_circuit_open:
      queued, err = kb_upload(job_id, account, kb_id, new_done)
      if err: kb_fail_streak += 1; (退避;连续≥N → kb_circuit_open=True,只继续洗)
      else:   submitted |= set(queued); kb_fail_streak = 0
  # (b) 成功退出判据 = 照抄 node2 app.py:539
  if body.status=="finished" and not any(f.kb_status in {"queued","uploading"} for f in files):
      return files                       # 全部清洗终态 且 无 kb 在途
  # (c) 两个独立看门狗(不合一)
  clean_sig = 指纹(未终态清洗文件的 status/elapsed)
  kb_sig    = 指纹(在途 kb 文件的 kb_status/kb_pct)
  若 clean_sig 连续 CLEAN_STALL_S 不变且仍有未终态清洗文件 → 判清洗卡死,停等
  若 kb_sig    连续 KB_STALL_S    不变且仍有在途 kb 文件   → 判入库卡死,停等
  # (d) 整 job 硬超时兜底
  若 now - t0 > HARD_S → 停等
  sleep(POLL_S)
```

### 4.1 为什么不用别的方案
- **方案 B(只在 poll_clean 里顺手 incremental upload,清洗后再 poll_kb)**:改动更小,但保留两段串行 watchdog/超时,代码两套逻辑,不如 A 收敛。
- **方案 C(SSE `/stream` 监听 done 事件)**:实时性最好,但引入断流/重连新失败面,且现有所有防挂逻辑都按轮询建,推翻不值 —— 否决。

## 5. 评审并入的硬约束(必须落实)

| # | 约束 | 来源 | 理由 |
|---|---|---|---|
| C1 | **退出判据照抄 `app.py:539`**:`finished 且 无 kb_status∈{queued,uploading}`,**不用裸 `finished`** | 同事1 | node2 的 finished 不含 kb;裸 finished 会早退漏入库(最后一条 done 还没来得及提交/上传) |
| C2 | **两个独立 stall 计时器**(clean_stall 盯未终态清洗 / kb_stall 盯在途 kb),**不合并成一个指纹** | 同事2 | 合一会被清洗进度不断 reset → kb 卡 uploading 永不触发看门狗 → 真挂死 |
| C3 | **kstat/cstat 终值严格取自 node2 快照**,不靠本地 `submitted` 推断 | 同事1 | `submitted` 只代表"已成功 POST 过";终态必须看 node2 真值 |
| C4 | **`submitted` 仅用于 kb-upload 调用去抖**;POST 失败的 key 不进 submitted(下 tick 自然重发);node2 标 `failed` 的不在 loop 内重投(交给 main 重试链路) | 同事1+2 | 既避免漏发,又维持"在途/失败不在本 loop 重复入库" |
| C5 | **kb-upload HTTP 连续失败 → 退避 + 熔断**(`kb_fail_streak ≥ N` 后停发,只继续洗;不在热路径反复 POST 拖死循环) | 同事2 | 防 node2 5xx/网络抖动把轮询热路径拖死 |
| C6 | **退出聚合守住"仅 `uploaded` 才算已入库"**:orphan→`not_done`(可重试)、done 但 kb 在途/超时→非 uploaded(`_retryable:349` **不重投**,防重复入库) | 同事1+2 | 保住跨 run 去重(`autorun_kb.py:106` 只认 `kb_status=="uploaded"`)与 main 重试轮次不破 |

> **看门狗分歧裁决**:同事1 倾向单指纹看门狗,同事2 明确反对。采纳同事2 —— 清洗进度会掩盖 kb 卡死,故用两个独立计时器(C2)。同事1 的退出判据(C1)是另一回事(判"是否做完"),两条并存不冲突。

## 6. 超时与防挂参数

- `POLL_S`:单一轮询间隔(~8s;原清洗 10s / kb 6s 折中,kb 尾段略慢于原 6s,无害)。
- `CLEAN_STALL_S` = 25min(沿用)、`KB_STALL_S` = 12min(沿用)。
- `HARD_S`:整 job 硬超时兜底 = `清洗预算 + kb 尾段预算`(沿用原量级,给足)。**定位是兜底**:真卡死由两个 stall 计时器提前抓(25/12min),HARD_S 只防"stall 没抓到的极端"。broll-auto 本就允许大批量长跑(技能明示"无人值守不阻塞"),故 HARD_S 保持宽松、不过度收紧。

## 7. 状态聚合(退出后,喂台账 + 重试)

循环返回最终 `files` 快照(及由它得到的 `key→kb_status` 映射),`_clean_round` 余下的 sid 反查/聚合逻辑(280–284 行)**保持不变**:

- `cstat` = `files[key].status` 或 `orphan`(无 key);
- `kstat` = `files[key].kb_status`(在途/超时则为 `uploading`/`None`,**非** `uploaded`);
- 台账 `kb_status` 字段:`uploaded` 才算成功;`not_done`(清洗没成)/在途超时值 → 下次不被跨 run 去重跳过;
- `_retryable`(346–349):`cstat!="done"` 或 `kstat=="failed"` 才重投;done 但 kb 在途/超时 **不重投**(防重复入库)。

## 8. 第二需求:没给账号/库就不入库(零改动,显式保持)

- 现状已满足:`autorun_kb.py` 强制 `--account/--kb`;broll-auto 技能(`SKILL.md`)写明"缺账号/库名 → 仅出 `filtered.html`,阶段二不入库并报告缺账号/库名"。
- 本次改动**不碰**此 guardrail。spec 显式声明:只有当次 broll-auto 调用提供了入库账号 + 库名,才进入 `autorun_kb.py` 阶段二;否则跑完阶段一即停并报告。实现阶段不引入任何"猜账号/猜库"路径。

## 9. 测试

- **桩单测**:用假的 jobs API 状态机(item 逐个 processing→done,kb queued→uploading→uploaded),验证:
  - 每条 done 在其完成的那一 tick 被提交 kb-upload(逐条,不等全批);
  - 同一 key 不被重复提交(submitted 去抖 + node2 幂等);
  - 退出严格等到 `finished 且 无 kb 在途`;
  - kb 卡 uploading + 清洗仍在动 → `kb_stall` 能触发(验证 C2 双计时器);
  - kb-upload 连续 POST 失败 → 熔断且循环不挂、清洗继续(验证 C5);
  - 退出聚合:orphan→not_done、done-但-kb-在途→非 uploaded(验证 C6)。
- **e2e**:用 broll-auto 记录的测试账号小批量(2~3 条)实跑,核对 `_autorun_manifest.jsonl` 中各条 `uploaded` 的时序是否随各自清洗完成**错开**(而非齐刷刷批尾)。

## 10. 改动面 / 契约

- **改**:`autorun_kb.py` —— `_clean_round` 重写为交织循环;`poll_clean` / `poll_kb` 折叠进该循环(或保留为内部 helper);`_snap_sig` 拆成两个指纹函数。新增 `POLL_S` / kb 熔断常量。
- **不改**:`main()` 重试轮次与汇总、台账写入、`load_manifest_uploaded` 去重、`resolve_kb`、`trigger_clean`、`kb_upload`(POST 封装)、所有数据契约字段名、node2 后端、broll-auto `SKILL.md` 的阶段二命令。
- **文档**:`SKILL.md` 阶段二步骤④⑤⑥的描述从"等 finished → 一次性 kb-upload → 等 kb"更新为"边洗边传:每条 done 即入库,两道独立看门狗 + 退出判据 = finished 且无 kb 在途"。
