# KB Recall Package Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `af-material-search` 中把口播稿语义计划、逻辑目录解析、知识库批量召回、筛选结果和账号素材包投递串成可重试的确定性流水线。

**Architecture:** AI 运行时只负责生成 `kb_recall_plan.json` 和筛选后的 `kb_recall_selection.json`；Python 脚本负责校验 JSON、按知识库范围批量调用线上接口、规范化 `kb_id + doc_id`、构造去重且保留多对多关系的投递负载。所有中间结果落到当前 `BROLL_RES`，投递使用独立 API Key 和稳定 `source_run_id`，网络重试不会创建重复素材包。

**Tech Stack:** Python 3 标准库、`unittest`、JSON 文件契约、现有 `/api/kb/knowledge-hub/resolve-kb-names`、`/pkb/kb/batch/search` 和 `/api/internal/material-packages/deliver` HTTP API。

## Global Constraints

- 不新增 Python 依赖，不引入后台服务或浏览器自动化。
- 日常命令使用 system `python3`；不得使用 `uvx` 或新建运行时。
- API Key 只从环境变量读取，不写入命令参数、结果文件或日志。
- 逻辑目录不存在时按解析接口语义静默忽略；某语义段最终没有知识库范围时停止 recall 并明确报错。
- 批量召回必须传非空 `kbs`。
- 线上返回的 `kb_id` 原样保留，`doc_id` 规范化为投递契约的 `file_id`。
- 带签名的 `file_url` 只用于搜索侧预览，不进入投递负载或持久化收据。
- 物理素材按 `kb_id + doc_id` 去重；同一素材允许保留多个语义段和查询词匹配。
- 搜索侧筛选以“匹配关系”为单位，不能用独占稿件匹配覆盖多对多关系。
- 投递超时或进程中断后必须能用同一 `source_run_id` 安全重试。
- 本仓库不保存账号侧编辑状态；投递成功后的查看、剔除、恢复和混剪由 `af-streamlit` 负责。

---

## 文件结构

- `kb_recall_contract.py`：四个 JSON 文件的验证、规范化和稳定 ID。
- `kb_recall_client.py`：三个远端 HTTP 接口的标准库客户端。
- `kb_recall_pipeline.py`：`recall`、`build`、`deliver` 三个 CLI 子命令。
- `KB_RECALL.md`：AI 语义规划、人工/模型筛选和运行命令的单一说明。
- `tests/test_kb_recall_contract.py`：文件契约与多对多去重测试。
- `tests/test_kb_recall_client.py`：HTTP 请求和响应适配测试。
- `tests/test_kb_recall_pipeline.py`：分组召回、负载构建和幂等收据测试。

流水线文件：

```text
scripts.json
→ kb_recall_plan.json
→ kb_recall_results.json
→ kb_recall_selection.json
→ material_package_payload.json
→ material_package_delivery.json
```

---

### Task 1: 定义语义计划、召回结果和筛选文件契约

**Files:**
- Create: `kb_recall_contract.py`
- Create: `tests/test_kb_recall_contract.py`

**Interfaces:**
- Produces:
  - `load_scripts(results_dir: str) -> list[Script]`
  - `load_recall_plan(results_dir: str, scripts: Sequence[Script]) -> RecallPlan`
  - `load_recall_results(results_dir: str) -> RecallResults`
  - `load_selection(results_dir: str, results: RecallResults) -> RecallSelection`
  - `material_key(kb_id: str, doc_id: str) -> str`。

- [ ] **Step 1: 写契约失败测试**

```python
class RecallContractTests(unittest.TestCase):
    def test_plan_requires_segments_to_reference_existing_scripts(self):
        with self.assertRaisesRegex(ValueError, "unknown script_id"):
            validate_plan(
                scripts=[{"script_id": "s1", "name": "稿一", "text": "正文"}],
                raw={"segments": [{"segment_id": "seg1", "script_id": "missing",
                                    "text": "片段", "queries": ["控制面板"],
                                    "directory_paths": ["知识库素材中台/奥克斯"]}]},
            )

    def test_material_key_uses_kb_and_doc_identity(self):
        self.assertEqual(material_key("kb-1", "doc-1"), "kb-1:doc-1")
```

覆盖重复 script/segment ID、空查询、空目录、未知 material key、未知 segment、重复 selected match 和稳定顺序。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_kb_recall_contract -v`

Expected: FAIL，因为模块不存在。

- [ ] **Step 3: 实现无依赖 dataclass 和校验器**

`kb_recall_plan.json` 的唯一有效形状：

```json
{
  "version": 1,
  "segments": [
    {
      "segment_id": "segment-1",
      "script_id": "script-1",
      "text": "支持语音控制，操作更方便",
      "queries": ["语音控制功能控制面板特写镜头"],
      "directory_paths": ["知识库素材中台/01_品牌与企业专区/奥克斯"],
      "position": 0
    }
  ]
}
```

`kb_recall_selection.json` 以匹配关系为粒度：

```json
{
  "version": 1,
  "selected_matches": [
    {
      "segment_id": "segment-1",
      "material_key": "kb-1:doc-1",
      "query": "语音控制功能控制面板特写镜头",
      "reason": "镜头直接展示控制面板"
    }
  ]
}
```

- [ ] **Step 4: 运行契约测试**

Run: `python3 -m unittest tests.test_kb_recall_contract -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add kb_recall_contract.py tests/test_kb_recall_contract.py
git commit -m "feat(kb-recall): define package pipeline contracts"
```

---

### Task 2: 实现目录解析、批量召回和投递 HTTP 客户端

**Files:**
- Create: `kb_recall_client.py`
- Create: `tests/test_kb_recall_client.py`

**Interfaces:**
- Produces:
  - `resolve_kb_names(app_base_url, api_key, directory_paths) -> tuple[str, ...]`
  - `batch_search(search_url, kbs, queries, page, page_size, top_k_per_query) -> list[SearchHit]`
  - `deliver_package(app_base_url, api_key, payload) -> DeliveryReceipt`。

- [ ] **Step 1: 写客户端失败测试**

```python
class RecallClientTests(unittest.TestCase):
    @mock.patch("kb_recall_client._post_json")
    def test_batch_search_maps_live_response_fields(self, post):
        post.return_value = (200, {
            "code": 200,
            "data": {"result": [{"doc_id": "doc-1", "kb_id": "kb-1",
                                    "kb_name": "旧机换新", "file_name": "a.mp4",
                                    "file_url": "https://signed", "query": "控制面板",
                                    "score": 0.91}], "total": 1},
        })
        hit = batch_search("http://search", ["旧机换新"], ["控制面板"], 1, 10, 5)[0]
        self.assertEqual((hit.kb_id, hit.doc_id), ("kb-1", "doc-1"))
```

覆盖 resolver 空数组、批量搜索拒绝空 kbs、非 200 code、非法字段、HTTP 超时和 delivery 的 200/201 两种成功状态。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_kb_recall_client -v`

Expected: FAIL。

- [ ] **Step 3: 用 `urllib.request` 实现 JSON POST**

```python
def _post_json(url, payload, headers, timeout=30):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))
```

日志只输出 URL、状态码和数量，不输出 header、API Key、口播稿全文或 `file_url`。

- [ ] **Step 4: 运行客户端测试**

Run: `python3 -m unittest tests.test_kb_recall_client -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add kb_recall_client.py tests/test_kb_recall_client.py
git commit -m "feat(kb-recall): add knowledge base API client"
```

---

### Task 3: 按知识库范围分组召回并保存规范化结果

**Files:**
- Create: `kb_recall_pipeline.py`
- Create: `tests/test_kb_recall_pipeline.py`

**Interfaces:**
- Consumes: Task 1 契约、Task 2 客户端。
- Produces: `run_recall(results_dir, client, page_size=10, top_k_per_query=5) -> RecallResults` 和 `kb_recall_results.json`。

- [ ] **Step 1: 写分组召回失败测试**

```python
def test_recall_groups_segments_with_same_resolved_kbs_and_preserves_many_to_many():
    results = run_recall(temp_dir, fake_client)
    self.assertEqual(fake_client.batch_calls, [
        {"kbs": ("kb-a",), "queries": ("控制面板", "语音控制")},
    ])
    matches = {(m.segment_id, m.material_key, m.query) for m in results.matches}
    self.assertIn(("seg-1", "kb-id:doc-1", "控制面板"), matches)
    self.assertIn(("seg-2", "kb-id:doc-1", "控制面板"), matches)
```

再测试不同 KB 范围分开调用、查询稳定去重、空解析范围失败、同一 doc 多查询只保留一个 material、结果原子写入。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_kb_recall_pipeline -v`

Expected: FAIL。

- [ ] **Step 3: 实现 recall 子命令**

算法必须是：

```text
逐 segment 解析 directory_paths
→ 按排序后的 kbs tuple 分组
→ 每组聚合并去重 queries
→ 每组调用一次 batch search
→ 通过 hit.query 关联组内所有包含该 query 的 segment
→ 按响应顺序为每个 query 独立计算从 1 开始的 rank
→ materials 按 kb_id + doc_id 去重
→ matches 按 segment + material + query 去重
→ 原子写 kb_recall_results.json
```

CLI：

```bash
python3 kb_recall_pipeline.py recall --results-dir "$BROLL_RES" --page-size 10 --top-k-per-query 5
```

环境变量：`AF_APP_BASE_URL`、`PKB_BATCH_SEARCH_URL`、`KB_MATERIAL_READER_API_KEY`。

- [ ] **Step 4: 运行流水线单测**

Run: `python3 -m unittest tests.test_kb_recall_pipeline -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add kb_recall_pipeline.py tests/test_kb_recall_pipeline.py
git commit -m "feat(kb-recall): recall materials by resolved directory scope"
```

---

### Task 4: 从筛选关系构造去重素材包负载

**Files:**
- Modify: `kb_recall_pipeline.py`
- Modify: `tests/test_kb_recall_pipeline.py`

**Interfaces:**
- Produces: `build_delivery_payload(results_dir, recipient_email, title, source_run_id) -> dict` 和 `material_package_payload.json`。

- [ ] **Step 1: 写负载构造失败测试**

```python
def test_build_payload_stores_material_once_and_keeps_selected_matches():
    payload = build_delivery_payload(temp_dir, "u@example.com", "八月素材包", "run-1")
    self.assertEqual(len(payload["materials"]), 1)
    self.assertEqual(len(payload["matches"]), 2)
    self.assertEqual(payload["materials"][0]["kb_id"], "kb-1")
    self.assertEqual(payload["materials"][0]["file_id"], "doc-1")
    self.assertNotIn("file_url", payload["materials"][0])
```

覆盖无选择结果、选择未知关系、选择重复关系、跨稿件同素材、reason 覆盖和稳定 payload 顺序。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_kb_recall_pipeline -v`

Expected: FAIL。

- [ ] **Step 3: 实现 build 子命令**

`materials.source_ref` 使用稳定请求内引用：

```python
source_ref = "m_" + hashlib.sha256(material_key.encode()).hexdigest()[:16]
```

只把被至少一条 `selected_matches` 引用的素材放入 payload。`matches` 从召回结果恢复原始 `score/rank/query`，从 selection 读取最终 `reason`。脚本和语义段保持原始 position。

CLI：

```bash
python3 kb_recall_pipeline.py build \
  --results-dir "$BROLL_RES" \
  --recipient-email user@example.com \
  --title "奥克斯 8 月口播素材包" \
  --source-run-id search-run-20260804-001
```

- [ ] **Step 4: 运行负载测试**

Run: `python3 -m unittest tests.test_kb_recall_pipeline -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add kb_recall_pipeline.py tests/test_kb_recall_pipeline.py
git commit -m "feat(kb-recall): build deduplicated package payload"
```

---

### Task 5: 投递、收据和安全重试

**Files:**
- Modify: `kb_recall_pipeline.py`
- Modify: `tests/test_kb_recall_pipeline.py`

**Interfaces:**
- Consumes: `material_package_payload.json`、Task 2 `deliver_package()`。
- Produces: `deliver_payload(results_dir, client) -> DeliveryReceipt` 和 `material_package_delivery.json`。

- [ ] **Step 1: 写投递失败测试**

```python
def test_delivery_retry_uses_same_source_run_id_and_records_idempotent_receipt():
    receipt = deliver_payload(temp_dir, fake_client)
    self.assertEqual(fake_client.payloads[0]["source_run_id"], "run-1")
    self.assertEqual(receipt.package_id, "pkg-1")
    saved = json.load(open(os.path.join(temp_dir, "material_package_delivery.json")))
    self.assertFalse(saved["created"])
```

覆盖 201 created、200 idempotent、超时不写成功收据、损坏 payload 拒绝和原子覆盖收据。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_kb_recall_pipeline -v`

Expected: FAIL。

- [ ] **Step 3: 实现 deliver 子命令**

```bash
MATERIAL_PACKAGE_DELIVERY_API_KEY=... \
python3 kb_recall_pipeline.py deliver --results-dir "$BROLL_RES"
```

`deliver` 不接受 email、title 或 source_run_id 参数；这些身份必须已经冻结在 `material_package_payload.json` 中。失败时保留 payload 并返回非零退出码；成功时原子写收据，便于重复执行和审计。

- [ ] **Step 4: 运行投递测试和完整 unittest**

Run: `python3 -m unittest tests.test_kb_recall_contract tests.test_kb_recall_client tests.test_kb_recall_pipeline -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add kb_recall_pipeline.py tests/test_kb_recall_pipeline.py
git commit -m "feat(kb-recall): deliver account material packages safely"
```

---

### Task 6: AI 操作说明、数据契约和线上只读验收

**Files:**
- Create: `KB_RECALL.md`
- Modify: `AGENTS.md`
- Modify: `skills/broll/SKILL.md`
- Modify: `tests/test_text_contracts.py`

**Interfaces:**
- Consumes: Tasks 1-5 CLI 和文件契约。
- Produces: AI 从口播稿生成 plan、筛选 selection、执行 recall/build/deliver 的唯一操作说明。

- [ ] **Step 1: 写文档合同失败测试**

```python
from pathlib import Path


def test_kb_recall_docs_name_all_artifacts_and_commands(self):
    text = Path("KB_RECALL.md").read_text()
    for name in ("kb_recall_plan.json", "kb_recall_results.json",
                 "kb_recall_selection.json", "material_package_payload.json",
                 "material_package_delivery.json"):
        self.assertIn(name, text)
    self.assertIn("kb_recall_pipeline.py recall", text)
    self.assertIn("kb_recall_pipeline.py deliver", text)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_text_contracts -v`

Expected: FAIL。

- [ ] **Step 3: 写单一操作说明并更新技能路由**

`KB_RECALL.md` 明确 AI 的两次职责：

1. 逐稿生成语义段、搜索词和逻辑目录到 `kb_recall_plan.json`。
2. 阅读规范化召回结果，按匹配关系生成 `kb_recall_selection.json`。

其余步骤必须调用确定性 CLI。`skills/broll/SKILL.md` 在用户明确要求“从知识库/素材中台召回并投递素材包”时路由到 `KB_RECALL.md`，不执行外部平台下载、清洗或重复入库流程。

- [ ] **Step 4: 运行本地完整测试**

Run: `python3 -m unittest discover -s tests -v`

Expected: 全部 PASS。

- [ ] **Step 5: 在临时结果目录执行线上只读 recall**

准备两条测试 segment，使用已上线目录解析接口和批量召回接口执行 `recall`，核对：

- `kb_recall_results.json` 中 `kb_id/doc_id/kb_name/file_name/query/score` 与线上响应一致。
- 相同素材被多个 query 命中时 `materials` 只有一条，`matches` 保留多条。
- 结果文件不包含 `file_url` 和任何 API Key。

本步骤不执行 `deliver`，直到 `af-streamlit` 投递接口部署并配置 Key。

- [ ] **Step 6: 投递接口部署后执行端到端验收**

用测试账号执行 build 和 deliver，随后在文案编辑坊确认素材包可见、素材数量一致、跨稿件关联完整，并验证同一 `source_run_id` 再投递返回同一 `package_id` 和 `created=false`。

- [ ] **Step 7: 提交**

```bash
git add KB_RECALL.md AGENTS.md skills/broll/SKILL.md tests/test_text_contracts.py
git commit -m "docs(kb-recall): document package delivery workflow"
```

---

## 完成标准

- AI 生成的语义计划能按各自逻辑目录限定真实知识库范围。
- 目录范围相同的查询合并为批量请求，范围不同的查询不会串库。
- 线上 `kb_id + doc_id` 被稳定保留，签名 URL 不落盘到投递负载。
- 素材去重但匹配关系不丢失，支持一个素材关联多个稿件和语义段。
- 同一 `source_run_id` 可以安全重试，账号不会收到重复素材包。
- 本地全部 unittest 通过，线上只读结果与接口响应一致，端到端投递与账号工作台数量一致。
