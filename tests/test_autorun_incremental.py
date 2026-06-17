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
