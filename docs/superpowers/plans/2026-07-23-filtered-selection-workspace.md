# Filtered Selection Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `filtered.html` 的人工选择跨刷新和重开持久保存，并提供连续的已选素材聚合视图与 F1“查找下一个”定位。

**Architecture:** 已有 `download_server.py` 负责把每个 `BROLL_RES` 的选择快照原子写入 `selection_state.json`，浏览器以 namespaced `localStorage` 为离线备用。`apply_verdicts.py` 为每张卡片输出稳定 ID，并在同一份 DOM 节点上实现选择恢复、聚合/还原和 F1 导航，避免复制卡片造成状态分叉。

**Tech Stack:** Python 3 标准库、`BaseHTTPRequestHandler`、生成式纯 HTML/CSS/JavaScript、现有 plain-assert/unittest 测试、Codex in-app Browser 交互验证。

## Global Constraints

- 只在当前 `codex/broll-compat` feature branch和现有脏工作区中增量修改；不得覆盖 `apply_verdicts.py`、`download_server.py` 中既有未提交改动。
- `apply_verdicts.py` 和测试使用 system `python3`；不安装依赖、不使用 uv/uvx、Playwright或新后台服务。
- 持久状态固定写入当前 `BROLL_RES/selection_state.json`；素材身份复用两侧一致的 `stable_id(page, url)`。
- 主题池与 filler 池现有 `data-pool`、`data-from-script`、`data-script`、`data-persona`、下载和清洗 payload 不得改变。
- 空选择必须持久化；重新出页时，旧 ID 恢复人工状态，新 ID 保留 AI 默认状态。

---

### Task 1: 选择状态文件与 loopback API

**Files:**
- Modify: `download_server.py`
- Create: `tests/test_selection_state.py`

**Interfaces:**
- Produces: `load_selection_state() -> dict`，返回含 `initialized/version/revision/known_ids/selected_ids/updated_at` 的规范化状态。
- Produces: `save_selection_state(raw: dict) -> dict`，校验并原子保存快照。
- Produces: `GET /selection` 与 `POST /selection` JSON 端点。

- [ ] **Step 1: 写状态读写失败测试**

创建 `tests/test_selection_state.py`，用 `tempfile.TemporaryDirectory()` 临时替换 `download_server.RES`，断言：不存在时 `initialized is False`；空选择可往返；保存结果裁掉重复 ID并写时间；`selected_ids` 非 `known_ids` 子集、负 revision、损坏 JSON 均抛 `ValueError`；目录中不遗留 `.selection_state.*.tmp`。

核心测试数据：

```python
saved = ds.save_selection_state({
    "version": 1,
    "revision": 3,
    "known_ids": ["BV1234567890", "BV1234567890", "xhs_abcd12345678"],
    "selected_ids": [],
})
assert saved["revision"] == 3
assert saved["known_ids"] == ["BV1234567890", "xhs_abcd12345678"]
assert ds.load_selection_state()["initialized"] is True
assert ds.load_selection_state()["selected_ids"] == []
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 tests/test_selection_state.py`

Expected: FAIL，提示 `download_server` 尚无 `load_selection_state` 或 `save_selection_state`。

- [ ] **Step 3: 实现状态规范化和原子写入**

在 `download_server.py` 模块级增加 `SELECTION_VERSION = 1`、`_selection_lock = threading.Lock()`，实现：

```python
def _normalize_selection_state(raw):
    if not isinstance(raw, dict) or raw.get("version") != SELECTION_VERSION:
        raise ValueError("selection state version must be 1")
    revision = raw.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("selection revision must be a non-negative integer")
    known = _normalize_selection_ids(raw.get("known_ids"), "known_ids")
    selected = _normalize_selection_ids(raw.get("selected_ids"), "selected_ids")
    if not set(selected).issubset(set(known)):
        raise ValueError("selected_ids must be a subset of known_ids")
    return {"version": 1, "revision": revision, "known_ids": known,
            "selected_ids": selected, "updated_at": str(raw.get("updated_at") or "")}
```

`_normalize_selection_ids` 要求 list、最多 50,000 项、每项为长度 1–200 的字符串并按首次出现顺序去重。`save_selection_state` 在锁内写 `NamedTemporaryFile(delete=False, dir=RES, prefix=".selection_state.", suffix=".tmp")`、`flush/fsync`、`os.replace`，并在 `finally` 清理未替换临时文件。服务端用本机时区 `datetime.now().astimezone().isoformat(timespec="seconds")` 生成 `updated_at`。

- [ ] **Step 4: 运行状态测试确认 GREEN**

Run: `python3 tests/test_selection_state.py`

Expected: `OK`。

- [ ] **Step 5: 写 HTTP 契约失败断言**

在同一测试中读取 `download_server.py` 文本并断言包含 `parsed.path == "/selection"`、GET 调用 `load_selection_state()`、POST 调用 `save_selection_state(body)`，且 CORS methods 为 `GET,POST,OPTIONS`。

- [ ] **Step 6: 运行新增断言确认 RED**

Run: `python3 tests/test_selection_state.py`

Expected: FAIL，提示缺 `/selection` 路由。

- [ ] **Step 7: 实现 GET/POST 路由**

给 Handler 增加 `_send_json(status, payload)`。`do_GET` 在 `/selection` 调用 `load_selection_state()`，成功 200，损坏状态 500 `{"ok": false, "error": "..."}`。`do_POST` 在通用下载分支之前处理 `/selection`，合法快照返回 200 `{"ok": true, "revision": N, "selected": N}`，校验失败返回 400。把 `_cors()` 的 allow methods 改为 `GET,POST,OPTIONS`。

- [ ] **Step 8: 运行测试确认 GREEN**

Run: `python3 tests/test_selection_state.py`

Expected: `OK`。

### Task 2: HTML 稳定身份与选择恢复

**Files:**
- Modify: `apply_verdicts.py`
- Create: `tests/test_selection_workspace_render.py`

**Interfaces:**
- Produces: 每个 `.card` 与 `input.sel` 的 `data-id=<stable_id>`。
- Produces: 页面常量 `SELECTION_KEY = "af_selection:<16位sha256>"`。
- Consumes: Task 1 的 `GET/POST /selection`。

- [ ] **Step 1: 写生成页面失败测试**

测试创建包含一条主题 B站素材、一条 filler 小红书素材、对应 scores/matches 的临时 `BROLL_RES`，真实执行 `apply_verdicts.py`，断言：

```python
assert 'data-id="BV1234567890"' in html
assert 'data-id="xhs_abcdef123456"' in html
assert 'id="showSelected"' in html
assert 'id="selectedWorkspace"' in html
assert 'var SELECTION_KEY="af_selection:' in html
assert 'BASE+"/selection"' in html
assert 'localStorage.setItem(SELECTION_KEY' in html
assert 'known_ids' in html and 'selected_ids' in html and 'revision' in html
```

- [ ] **Step 2: 运行页面测试确认 RED**

Run: `python3 tests/test_selection_workspace_render.py`

Expected: FAIL，首先缺 `data-id` 或 `showSelected`。

- [ ] **Step 3: 输出稳定 ID、命名空间和骨架 DOM**

`card(c)` 复用现有 `sid = stable_id(...)`，在外层 `.card` 和 checkbox 同时写 `data-id`。用 `hashlib.sha256(os.path.abspath(RES).encode("utf-8")).hexdigest()[:16]` 生成 `SELECTION_NS` 并替换 JS 占位符。header 在下载按钮前加入：

```html
<button id="showSelected" class="dlbtn2">展示已选（0）</button>
```

`main` 中加入：

```html
<section id="selectedWorkspace" hidden>
  <h2>✓ 已选素材 <span id="selectedTotal">0</span></h2>
  <div id="selectedEmpty" class="selected-empty">当前没有已选素材</div>
  <div id="selectedGrid" class="grid"></div>
</section>
<div id="allWorkspace">现有全部分区</div>
```

- [ ] **Step 4: 实现初始化合并和串行保存**

在现有 async IIFE 中增加以下职责明确的函数：

```javascript
function allSels(){ return Array.prototype.slice.call(document.querySelectorAll("input.sel")); }
function normalizeState(raw){ /* version/revision/数组/子集校验；非法返回 null */ }
function applyState(state){ /* known ID 恢复人工值；未知 ID 保留 HTML checked */ }
function snapshot(bump){ /* 当前页裁剪后的 known/selected；bump 时 revision++ */ }
function readLocal(){ /* JSON parse + normalizeState；异常返回 null */ }
function writeLocal(state){ localStorage.setItem(SELECTION_KEY,JSON.stringify(state)); }
function queueSave(bump){ /* 同步 localStorage，再在 saveChain 上串行 POST /selection */ }
async function restoreSelection(){ /* local/server 按 revision 取新者；合入新 ID并保存 */ }
```

单项和 `zall` change 都执行 `refresh(); queueSave(true)`。恢复阶段不增加人工 revision；只有发现新 ID、服务端未初始化或需把更新的本地快照同步回服务端时，生成一次新快照。

- [ ] **Step 5: 运行页面测试确认 GREEN**

Run: `python3 tests/test_selection_workspace_render.py`

Expected: `OK`。

### Task 3: 连续聚合视图与 F1 导航

**Files:**
- Modify: `apply_verdicts.py`
- Modify: `tests/test_selection_workspace_render.py`

**Interfaces:**
- Produces: `setSelectedMode(on: boolean)` 在同一批卡片 DOM 上聚合/还原。
- Produces: `focusNextSelected()` 实现无修饰键 F1 定位。

- [ ] **Step 1: 写聚合与快捷键失败断言**

在页面测试中断言生成 HTML 包含：

```python
for token in [
    "selectionHomes", "document.createComment", "selectedGrid.appendChild",
    "restoreCard", "setSelectedMode", "返回全部素材",
    'e.key==="F1"', "focusNextSelected", "scrollIntoView",
    'block:"center"', "closest(\"details\")", "focus-hit",
]:
    assert token in html, token
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 tests/test_selection_workspace_render.py`

Expected: FAIL，缺聚合或 F1 token。

- [ ] **Step 3: 实现同节点聚合/还原**

初始化时按文档顺序为每张 `.card` 插入 comment marker 并存入 `Map selectionHomes`。`setSelectedMode(true)` 先 `stopPlay()`，把已勾选卡片依次 append 到 `selectedGrid`，显示 `selectedWorkspace` 并隐藏 `allWorkspace`；`false` 时把每张卡片插回其 marker 后，恢复原分区和顺序。聚合视图中取消勾选调用 `restoreCard(card)`，卡片立即从连续网格退出。`refresh()` 同步 `.picked` class、按钮文案、总数和空状态，并让总数覆盖所有 `.sel`，包括 filler-drop。

- [ ] **Step 4: 实现 F1 查找下一个**

维护 `focusedCard`。第一次 F1 从当前 header 下边界向下寻找最近的已选卡片；关闭 `<details>` 中的卡片用最近关闭祖先 summary 的位置参与比较。选中目标后展开全部祖先 details，调用 `scrollIntoView({behavior:"smooth",block:"center"})`，添加 `.focus-hit` 并显示 `已选 X / N`。后续 F1 按当前视图顺序加一并循环。取消当前项或切换视图时清除高亮；零选择时只更新 header 提示。

- [ ] **Step 5: 增加视觉样式**

增加 `.selected-workspace`、`.selected-empty`、`.card.picked`、`.card.focus-hit` 和 `@keyframes selectionPulse`。高亮必须同时有 3px 外框和 box-shadow，不能只依赖 checkbox 颜色。

- [ ] **Step 6: 运行页面测试确认 GREEN**

Run: `python3 tests/test_selection_workspace_render.py`

Expected: `OK`。

### Task 4: 回归与当前页面真实交互验证

**Files:**
- Modify: `CLAUDE.md`
- Modify: `skills/broll/SKILL.md`
- Test: `tests/test_selection_state.py`
- Test: `tests/test_selection_workspace_render.py`

**Interfaces:**
- Consumes: Tasks 1–3 的完整页面和服务端行为。

- [ ] **Step 1: 更新页面数据契约文档**

在 `CLAUDE.md` 的 `filtered.html + verdicts.json` 条目和 `skills/broll/SKILL.md` 的出页说明中追加：`selection_state.json` 跨刷新保存、右上角“展示已选”连续网格、F1 下一条定位。只改对应句子，不复制完整设计。

- [ ] **Step 2: 运行新增测试**

Run: `python3 tests/test_selection_state.py && python3 tests/test_selection_workspace_render.py`

Expected: 两个脚本均输出 `OK`。

- [ ] **Step 3: 运行全部测试**

Run: `for f in tests/test_*.py; do python3 "$f" || exit 1; done`

Expected: exit 0，无 FAIL/Traceback。

- [ ] **Step 4: 重新生成用户当前结果页**

识别当前浏览器 URL 对应的 `BROLL_RES`，先复制现有 `filtered.html` 到同目录 `filtered.before-selection-workspace.html` 作为可恢复备份，再用 system `python3` 和该 `BROLL_RES` 运行 `apply_verdicts.py`。不修改 `candidates.json`、scores 或 matches。

- [ ] **Step 5: 用当前页面验证完整交互**

在用户已打开的页面中：记录一个原始已选 ID；人工勾选一条未选素材并取消一条已选素材；刷新确认两项保持；点击“展示已选”确认跨分区连续网格和计数；在聚合视图取消一条并刷新确认保持；返回全部素材；连续按 F1 至少三次确认不同卡片居中高亮；定位最后一条后再按一次确认循环。测试结束恢复用户测试前的选择快照，避免改变用户实际选片结果。

- [ ] **Step 6: 检查最终差异**

Run: `git diff --check && git status --short && git diff --stat`

Expected: 无 whitespace error；仅包含既有用户改动、本次明确修改与新增测试/文档；不得出现临时测试文件、结果数据或备份文件被 git 跟踪。
