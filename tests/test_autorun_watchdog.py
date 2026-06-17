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
    def __init__(self):
        self.tick = 0
        self.f = {"a": {"status": "done", "kb_status": None, "stable_id": "s1", "elapsed": 9, "kb_pct": 0}}
    def get_json(self, url, timeout=None):
        self.tick += 1; _clock[0] += 60                  # 每 tick 过 60s
        return 200, {"status": "finished", "files": copy.deepcopy(self.f)}
    def kb_upload(self, job_id, account, kb_id, keys):
        for k in keys:                                    # 提交即 queued,但此后永不前进(卡死)
            if self.f[k]["kb_status"] is None: self.f[k]["kb_status"] = "queued"
        return list(keys), None
s = StuckKb(); A.get_json = s.get_json; A.kb_upload = s.kb_upload
_clock[0] = 1000.0
files = A.poll_clean_and_upload("j", 1, "acct", "kb1")    # 必须返回(不挂死)
assert files["a"]["kb_status"] == "queued", files         # 卡在 queued,非 uploaded
assert s.tick < 20, s.tick                                # 由 kb_stall(~tick5)收手,而非 hard_s(~tick150)
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
