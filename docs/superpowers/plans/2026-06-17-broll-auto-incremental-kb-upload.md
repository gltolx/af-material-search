# broll-auto 边清洗边入库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 broll-auto 阶段二每条素材清洗完成即刻入库知识库,不再等整批洗完才一次性入库。

**Architecture:** 把 `autorun_kb.py` 的 `_clean_round` 三段串行(等整批清洗→一次性 kb-upload→等入库)重写为**单个交织轮询循环** `poll_clean_and_upload`:每 tick 拉 node2 job 快照,发现新 `done` 即提交 kb-upload;双独立看门狗(清洗 stall / kb stall)+ 硬超时兜底;退出判据照抄 node2 `app.py:539`(`finished 且 无 kb 在途`)。返回值/台账/重试/去重契约全不变,node2 零改动(其 kb-upload 端点本身幂等去重)。

**Tech Stack:** Python 3(`autorun_kb.py` 跑在 douyin venv python,含 requests);测试为仓库惯例的「纯 assert 脚本」(无 pytest),`python3 tests/xxx.py` 打印 `OK`。

依据 spec:`docs/superpowers/specs/2026-06-17-broll-auto-incremental-kb-upload-design.md`(含 C1~C6 硬约束、双看门狗裁决)。

---

## File Structure

- **Modify** `autorun_kb.py`:
  - 删 `poll_clean`(171–206)、`poll_kb`(221–253)、`_snap_sig`(166–168)、常量 `CLEAN_POLL_S`/`KB_POLL_S`(36/38)。
  - 新增常量 `POLL_S` / `KB_FAIL_MAX`;新增 `_clean_sig` / `_kb_sig` / `poll_clean_and_upload` / `_aggregate_round`。
  - `_clean_round` 瘦身为:`trigger_clean` → `poll_clean_and_upload` → `_aggregate_round`。
  - 更新模块顶部 docstring(1–23)第 3~6 步描述为"边洗边传"。
- **Create** `tests/test_autorun_incremental.py`:happy-path 逐条入库 + 去重 + 退出判据。
- **Create** `tests/test_autorun_watchdog.py`:kb_stall 触发、kb-upload 熔断、`_aggregate_round` 聚合。
- **Modify** `skills/broll-auto/SKILL.md`:阶段二步骤④⑤⑥描述改为边洗边传 + 双看门狗 + 退出判据。

> 测试用 **douyin venv python** 跑(`autorun_kb.py` 顶部 `import requests`,缺则 `sys.exit(2)`):
> `~/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_autorun_incremental.py`

---

## Task 1: 核心重写 — `poll_clean_and_upload` + happy-path 逐条入库

**Files:**
- Modify: `autorun_kb.py`(常量区 36–41;删 166–168/171–206/221–253;改 256–285;docstring 1–23)
- Test: `tests/test_autorun_incremental.py`(新建)

- [ ] **Step 1: 写失败测试 `tests/test_autorun_incremental.py`**

```python
#!/usr/bin/env python3
"""poll_clean_and_upload:边洗边传——每条 done 即 kb-upload(逐条不等全批),
退出严格等到 finished 且无 kb 在途;同一 key 不重复提交。须用 douyin venv python 跑(有 requests)。"""
import os, sys, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import autorun_kb as A

A.time.sleep = lambda *a, **k: None                      # 不真睡

class Fake:
    """假 node2 job:3 文件错峰清洗(a@tick1 / b@tick2 / c@tick3),kb 提交后下一 tick 转 uploaded。"""
    def __init__(self):
        self.tick = 0
        self.f = {
            "a": {"status": "processing", "kb_status": None, "stable_id": "s1", "elapsed": 0, "kb_pct": 0},
            "b": {"status": "processing", "kb_status": None, "stable_id": "s2", "elapsed": 0, "kb_pct": 0},
            "c": {"status": "processing", "kb_status": None, "stable_id": "s3", "elapsed": 0, "kb_pct": 0},
        }
        self.upload_calls = []                           # 每次 kb_upload 收到的 keys
    def get_json(self, url, timeout=None):
        self.tick += 1
        for k, v in self.f.items():                      # kb queued→uploaded(模拟异步上传完成)
            if v["kb_status"] == "queued":
                v["kb_status"] = "uploaded"; v["kb_pct"] = 100
        order = {"a": 1, "b": 2, "c": 3}                 # 错峰 done
        for k, v in self.f.items():
            if self.tick >= order[k]:
                v["status"] = "done"
            elif v["status"] == "processing":
                v["elapsed"] += 5                        # 清洗在推进(看门狗不误判)
        status = "finished" if all(v["status"] in A._TERMINAL for v in self.f.values()) else "processing"
        return 200, {"status": status, "files": copy.deepcopy(self.f)}
    def kb_upload(self, job_id, account, kb_id, keys):
        self.upload_calls.append(list(keys))
        newq = []                                        # node2 幂等:只收 done 且未 queued/uploaded 的
        for k in keys:
            v = self.f.get(k)
            if v and v["status"] == "done" and v["kb_status"] not in ("queued", "uploading", "uploaded"):
                v["kb_status"] = "queued"; newq.append(k)
        return newq, None

fake = Fake()
A.get_json = fake.get_json
A.kb_upload = fake.kb_upload
files = A.poll_clean_and_upload("job_test", 3, "acct@x", "kb1")

# 1) 全部入库
assert all(files[k]["kb_status"] == "uploaded" for k in ("a", "b", "c")), files
# 2) 逐条(错峰):a 在 c 还没 done 时就已被提交
assert fake.upload_calls[0] == ["a"], fake.upload_calls
assert "b" in [k for call in fake.upload_calls for k in call]
# 3) 去重:每个 key 只被"新提交"一次(submitted 去抖 + node2 幂等)
flat = [k for call in fake.upload_calls for k in call]
assert flat.count("a") == 1 and flat.count("b") == 1 and flat.count("c") == 1, fake.upload_calls
print("OK incremental")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `~/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_autorun_incremental.py`
Expected: FAIL — `AttributeError: module 'autorun_kb' has no attribute 'poll_clean_and_upload'`

- [ ] **Step 3: 改 `autorun_kb.py` 常量区**(36–41 行)

把:
```python
CLEAN_POLL_S = 10
CLEAN_STALL_S = 25 * 60                                  # 清洗快照连续无变化 → 判卡死
KB_POLL_S = 6
KB_STALL_S = 12 * 60
```
改为:
```python
POLL_S = int(os.environ.get("AUTORUN_POLL_S", "8"))     # 统一轮询间隔(清洗10/kb6 折中)
CLEAN_STALL_S = 25 * 60                                  # 清洗快照连续无变化 → 判卡死
KB_STALL_S = 12 * 60                                     # kb 在途快照连续无变化 → 判卡死
KB_FAIL_MAX = int(os.environ.get("AUTORUN_KB_FAIL_MAX", "5"))  # kb-upload 连续失败达此 → 熔断停发只继续洗
```

- [ ] **Step 4: 删旧 `_snap_sig`(166–168)、`poll_clean`(171–206)、`poll_kb`(221–253),换成新指纹 + 交织轮询**

在原 `poll_clean`/`poll_kb` 位置写:
```python
def _clean_sig(files):
    """清洗看门狗指纹:仅未终态清洗文件的 (status, elapsed)。"""
    return tuple(sorted((k, f.get("status"), f.get("elapsed"))
                        for k, f in files.items() if f.get("status") not in _TERMINAL))


def _kb_sig(files):
    """入库看门狗指纹:仅在途 kb 文件的 (kb_status, kb_pct)。"""
    return tuple(sorted((k, f.get("kb_status"), f.get("kb_pct"))
                        for k, f in files.items() if f.get("kb_status") in ("queued", "uploading")))


def poll_clean_and_upload(job_id, n_expected, account, kb_id):
    """交织轮询:边洗边传。每 tick 拉 node2 job 快照——
      (a) 新 done 且未提交过的 key → 立即 kb-upload(本地 submitted 去抖;POST 失败下 tick 重发;连续失败→熔断);
      (b) 退出 = node2 finished 且 无 kb 在途(照抄 app.py:539,C1);
      (c) 双独立看门狗:清洗 stall(盯未终态清洗)/ kb stall(仅在清洗已完后盯在途 kb)(C2);
      (d) 整 job 硬超时兜底。
    返回最终 files 快照(含各文件 status/kb_status,由 _aggregate_round 取终值,C3)。"""
    base = NODE2 + "/api/jobs/" + job_id
    hard_s = max(2 * 3600, n_expected * 12 * 60) + max(30 * 60, n_expected * 90)   # 清洗预算+kb尾段预算(兜底)
    t0 = time.time()
    submitted = set()                                   # 已成功 POST(queued 返回含)的 key,仅作去抖(C4)
    kb_fail_streak = 0; kb_circuit_open = False
    clean_sig = kb_sig = None; clean_change = kb_change = t0; last_files = {}
    while True:
        try:
            code, body = get_json(base)
        except Exception as e:
            log("轮询 /api/jobs 异常(重试):" + str(e)[:100]); time.sleep(POLL_S)
            if time.time() - t0 > hard_s:
                log("硬超时(连轮询都拿不到),放弃等待"); return last_files
            continue
        if code == 410:
            log("job 已过期(node2 3天清理),停止轮询"); return last_files
        if code != 200 or not isinstance(body, dict):
            log("轮询返回异常 HTTP %s,稍后重试" % code); time.sleep(POLL_S); continue
        files = body.get("files") or {}; last_files = files
        status = body.get("status"); now = time.time()
        # (a) 边洗边传
        new_done = [k for k, f in files.items() if f.get("status") == "done" and k not in submitted]
        just_submitted = False
        if new_done and not kb_circuit_open:
            queued, err = kb_upload(job_id, account, kb_id, new_done)
            if err:
                kb_fail_streak += 1
                log("kb-upload 失败(连续 %d):%s" % (kb_fail_streak, err))
                if kb_fail_streak >= KB_FAIL_MAX:
                    kb_circuit_open = True
                    log("⚠️ kb-upload 连续失败 %d 次,熔断:停发,只继续清洗轮询" % kb_fail_streak)
            else:
                submitted |= set(queued); kb_fail_streak = 0
                if queued:
                    just_submitted = True
                    log("边洗边传:本tick入库提交 %d 条(submitted 累计 %d)" % (len(queued), len(submitted)))
        # 刚提交过 → 本 tick 快照已过期(提交前 GET 的),下 tick 再拉新状态判退出/看门狗,
        # 否则会在"最后一条刚 done 即 finished"那 tick 用旧快照误判"无 kb 在途"而早退漏入库(C1)。
        if just_submitted:
            time.sleep(POLL_S); continue
        # 进度日志 + 双看门狗计时
        csig = _clean_sig(files); ksig = _kb_sig(files)
        clean_pending = any(f.get("status") not in _TERMINAL for f in files.values())
        kb_inflight = any(f.get("kb_status") in ("queued", "uploading") for f in files.values())
        if csig != clean_sig or ksig != kb_sig:
            done_n = sum(1 for f in files.values() if f.get("status") == "done")
            up_n = sum(1 for f in files.values() if f.get("kb_status") == "uploaded")
            log("进度 finished=%s done=%d/%d 已入库=%d/%d" % (status == "finished", done_n, len(files), up_n, len(files)))
        if csig != clean_sig: clean_sig = csig; clean_change = now
        if ksig != kb_sig: kb_sig = ksig; kb_change = now
        # (b) 成功退出(C1)
        if status == "finished" and not kb_inflight:
            log("✅ 清洗全部终态 且 无 kb 在途,收尾"); return files
        # (c) 双看门狗(C2):清洗卡死随时判;kb 卡死仅在清洗已完后判(否则继续洗,别因 kb 卡放弃在洗的)
        if clean_pending and now - clean_change > CLEAN_STALL_S:
            stuck = [k for k, f in files.items() if f.get("status") not in _TERMINAL]
            log("⚠️ 清洗无进展 %d 分钟,判卡死,停等;已 done 继续入库:%s" % (CLEAN_STALL_S // 60, stuck[:8]))
            return files
        if not clean_pending and kb_inflight and now - kb_change > KB_STALL_S:
            stuck = [k for k, f in files.items() if f.get("kb_status") in ("queued", "uploading")]
            log("⚠️ 入库无进展 %d 分钟,判卡死,停等;未终态记非 uploaded:%s" % (KB_STALL_S // 60, stuck[:8]))
            return files
        # (d) 硬超时兜底
        if now - t0 > hard_s:
            log("⚠️ 整 job 硬超时(%d分钟),停等" % (hard_s // 60)); return files
        time.sleep(POLL_S)
```

- [ ] **Step 5: 改 `_clean_round`(256–285)用新循环 + 抽 `_aggregate_round`**

`_clean_round` 改为:
```python
def _aggregate_round(items, files, job_id):
    """把 node2 最终 files 快照按 stable_id 聚回每条 item 的 {cstat,kstat,key,job_id,err}。
    kstat/cstat 严格取自快照(C3);无 key→orphan;done 但 kb 在途/超时→kstat 非 uploaded(C6,_retryable 不重投)。"""
    out = {}
    key2sid = {k: (files.get(k) or {}).get("stable_id") for k in files}
    for it in items:
        sid = it["_sid"]
        key = next((k for k, s in key2sid.items() if s == sid), None)
        f = files.get(key) or {}
        cstat = f.get("status") if key else "orphan"
        out[sid] = {"cstat": cstat or "orphan", "kstat": f.get("kb_status") if key else None,
                    "key": key, "job_id": job_id, "err": f.get("err", "") if key else ""}
    return out


def _clean_round(items, kb_id, account, outdir, res):
    """一轮 清洗+边洗边传入库:触发清洗 → 交织轮询(每条 done 即入库)→ 按 sid 聚合。
    返回 ({_sid: {cstat,kstat,key,job_id,err}}, job_id)。job_id=None 表示触发失败。"""
    job_id, info = trigger_clean(dlport_required(res), items, outdir)
    if not job_id:
        log("❌ 本轮清洗触发失败:" + str(info))
        return {}, None
    files = poll_clean_and_upload(job_id, len(items), account, kb_id)
    done_n = sum(1 for f in files.values() if f.get("status") == "done")
    up_n = sum(1 for f in files.values() if f.get("kb_status") == "uploaded")
    log("本轮:清洗 done %d/%d,入库 uploaded %d" % (done_n, len(items), up_n))
    return _aggregate_round(items, files, job_id), job_id
```

- [ ] **Step 6: 跑测试确认通过**

Run: `~/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_autorun_incremental.py`
Expected: PASS — 打印 `OK incremental`

- [ ] **Step 7: 改模块 docstring(1–23 行)第 3~6 步描述**

把现有第 3~6 步("等清洗完成 / 收集成功项 / 入库 / 等入库"四段)替换为两段,体现边洗边传:
```
  3. 交织轮询(边洗边传):每 tick 拉 {node2}/api/jobs/<job_id>——发现新 done 即 kb-upload 该条(逐条不等全批,
     node2 端点幂等去重,本地 submitted 去抖);退出 = finished 且无 kb 在途(照抄 app.py:539)。
     **三道防线**:清洗 stall 看门狗(未终态清洗 STALL 不变→判卡)+ kb stall 看门狗(清洗完后在途 kb STALL 不变→判卡)
     + 整 job 硬超时(清洗预算+kb尾段预算)。卡住条记非 uploaded 不入库,已 uploaded/已 done 继续。
  4. 按 stable_id 聚回每条最终 {cstat,kstat}(严格取自 node2 快照),供台账 + 重试判定。
```
(原第 5、6 步并入,后续编号顺延:写台账=步骤 5。)

- [ ] **Step 8: 跑全量已有测试确认零回归**

Run: `for t in tests/test_*.py; do echo "== $t"; ~/.local/share/uv/tools/douyin-mcp-server/bin/python "$t" || python3 "$t"; done`
Expected: 各测试打印 `OK`(或其既有成功输出),无报错。

- [ ] **Step 9: Commit**

```bash
git add autorun_kb.py tests/test_autorun_incremental.py
git commit -m "feat(broll-auto): 边洗边传——每条清洗完成即入库(交织轮询替代等整批)

_clean_round 三段串行(等整批→一次性kb-upload→等入库)重写为 poll_clean_and_upload
单交织循环:新done即提交、退出照抄node2 app.py:539、双独立看门狗。node2零改动。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 防挂死/失败模式测试 — kb 看门狗 · 熔断 · 聚合

**Files:**
- Test: `tests/test_autorun_watchdog.py`(新建)
- Modify: `autorun_kb.py`(仅当测试暴露 bug 才改)

- [ ] **Step 1: 写测试 `tests/test_autorun_watchdog.py`**

```python
#!/usr/bin/env python3
"""三个防线测试:① kb 卡 uploading + 清洗已完 → kb_stall 触发(不挂死);
② kb-upload 连续失败 + 清洗仍在跑 → 熔断停发、循环靠清洗 finished 收尾;
③ _aggregate_round:orphan / done但kb在途 → kstat 非 uploaded。须用 douyin venv python 跑。"""
import os, sys, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import autorun_kb as A

A.time.sleep = lambda *a, **k: None
_clock = [1000.0]
A.time.time = lambda: _clock[0]                          # 可控时钟(stall 计时)

# ── ① kb_stall:清洗秒完,kb 提交后永远卡 queued ──────────────
A.KB_STALL_S = 120                                        # 缩短便于测
class StuckKb:
    def __init__(self): self.f = {"a": {"status": "done", "kb_status": None, "stable_id": "s1", "elapsed": 9, "kb_pct": 0}}
    def get_json(self, url, timeout=None):
        _clock[0] += 60                                   # 每 tick 过 60s
        return 200, {"status": "finished", "files": copy.deepcopy(self.f)}
    def kb_upload(self, job_id, account, kb_id, keys):
        for k in keys:                                    # 提交即 queued,但此后永不前进(卡死)
            if self.f[k]["kb_status"] is None: self.f[k]["kb_status"] = "queued"
        return list(keys), None
s = StuckKb(); A.get_json = s.get_json; A.kb_upload = s.kb_upload
_clock[0] = 1000.0
files = A.poll_clean_and_upload("j", 1, "acct", "kb1")    # 必须返回(不挂死)
assert files["a"]["kb_status"] == "queued", files         # 卡在 queued,非 uploaded
print("OK kb_stall 触发不挂死")

# ── ② 熔断:a 秒 done 但 kb-upload 恒失败;b 清洗 10 tick 后才 done ──
A.KB_FAIL_MAX = 3
class FailKb:
    def __init__(self):
        self.tick = 0; self.calls = 0
        self.f = {"a": {"status": "done", "kb_status": None, "stable_id": "s1", "elapsed": 9, "kb_pct": 0},
                  "b": {"status": "processing", "kb_status": None, "stable_id": "s2", "elapsed": 0, "kb_pct": 0}}
    def get_json(self, url, timeout=None):
        self.tick += 1; _clock[0] += 1
        if self.tick >= 10: self.f["b"]["status"] = "done"
        elif self.f["b"]["status"] == "processing": self.f["b"]["elapsed"] += 5
        status = "finished" if all(v["status"] in A._TERMINAL for v in self.f.values()) else "processing"
        return 200, {"status": status, "files": copy.deepcopy(self.f)}
    def kb_upload(self, job_id, account, kb_id, keys):
        self.calls += 1; return None, "boom"              # 永远失败
fk = FailKb(); A.get_json = fk.get_json; A.kb_upload = fk.kb_upload
_clock[0] = 1000.0
A.poll_clean_and_upload("j", 2, "acct", "kb1")            # 靠 b 清洗 finished 收尾
assert fk.calls == A.KB_FAIL_MAX, fk.calls               # 熔断后不再 POST(恰 KB_FAIL_MAX 次)
print("OK kb-upload 熔断")

# ── ③ _aggregate_round:聚合三态 ──────────────────────────────
items = [{"_sid": "s1"}, {"_sid": "s2"}, {"_sid": "s_orphan"}]
files = {"ka": {"status": "done", "kb_status": "uploaded", "stable_id": "s1", "err": ""},
         "kb": {"status": "done", "kb_status": "uploading", "stable_id": "s2", "err": ""}}
out = A._aggregate_round(items, files, "job9")
assert out["s1"]["kstat"] == "uploaded" and out["s1"]["cstat"] == "done", out
assert out["s2"]["kstat"] == "uploading" and out["s2"]["cstat"] == "done", out   # done 但 kb 在途 → 非 uploaded
assert out["s_orphan"]["cstat"] == "orphan" and out["s_orphan"]["kstat"] is None, out
# 维持 _retryable 语义:done+kb在途 不重投;orphan(cstat!=done) 可重投
assert A._retryable(out["s2"]) is False, out             # 防重复入库
assert A._retryable(out["s_orphan"]) is True, out
print("OK aggregate + retryable 语义")
print("ALL OK")
```

> 注:`_retryable` 当前定义在 `main()` 内(346–349 行的闭包)。若测试 `A._retryable` 取不到,本任务**把 `_retryable` 提为模块级函数**(纯函数、无闭包变量),`main()` 内引用不变。这是让其可测的最小改动。

- [ ] **Step 2: 跑测试,按失败逐个修**

Run: `~/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_autorun_watchdog.py`
Expected(首跑可能 FAIL):
- 若 `_retryable` 取不到 → 执行 Step 3 提为模块级;
- 若 kb_stall 不触发(死循环超时)→ 核对 `poll_clean_and_upload` 的 `(c)` 段 `not clean_pending and kb_inflight` 判据;
- 通过后打印 `ALL OK`。

- [ ] **Step 3:(条件)把 `_retryable` 提为模块级函数**

在 `main()` 之前加:
```python
def _retryable(r):
    # 可安全重投 = 清洗未成功(含下载失败的假失败)或 kb 明确 failed;
    # kb 在途/超时(已 done 但 kstat 未终态)不重投——避免它其实已上传造成重复入库
    return r.get("cstat") != "done" or r.get("kstat") == "failed"
```
删 `main()` 内 346–349 行那份同名闭包(引用处不变,改用模块级)。

- [ ] **Step 4: 跑测试确认通过**

Run: `~/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_autorun_watchdog.py`
Expected: PASS — 打印 `ALL OK`

- [ ] **Step 5: Commit**

```bash
git add tests/test_autorun_watchdog.py autorun_kb.py
git commit -m "test(broll-auto): kb看门狗/熔断/聚合 防挂死测试 + _retryable 提为模块级

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 更新 broll-auto 技能文档

**Files:**
- Modify: `skills/broll-auto/SKILL.md`(阶段二步骤 ④⑤⑥ 描述,43 行那段)

- [ ] **Step 1: 改 SKILL.md 阶段二 `autorun_kb.py 自动做` 那段**

把现文(43 行)里的:
```
→ **④轮询 node2 到 finished**(无进展看门狗 25min + 整 job 硬超时;卡住条记 orphan 不入库、已 done 继续)→ **⑤ kb-upload**(只传 done 的 **key**)→ **⑥轮询 kb_status 到 uploaded/failed**(独立第二道硬超时)→
```
改为:
```
→ **④交织轮询·边洗边传**(每条清洗 done 即刻 kb-upload 入库,逐条不等全批;退出=node2 finished 且无 kb 在途,照抄 app.py:539)→ **⑤三道防线**(清洗 stall 看门狗 25min + kb stall 看门狗 12min + 整 job 硬超时;卡住条记非 uploaded 不入库、已入库/已 done 继续)→
```
并把后面"⑦写台账"改为"⑥写台账"(编号顺延)。

- [ ] **Step 2: 自查渲染**

Run: `grep -n "边洗边传\|交织轮询\|app.py:539" skills/broll-auto/SKILL.md`
Expected: 命中新描述行。

- [ ] **Step 3: Commit**

```bash
git add skills/broll-auto/SKILL.md
git commit -m "docs(broll-auto): 阶段二改为边洗边传——每条清洗完成即入库

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 验收(e2e,人工/无人值守实跑,不进自动化)

按 broll-auto 记忆库的 e2e 测试账号,小批量(2~3 条)实跑阶段二,核对 `results*/_autorun_manifest.jsonl`:各条 `kb_status=="uploaded"` 的 `ts`(及 node2 进度页各条完成时刻)是否随**各自清洗完成错开**,而非齐刷刷集中在批尾 —— 即"边洗边传"生效。失败/超时条 `kb_status` 应为非 `uploaded`(下次跨 run 不被跳过、可补)。

---

## Self-Review(plan vs spec)

- **C1 退出判据照抄 app.py:539** → Task1 Step4 `(b)` 段 `status=="finished" and not kb_inflight`。✓
- **C2 双独立看门狗** → Task1 Step4 `(c)` 段两个独立计时器 + Task2 ① 测试。✓
- **C3 kstat 取自 node2 快照** → `_aggregate_round` 直读 `f.get("kb_status")` + Task2 ③ 测试。✓
- **C4 submitted 仅去抖、POST 失败不进 submitted** → `(a)` 段仅 `submitted |= set(queued)`(成功才加)。✓
- **C5 kb-upload 失败退避熔断** → `kb_fail_streak`/`kb_circuit_open` + Task2 ② 测试。✓
- **C6 仅 uploaded 算已入库、在途不重投** → `_aggregate_round` + 模块级 `_retryable` + Task2 ③ 断言。✓
- **第二需求(没给账号/库不入库)** → 零改动,`main()` 强制 `--account/--kb` 不变;无新增"猜账号/库"路径。spec §8 已声明,无需任务。✓
- **占位扫描**:无 TBD/TODO;每个改码步骤含完整代码。✓
- **类型/命名一致**:`poll_clean_and_upload`/`_aggregate_round`/`_clean_sig`/`_kb_sig`/`_retryable`/`POLL_S`/`KB_FAIL_MAX` 全程同名。✓
