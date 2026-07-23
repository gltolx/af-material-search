# Pexels Batch Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让当前 Pexels 验收页把已选素材的官方 MP4 直链批量下载到用户选择的本地目录。

**Architecture:** 在现有 `download_server.py` 中增加一个严格白名单的 Pexels 下载分流；用独立的小工具从本批 `harvest_pexels.json` 将 `direct_url` 注入现有 `filtered.html`，恢复下载按钮但保留清洗保护。Pexels 结果目录单独运行一个下载端点，通过自己的 `.dlport` 与页面连接。

**Tech Stack:** Python 3 标准库、现有 `requests`（douyin venv）、现有单文件 HTML/JavaScript、`unittest`。

## Global Constraints

- 只接受 `https://videos.pexels.com/...` 官方 MP4 直链。
- 不重跑素材搜索、相关性打分或自动勾选。
- 不自动开始下载；仅在用户点击“下载选中”后执行。
- 批量清洗按钮继续隐藏并拦截；下载端点的清洗地址固定为不可达的 `http://127.0.0.1:1`。
- 单条失败不阻断整批；继续复用现有幂等、进度和 `_manifest.jsonl`。
- 当前工作区有用户未提交改动；不得覆盖、回退或提交无关改动。

---

### Task 1: Pexels 后端下载分流

**Files:**
- Modify: `download_server.py`
- Create: `tests/test_pexels_download.py`

**Interfaces:**
- Consumes: 页面 POST 的 `{"platform":"Pexels","page":"Pexels 详情页","url":"官方 MP4 直链"}`。
- Produces: `platform_of(item) == "pexels"`、`dl_pexels(item, outbase) -> (file_or_none, error)`、`DOWNLOADERS["pexels"]`。

- [ ] **Step 1: 写失败测试**

```python
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import download_server as ds

item = {
    "platform": "Pexels",
    "title": "团队办公",
    "page": "https://www.pexels.com/zh-cn/video/8479048/",
    "url": "https://videos.pexels.com/video-files/8479048/sample.mp4",
}
assert ds.platform_of(item) == "pexels"
assert ds.build_name(item).startswith("Pexels_8479048_")

calls = []
old = ds._dl_once
try:
    def fake_once(url, headers, out):
        calls.append((url, headers, out))
        with open(out, "wb") as f:
            f.write(b"x" * 2048)
        return True, ""
    ds._dl_once = fake_once
    with tempfile.TemporaryDirectory() as tmp:
        outbase = os.path.join(tmp, "clip")
        fn, err = ds.dl_pexels(item, outbase)
        assert err == ""
        assert fn == outbase + ".mp4"
        assert os.path.getsize(fn) == 2048
finally:
    ds._dl_once = old

bad = dict(item, url="https://example.com/evil.mp4")
called = []
old = ds._dl_once
try:
    ds._dl_once = lambda *args: called.append(args)
    fn, err = ds.dl_pexels(bad, "/tmp/never-write")
    assert fn is None
    assert "videos.pexels.com" in err
    assert called == []
finally:
    ds._dl_once = old
```

- [ ] **Step 2: 运行测试并确认因缺功能失败**

Run:

```bash
/Users/linxiao/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_pexels_download.py
```

Expected: FAIL，因为 `platform_of()` 返回 `bilibili` 或 `dl_pexels` 尚不存在。

- [ ] **Step 3: 写最小后端实现**

在 `download_server.py` 中：

```python
PLAT_CN = {
    "bilibili": "B站", "youtube": "YouTube", "xiaohongshu": "小红书",
    "douyin": "抖音", "pexels": "Pexels",
}
```

`platform_of()` 在其他兜底前增加：

```python
if "pexels" in plat.lower() or "pexels.com" in host.lower():
    return "pexels"
```

增加：

```python
def dl_pexels(item, outbase):
    direct = (item.get("direct_url") or item.get("url") or "").strip()
    parsed = urllib.parse.urlparse(direct)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "videos.pexels.com":
        return None, "Pexels 仅允许 https://videos.pexels.com 官方直链"
    if not parsed.path.lower().endswith(".mp4"):
        return None, "Pexels 直链不是 MP4"
    out = outbase + ".mp4"
    ok, err = _dl_once(direct, {"User-Agent": UA, "Referer": "https://www.pexels.com/"}, out)
    return (out, "") if ok else (None, "Pexels 下载失败:" + err)
```

`native_id()` 在通用 `stable_id` 前增加：

```python
if platform_of(item) == "pexels":
    m = re.search(r"pexels\.com/(?:[^/]+/)?video/(?:[^/?#]*-)?(\d+)", item.get("page") or "")
    if m:
        return m.group(1)
```

并将 `pexels` 注册到 `DOWNLOADERS`。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
/Users/linxiao/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_pexels_download.py
/Users/linxiao/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_build_name.py
```

Expected: 两个测试均输出 `OK`。

### Task 2: 将官方直链注入现有 Pexels 页面

**Files:**
- Create: `enable_pexels_download.py`
- Create: `tests/test_enable_pexels_download.py`

**Interfaces:**
- Consumes: 结果目录中的 `harvest_pexels.json` 与 `filtered.html`。
- Produces: `enable_result_dir(path) -> int`，返回获得官方直链的 Pexels 卡片数。

- [ ] **Step 1: 写失败测试**

测试构造两张 Pexels 卡片，验证：

```python
updated = epd.enable_result_dir(result_dir)
assert updated == 2
assert output.count("https://videos.pexels.com/") == 4  # 每卡 anchor + checkbox
assert 'id="dlSel"' in output
assert 'id="dlSel" class="dlbtn" style="display:none"' not in output
assert 'id="disablePexelsDownloadGuard"' not in output
assert 'id="clnSel" class="dlbtn" style="display:none"' in output
assert 'id="disableCleaningGuard"' in output
assert epd.enable_result_dir(result_dir) == 2  # 幂等
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run:

```bash
python3 tests/test_enable_pexels_download.py
```

Expected: FAIL，因为 `enable_pexels_download.py` 尚不存在。

- [ ] **Step 3: 写最小页面转换实现**

`enable_pexels_download.py` 需要：

```python
def transform_html(source: str, direct_by_page: dict[str, str]) -> tuple[str, int]:
    """替换 Pexels 卡片的 data-url、恢复下载按钮、删除下载保护，保留清洗保护。"""

def enable_result_dir(result_dir: Path) -> int:
    """从 harvest_pexels.json 建映射，原子写回 filtered.html，返回匹配卡片数。"""
```

转换规则：

- 只改带 `data-plat="Pexels"` 的 `<a>` 和 `<input>` 标签；
- 用标签的 `data-page` 查 `direct_by_page`，将 `data-url` 换成 HTML 转义后的官方直链；
- 下载按钮仅删除 `display:none` 与 `aria-hidden`，保留初始 `disabled` 交给页面选择状态逻辑更新；
- 完整删除 `disablePexelsDownloadGuard` 脚本；
- 将页头提示改成“Pexels 支持批量下载；清洗/入库已禁用”；
- `disableCleaningGuard` 和清洗按钮保持原样；
- 用同目录临时文件 + `os.replace()` 原子写回。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
python3 tests/test_enable_pexels_download.py
```

Expected: 输出 `OK`。

### Task 3: 应用到本批并启动独立端点

**Files:**
- Modify generated artifact: `results_romeo_ai_education_20260723_pexels/filtered.html`
- Runtime sidecar: `results_romeo_ai_education_20260723_pexels/.dlport`

**Interfaces:**
- Consumes: Task 1 后端与 Task 2 页面转换工具。
- Produces: 当前 Pexels 页面可见“下载选中”按钮，并连接自己的本地端点。

- [ ] **Step 1: 转换当前页面**

Run:

```bash
python3 enable_pexels_download.py \
  /Users/linxiao/workspace/af-material-search/results_romeo_ai_education_20260723_pexels
```

Expected: 报告 `12` 张 Pexels 卡片获得官方直链；其中 10 张保持自动勾选。

- [ ] **Step 2: 启动持久端点**

在 Terminal 中运行：

```bash
env \
  MATCLEAN_CLEAN_URL=http://127.0.0.1:1 \
  MATCLEAN_CLEAN_TOKEN=disabled \
  BROLL_RES=/Users/linxiao/workspace/af-material-search/results_romeo_ai_education_20260723_pexels \
  /Users/linxiao/.local/share/uv/tools/douyin-mcp-server/bin/python \
  /Users/linxiao/workspace/af-material-search/download_server.py
```

Expected: 自动使用 `8789` 或下一个空闲端口并写入 Pexels 目录 `.dlport`。

- [ ] **Step 3: 运行完整验证**

Run:

```bash
DLP=$(cat results_romeo_ai_education_20260723_pexels/.dlport)
curl -fsS "http://127.0.0.1:$DLP/ping"
curl -fsS http://127.0.0.1:8765/results_romeo_ai_education_20260723_pexels/.dlport
```

Expected: 两者分别返回 `results_romeo_ai_education_20260723_pexels` 和同一个端口。

再用脚本检查：

- 10 个 checkbox 带 `checked`；
- 10 个已选项都有 `videos.pexels.com` 直链；
- 下载按钮可见；
- 清洗按钮与 `disableCleaningGuard` 仍存在；
- 主批 `8788` 的 `/ping` 仍返回主批目录名。

- [ ] **Step 4: 回归测试**

Run:

```bash
/Users/linxiao/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_pexels_download.py
python3 tests/test_enable_pexels_download.py
/Users/linxiao/.local/share/uv/tools/douyin-mcp-server/bin/python tests/test_build_name.py
```

Expected: 全部通过。
