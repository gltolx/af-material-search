# 口播稿级自动选片 + 人物主体降分 + 时长规避 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 B-roll 流水线上加四条能力：R1 人物主体降分、R2 规避 >20 分钟、R3 逐稿匹配 2~5 条并自动勾选、R4 人设/稿名展示 + 口播稿名作文件名前缀。

**Architecture:** 收割/打分维持选题级混池不动；新增"稿/人设"维度活在两个新文件（`results/scripts.json`、`results/script_matches.json`），由 Claude 运行时解析/匹配产出。R1/R2 的阈值进 `relevance_spec.json`（code 内置默认值兜底），降分/超时判决在 `apply_verdicts.py` 确定性执行。`apply_verdicts.py` 设计为幂等可二次运行：有 `script_matches.json` 则渲染自动勾选 + 标签。

**Tech Stack:** Python 3（system python3 跑 score/apply，douyin venv python 跑 download_server）、yt-dlp + ffmpeg（抽帧）、纯前端 JS（filtered.html）。无 pytest——测试是 `python3 tests/*.py` 的 plain-assert 脚本 + 临时 fixture 跑真脚本。

---

## 测试约定（先读）

- 本仓**零测试框架**。新测试 = `tests/` 下的普通 python 脚本，末尾 `print("OK")`，用断言；`python3 tests/test_xxx.py` 单独跑，退出码非 0 即失败。**不引 pytest / 不加依赖**。
- 纯函数（命名、抽帧选择、payload 构造）→ import 后直接断言。
- 涉及整脚本行为（apply_verdicts 出页）→ 测试里用 `tempfile` 造最小 fixture 目录、`subprocess` 带 `BROLL_RES=<tmp>` 跑真脚本、再读产物断言。fixture 的 `cover` 一律设 `""`（避免 localize 走网络）。
- 用 **system `python3`** 跑（apply_verdicts/score_candidates 的铁律）；缺 PIL 只是跳过 pHash，不影响测试产物。

---

## Task 1: apply_verdicts.py — R1 人物主体降分 + R2 规避 >20 分钟

**Files:**
- Modify: `apply_verdicts.py:9-10`（加载 MAXDUR/PEN 配置）、`apply_verdicts.py:19-29`（`verdict()` 函数）
- Modify: `results/relevance_spec.json`（显式加两个键）
- Test: `tests/test_verdict_r1r2.py`

- [ ] **Step 1: 写失败测试**

`tests/test_verdict_r1r2.py`：
```python
#!/usr/bin/env python3
"""R1 人物主体降分 + R2 超20分钟弃:造 fixture 跑 apply_verdicts.py,断言三色。"""
import json, os, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run(tmp):
    cands = [
        {"idx":0,"platform":"B站/YT","title":"超长视频","url":"https://www.bilibili.com/video/BV10000000aa","page":"https://www.bilibili.com/video/BV10000000aa","cover":"","duration":1500,"stage0":"need_llm"},
        {"idx":1,"platform":"B站/YT","title":"单人说话头","url":"https://www.bilibili.com/video/BV20000000bb","page":"https://www.bilibili.com/video/BV20000000bb","cover":"","duration":120,"stage0":"need_llm"},
        {"idx":2,"platform":"B站/YT","title":"现场空镜","url":"https://www.bilibili.com/video/BV30000000cc","page":"https://www.bilibili.com/video/BV30000000cc","cover":"","duration":90,"stage0":"need_llm"},
    ]
    scores = [
        {"idx":0,"score":90,"scene":"x","era":"x","reason":"长","need_cover":False,"person_primary":"none"},
        {"idx":1,"score":80,"scene":"x","era":"x","reason":"单人","need_cover":False,"person_primary":"dominant"},
        {"idx":2,"score":80,"scene":"x","era":"x","reason":"现场","need_cover":False,"person_primary":"none"},
    ]
    spec = {"topic":"测试","plat_thresholds":{"B站/YT":{"keep_hi":62,"review_lo":40}},
            "max_duration_sec":1200,"person_penalty":{"dominant":35,"partial":12}}
    json.dump(cands, open(os.path.join(tmp,"candidates.json"),"w"), ensure_ascii=False)
    json.dump(scores, open(os.path.join(tmp,"scores_part0.json"),"w"), ensure_ascii=False)
    json.dump(spec, open(os.path.join(tmp,"relevance_spec.json"),"w"), ensure_ascii=False)
    env = dict(os.environ, BROLL_RES=tmp)
    r = subprocess.run([sys.executable, os.path.join(REPO,"apply_verdicts.py")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return {c["idx"]: c for c in json.load(open(os.path.join(tmp,"verdicts.json")))}

with tempfile.TemporaryDirectory() as tmp:
    v = run(tmp)
    assert v[0]["verdict"] == "drop" and "超20分钟" in v[0]["vreason"], v[0]   # R2
    assert v[1]["verdict"] == "review", v[1]                                   # R1:80-35=45 → review
    assert "人物主体" in v[1]["vreason"], v[1]
    assert v[2]["verdict"] == "keep", v[2]                                     # 对照:80 none → keep
print("OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_verdict_r1r2.py`
Expected: FAIL（断言错——现在 idx0 会按 90 分判 keep，idx1 不降分判 keep）

- [ ] **Step 3: 加配置加载**

`apply_verdicts.py` 第 10 行后插入：
```python
MAXDUR = int(spec.get("max_duration_sec", 1200))                    # R2:>此秒数(默认20分钟)直接弃
PEN = spec.get("person_penalty", {"dominant": 35, "partial": 12})   # R1:人物主体降分,spec 可覆盖
```

- [ ] **Step 4: 改写 verdict()**

把 `apply_verdicts.py:19-29` 的 `verdict()` 整体替换为：
```python
def verdict(c):
    s = scores.get(c["idx"]); pt = PT.get(c["platform"], DEFT)
    dur = c.get("duration")
    if dur and dur > MAXDUR:                                         # R2:超时长直接弃(透明显示在灰区)
        return "drop", f"超{MAXDUR // 60}分钟", (s.get("score") if s else None)
    if c["stage0"] == "kill": return "drop", "负面:" + c.get("kill_reason", ""), None
    if c["stage0"] == "need_enrich": return "review", "小红书空标题·待看封面", (s.get("score") if s else None)
    if not s: return "review", "未判分", None
    sc = s.get("score"); rsn = (s.get("reason") or "")[:40]
    pp = s.get("person_primary") or "none"                          # R1:none/partial/dominant
    if pp in ("dominant", "partial") and sc is not None:
        sc = max(0, sc - int(PEN.get(pp, 0))); rsn = (rsn + " ·人物主体")[:46]
    if s.get("need_cover"): return ("keep" if (sc or 0) >= pt["keep_hi"] else "review"), rsn + " ·待封面", sc
    if sc is None: return "review", rsn, None
    if sc >= pt["keep_hi"]: return "keep", rsn, sc
    if sc >= pt.get("review_lo", 40): return "review", rsn, sc
    return "drop", rsn, sc
```

- [ ] **Step 5: 给当前 spec 显式加键**

`results/relevance_spec.json` 在 `"plat_thresholds"` 块之后（第 75 行 `}` 后）加两个键（注意补逗号）：
```json
 "max_duration_sec": 1200,
 "person_penalty": {"dominant": 35, "partial": 12},
```
用编辑器插入到 `plat_thresholds` 对象之后、`concepts` 之前。改完用 `python3 -c "import json;json.load(open('results/relevance_spec.json'))"` 确认 JSON 合法。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_verdict_r1r2.py`
Expected: `OK`

- [ ] **Step 7: 提交**

```bash
git add apply_verdicts.py results/relevance_spec.json tests/test_verdict_r1r2.py
git commit -m "feat(verdict): R1 人物主体降分 + R2 规避>20min(阈值进spec,默认兜底)"
```

---

## Task 2: score_candidates.py — R2 预过滤提前 kill 已知超长

**Files:**
- Modify: `score_candidates.py:12-13`（读 MAXDUR）、`score_candidates.py:23-29`（kill 分支）
- Test: `tests/test_prefilter_duration.py`

- [ ] **Step 1: 写失败测试**

`tests/test_prefilter_duration.py`：
```python
#!/usr/bin/env python3
"""R2 预过滤:已知 duration>1200 的候选直接 kill(省 AI 打分)。"""
import json, os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with tempfile.TemporaryDirectory() as tmp:
    scored = [
        {"platform":"B站/YT","title":"超长片","url":"u1","page":"p1","cover":"","duration":1500},
        {"platform":"B站/YT","title":"正常片","url":"u2","page":"p2","cover":"","duration":100},
        {"platform":"B站/YT","title":"未知时长","url":"u3","page":"p3","cover":"","duration":None},
    ]
    spec = {"topic":"t","negative_terms":[],"max_duration_sec":1200}
    json.dump(scored, open(os.path.join(tmp,"scored.json"),"w"), ensure_ascii=False)
    json.dump(spec, open(os.path.join(tmp,"relevance_spec.json"),"w"), ensure_ascii=False)
    env = dict(os.environ, BROLL_RES=tmp)
    r = subprocess.run([sys.executable, os.path.join(REPO,"score_candidates.py")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    cands = json.load(open(os.path.join(tmp,"candidates.json")))
    by = {c["title"]: c for c in cands}
    assert by["超长片"]["stage0"] == "kill" and "超20" in by["超长片"].get("kill_reason",""), by["超长片"]
    assert by["正常片"]["stage0"] == "need_llm", by["正常片"]      # 未超长不动
    assert by["未知时长"]["stage0"] == "need_llm", by["未知时长"]  # 时长缺失放行(留给 apply_verdicts 回填后再卡)
print("OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_prefilter_duration.py`
Expected: FAIL（"超长片"现在被判 need_llm）

- [ ] **Step 3: 读 MAXDUR**

`score_candidates.py` 第 13 行 `NEG = SPEC["negative_terms"]` 后加：
```python
MAXDUR = int(SPEC.get("max_duration_sec", 1200))   # R2:已知时长>此值直接 kill,省 AI 打分(双保险,apply_verdicts 还会兜)
```

- [ ] **Step 4: 加 kill 分支**

`score_candidates.py` 第 24-25 行：
```python
    neg = next((n for n in NEG if n in t), None)
    if neg:
        rec["stage0"] = "kill"; rec["kill_reason"] = neg; kill.append(rec)
```
改为（在负面词命中之后、`elif plat == "小红书"` 之前插入超长分支）：
```python
    dur = c.get("duration")
    neg = next((n for n in NEG if n in t), None)
    if neg:
        rec["stage0"] = "kill"; rec["kill_reason"] = neg; kill.append(rec)
    elif dur and dur > MAXDUR:
        rec["stage0"] = "kill"; rec["kill_reason"] = f"超20分钟({dur}s)"; kill.append(rec)
```
（即把原 `if neg:` 链接成 `if neg / elif dur超长 / elif 小红书空标题 / else`，注意原本的 `elif plat == "小红书"` 现在跟在超长分支后。）

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_prefilter_duration.py`
Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add score_candidates.py tests/test_prefilter_duration.py
git commit -m "feat(prefilter): R2 已知>20min 候选预过滤直接 kill(省AI打分)"
```

---

## Task 3: extract_early_frames.py — 选择性抽开头帧(R1 识别手段)

**Files:**
- Create: `extract_early_frames.py`
- Test: `tests/test_extract_frames.py`

**职责**：只对 `scores_part*.json` 里 `need_frames:true` 的候选，按 idx 找到 `candidates.json` 的 page，用 `yt-dlp --download-sections "*0-6"` 下开头 6 秒到临时文件，ffmpeg 抽 3 帧（0s/2s/4s）落 `results/covers_frames/<idx>_f{0,1,2}.jpg`，供 Claude 看帧定稿 `person_primary`。纯函数 `select_need_frames` / `build_ytdlp_cmd` / `build_ffmpeg_cmd` 可单测；网络/ffmpeg 不进测试。

- [ ] **Step 1: 写失败测试（纯函数）**

`tests/test_extract_frames.py`：
```python
#!/usr/bin/env python3
"""extract_early_frames 纯函数:选择 need_frames 子集 + 构造命令。不下网络。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extract_early_frames as ef

scores = [
    {"idx":0,"person_primary":"none","need_frames":False},
    {"idx":1,"person_primary":"partial","need_frames":True},
    {"idx":2,"need_frames":True},
    {"idx":3,"person_primary":"dominant"},   # 无 need_frames → 不抽
]
sel = ef.select_need_frames(scores)
assert sel == [1, 2], sel

ycmd = ef.build_ytdlp_cmd("https://www.bilibili.com/video/BVxxx", "/tmp/clip", "yt-dlp", "ffmpeg")
assert "--download-sections" in ycmd and "*0-6" in ycmd, ycmd
assert "https://www.bilibili.com/video/BVxxx" == ycmd[-1], ycmd

fcmds = ef.build_ffmpeg_cmds("/tmp/clip.mp4", "/tmp/out/7", "ffmpeg")
assert len(fcmds) == 3, fcmds
assert all("ffmpeg" == c[0] for c in fcmds), fcmds
assert fcmds[0][-1].endswith("_f0.jpg") and fcmds[2][-1].endswith("_f2.jpg"), fcmds
print("OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_extract_frames.py`
Expected: FAIL with "No module named 'extract_early_frames'"

- [ ] **Step 3: 写实现**

`extract_early_frames.py`：
```python
#!/usr/bin/env python3
"""R1 识别手段:对 scores_part*.json 中 need_frames=true 的候选,下开头6秒抽3帧,供 Claude 看帧定稿 person_primary。
只对存疑子集抽(不给全池下片,守"轻")。读 candidates.json + scores_part*.json,出 results/covers_frames/<idx>_f{0,1,2}.jpg。
平台不支持区间下载时 best-effort 跳过(仍可只看静态封面)。
用法:python3 extract_early_frames.py   (system python3 即可;只调 yt-dlp/ffmpeg 子进程)
"""
import os, glob, json, subprocess, shutil, tempfile

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
FRAMES = os.path.join(RES, "covers_frames")


def _find_bin(name, env, fixed):
    p = os.environ.get(env)
    if p and os.path.exists(p): return p
    p = shutil.which(name)
    if p: return p
    c = [x for x in fixed if os.path.exists(x)]
    return sorted(c, reverse=True)[0] if c else name


def select_need_frames(scores):
    """返回需抽帧的 idx 列表(need_frames 为真)。"""
    return [s["idx"] for s in scores if s.get("need_frames") and "idx" in s]


def build_ytdlp_cmd(page, outbase, ytdlp, ffmpeg):
    """下开头6秒到 outbase.%(ext)s。"""
    return [ytdlp, "--no-warnings", "--no-playlist", "--ffmpeg-location", ffmpeg,
            "--download-sections", "*0-6", "--force-keyframes-at-cuts",
            "-f", "bv*+ba/b", "--merge-output-format", "mp4",
            "-o", outbase + ".%(ext)s", "--retries", "5", "--socket-timeout", "40",
            "--cookies-from-browser", "chrome", page]


def build_ffmpeg_cmds(clip, outbase, ffmpeg):
    """从 clip 抽 0/2/4 秒各一帧 → outbase_f{0,1,2}.jpg。"""
    cmds = []
    for i, t in enumerate((0, 2, 4)):
        cmds.append([ffmpeg, "-y", "-ss", str(t), "-i", clip, "-frames:v", "1",
                     "-q:v", "3", f"{outbase}_f{i}.jpg"])
    return cmds


def _find_clip(outbase):
    d, base = os.path.dirname(outbase), os.path.basename(outbase)
    cand = [f for f in os.listdir(d) if f.startswith(base + ".") and not f.endswith(".part")]
    return os.path.join(d, cand[0]) if cand else None


def extract_one(idx, page, ytdlp, ffmpeg):
    if not page:
        return False, "无 page"
    os.makedirs(FRAMES, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        clipbase = os.path.join(td, "clip")
        try:
            subprocess.run(build_ytdlp_cmd(page, clipbase, ytdlp, ffmpeg),
                           capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return False, "yt-dlp 超时"
        clip = _find_clip(clipbase)
        if not clip or os.path.getsize(clip) < 1024:
            return False, "无开头片段(平台不支持区间/限流)"
        outbase = os.path.join(FRAMES, str(idx))
        got = 0
        for cmd in build_ffmpeg_cmds(clip, outbase, ffmpeg):
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if os.path.exists(cmd[-1]) and os.path.getsize(cmd[-1]) > 500:
                    got += 1
            except subprocess.TimeoutExpired:
                pass
        return (got > 0), (f"抽到 {got} 帧" if got else "ffmpeg 未出帧")


def main():
    ytdlp = _find_bin("yt-dlp", "YTDLP",
                      glob.glob(os.path.expanduser("~/Library/Python/3.*/bin/yt-dlp")) + [os.path.expanduser("~/.local/bin/yt-dlp")])
    ffmpeg = _find_bin("ffmpeg", "FFMPEG", [os.path.expanduser("~/.local/bin/ffmpeg")])
    cands = {c["idx"]: c for c in json.load(open(os.path.join(RES, "candidates.json"), encoding="utf-8"))}
    scores = []
    for f in sorted(glob.glob(os.path.join(RES, "scores_part*.json"))):
        scores += json.load(open(f, encoding="utf-8"))
    todo = select_need_frames(scores)
    print(f"需抽帧候选 {len(todo)} 个 → {FRAMES}")
    ok = 0
    for idx in todo:
        c = cands.get(idx) or {}
        page = c.get("page") or c.get("url")
        good, msg = extract_one(idx, page, ytdlp, ffmpeg)
        ok += 1 if good else 0
        print(f"  [{idx}] {'✓' if good else '✗'} {msg} | {(c.get('title') or '')[:30]}")
    print(f"完成:{ok}/{len(todo)} 抽到帧;看 {FRAMES}/<idx>_f*.jpg 定稿 person_primary 后回填 scores_part")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_extract_frames.py`
Expected: `OK`

- [ ] **Step 5: 提交**

```bash
git add extract_early_frames.py tests/test_extract_frames.py
git commit -m "feat(R1): extract_early_frames.py 对存疑候选下开头6秒抽3帧(选择性,守轻)"
```

---

## Task 4: SCORING.md — person_primary 评判规则 + need_frames + 20min(文档)

**Files:**
- Modify: `SCORING.md`（在"首帧图"小节后加新小节）

- [ ] **Step 1: 加 person_primary + 时长小节**

`SCORING.md` 第 23 行（"首帧图"小节末尾）之后插入：
```markdown
## 人物主体降分(R1,2026-06)

> 目的:成片要和**数字人口播**混剪,真人出镜的素材会和数字人"打架"——所以**明显以人物为主体的素材降分**(不硬丢,留三色 + 人工兜底)。

- 判分时对每条候选多给一个 `person_primary` 字段:
  - `none` — 画面主体是物/景/事件(空镜、赛事画面、老物件、街景),无人物或人物只是远景陪衬。
  - `partial` — 有人物但非绝对主体(人群、背景路人、手部特写、模糊带过)。小扣分。
  - `dominant` — **明显以单人/说话头为主体**(采访、口播、vlog 正脸、单人占屏过半)。重扣分。
- **识别手段**:① 默认看本地封面 `covers/{idx}.jpg`;② 封面看不准(如封面是文字卡/标题党/截图)时,把该条标 `need_frames:true`,跑 `extract_early_frames.py` 下开头 6 秒抽 3 帧到 `covers_frames/<idx>_f*.jpg`,看帧再定 `person_primary`。
- **降分值**在 `relevance_spec.json.person_penalty`(默认 `{"dominant":35,"partial":12}`),由 `apply_verdicts.py` 确定性执行:有效分 = max(0, 原分 − penalty),再套三色阈值。改力度只改 spec 一处。

## 时长规避(R2,2026-06)

- 规避 **>20 分钟**(`relevance_spec.json.max_duration_sec`,默认 1200 秒)的视频:
  - `score_candidates.py` 对**已知时长**超长的预过滤直接 kill(省 AI 打分)。
  - `apply_verdicts.py` 在 duration 回填后,对 `duration>max_duration_sec` 的**强制判 drop**(理由"超20分钟",仍显示在灰区可查)。

## 判分输出字段(scores_part*.json)

每条:`{idx, score(0-100), scene, era, reason, need_cover, person_primary, need_frames}`。其中 `person_primary∈{none,partial,dominant}`、`need_frames` 为是否需抽帧确认(默认 false)。
```

- [ ] **Step 2: 验证**

Run: `cd /Users/linxiao/workspace/af-material-search && grep -c "person_primary" SCORING.md`
Expected: ≥ 3

- [ ] **Step 3: 提交**

```bash
git add SCORING.md
git commit -m "docs(scoring): person_primary 评判规则 + need_frames + 20min 规避"
```

---

## Task 5: apply_verdicts.py — 吃 script_matches.json:自动勾选 + 人设/稿名标签(R3+R4 渲染)

**Files:**
- Modify: `apply_verdicts.py`（加载 script_matches → idx 映射;`card()` 加 checked/data-*/标签;header 加欠匹配提示;CSS 加 `.mtag`）
- Test: `tests/test_script_matches_render.py`

- [ ] **Step 1: 写失败测试**

`tests/test_script_matches_render.py`：
```python
#!/usr/bin/env python3
"""R3+R4:有 script_matches.json 时,匹配卡片自动勾选 + 带 data-script/persona + 标签;欠匹配给提示。"""
import json, os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with tempfile.TemporaryDirectory() as tmp:
    cands = [
        {"idx":0,"platform":"B站/YT","title":"现场空镜","url":"https://www.bilibili.com/video/BV30000000cc","page":"https://www.bilibili.com/video/BV30000000cc","cover":"","duration":90,"stage0":"need_llm"},
        {"idx":1,"platform":"B站/YT","title":"另一条","url":"https://www.bilibili.com/video/BV40000000dd","page":"https://www.bilibili.com/video/BV40000000dd","cover":"","duration":80,"stage0":"need_llm"},
    ]
    scores = [
        {"idx":0,"score":80,"scene":"x","era":"x","reason":"现场","need_cover":False,"person_primary":"none"},
        {"idx":1,"score":75,"scene":"x","era":"x","reason":"备选","need_cover":False,"person_primary":"none"},
    ]
    spec = {"topic":"测试","plat_thresholds":{"B站/YT":{"keep_hi":62,"review_lo":40}},
            "max_duration_sec":1200,"person_penalty":{"dominant":35,"partial":12}}
    matches = {"s001":{"persona":"老王","name":"开场白稿","words":120,
                       "matched":[{"idx":0,"stable_id":"BV30000000cc","reason":"贴主旨","score":80}]}}
    for n, o in [("candidates.json",cands),("scores_part0.json",scores),
                 ("relevance_spec.json",spec),("script_matches.json",matches)]:
        json.dump(o, open(os.path.join(tmp,n),"w"), ensure_ascii=False)
    env = dict(os.environ, BROLL_RES=tmp)
    r = subprocess.run([sys.executable, os.path.join(REPO,"apply_verdicts.py")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    htmltext = open(os.path.join(tmp,"filtered.html"), encoding="utf-8").read()
    # idx0 被匹配 → 该 checkbox 带 checked + data-script + data-persona
    assert 'data-script="开场白稿"' in htmltext, "缺 data-script"
    assert 'data-persona="老王"' in htmltext, "缺 data-persona"
    assert 'class="sel"' in htmltext and "checked" in htmltext, "缺自动勾选"
    # 可见标签
    assert "开场白稿" in htmltext and "老王" in htmltext, "缺人设/稿名标签"
    # 欠匹配提示(s001 只 1 条 <2)
    assert "欠匹配" in htmltext, "缺欠匹配提示"
print("OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_script_matches_render.py`
Expected: FAIL（缺 data-script 等）

- [ ] **Step 3: 加载 script_matches → idx 映射**

`apply_verdicts.py` 第 17 行（scores 加载 for 循环结束）之后插入：
```python
# ---- R3/R4:逐稿匹配表(可选;由 Claude 匹配后产出)→ idx→{script_id,persona,name} ----
try:
    SM = json.load(open(os.path.join(RES, "script_matches.json"), encoding="utf-8"))
except Exception:
    SM = {}
IDX2SCRIPT = {}
for _sid, _info in (SM.items() if isinstance(SM, dict) else []):
    for _m in (_info.get("matched") or []):
        if "idx" in _m:
            IDX2SCRIPT[_m["idx"]] = {"persona": _info.get("persona", ""), "name": _info.get("name", "")}
UNDERMATCHED = [(_info.get("name") or "?") for _info in (SM.values() if isinstance(SM, dict) else []) if len((_info.get("matched") or [])) < 2]
```

- [ ] **Step 4: 改 card() 注入 checked/data-*/标签**

把 `apply_verdicts.py:124-140` 的 `card()` 整体替换为（在 checkbox 末尾加 `ds+chk`，meta 里插 `mtag`）：
```python
def card(c):
    cov = e(c.get("cover") or "")
    thumb = (f'<img class="im" loading="lazy" referrerpolicy="no-referrer" src="{cov}">' if cov and cov.startswith("covers/") else (f'<img class="im" loading="lazy" referrerpolicy="no-referrer" src="{cov}">' if cov else '<div class="ph">无封面</div>'))
    sc = c["vscore"]; scb = f'<span class="badge sc" style="background:{vcolor(c["verdict"])}">{sc if sc is not None else "?"}</span>'
    dup = f'<span class="badge dup">×{c["_dups"]+1}</span>' if c.get("_dups") else ''
    dur = c.get("duration"); durb = f'<span class="badge dur">{fmt(dur)}</span>' if dur else ''
    link = e(c.get("page") or c.get("url") or "#")
    sm = IDX2SCRIPT.get(c["idx"])                                       # R3/R4:命中匹配 → 自动勾选+标签
    chk = " checked" if sm else ""
    ds = (' data-script="' + e(sm["name"]) + '" data-persona="' + e(sm.get("persona") or "") + '"') if sm else ''
    mtag = ''
    if sm:
        per = e(sm.get("persona") or "")
        mtag = '<div class="mtag">' + (('👤' + per + ' ｜ ') if per else '') + '📄' + e(sm["name"]) + '</div>'
    return ('<div class="card">'
            '<a class="thumb" href="' + link + '" target="_blank" rel="noopener"'
            ' data-plat="' + e(c["platform"]) + '" data-page="' + e(c.get("page") or "") + '" data-url="' + e(c.get("url") or "")
            + '" data-cover="' + cov + '">'
            + thumb + scb + f'<span class="badge plat">{e(disp_plat(c))}</span>{dup}{durb}'
            '<label class="chk" onclick="event.stopPropagation()"><input type="checkbox" class="sel"'
            ' data-plat="' + e(c["platform"]) + '" data-page="' + e(c.get("page") or "") + '" data-url="' + e(c.get("url") or "")
            + '" data-title="' + e(c.get("title") or "") + '" data-verdict="' + e(c["verdict"])
            + '" data-score="' + e(c["vscore"] if c["vscore"] is not None else "") + '"' + ds + chk + '></label></a>'
            '<div class="meta"><a href="' + link + '" target="_blank" rel="noopener">' + e(c.get("title") or "(无标题)") + '</a>' + mtag + '<div class="sub">' + e(c["vreason"]) + '</div></div></div>')
```

- [ ] **Step 5: CSS 加 .mtag 样式**

`apply_verdicts.py:147` 的 CSS 字符串里，把 `.sub{...}` 那一段之后接上（在该 `"..."` 字符串内 `.sub` 规则后追加）：
```
.mtag{font-size:11px;color:#3730a3;background:#eef2ff;border:1px solid #c7d2fe;border-radius:4px;padding:1px 6px;margin-top:4px;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
```
即把第 147 行末尾的 `...margin-top:4px}"` 改为 `...margin-top:4px}.mtag{font-size:11px;color:#3730a3;background:#eef2ff;border:1px solid #c7d2fe;border-radius:4px;padding:1px 6px;margin-top:4px;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"`

- [ ] **Step 6: header 加欠匹配提示**

`apply_verdicts.py:300` 的 header `note` div 里，把 `<div class="note">共{len(reps)}条 ...` 那一行改为在其后追加欠匹配提示。具体把第 300 行：
```python
       f'<header><div class="hleft"><h1>素材关联度语义筛选 · {e(_topic_disp)}</h1><div class="note">共{len(reps)}条 · 🟢留{len(keep)}/🟡审{len(review)}/⚪弃{len(drop)} · {e(psum)}</div></div>'
```
改为：
```python
       f'<header><div class="hleft"><h1>素材关联度语义筛选 · {e(_topic_disp)}</h1><div class="note">共{len(reps)}条 · 🟢留{len(keep)}/🟡审{len(review)}/⚪弃{len(drop)} · {e(psum)}</div>'
       + (f'<div class="note" style="color:#b45309">⚠ 欠匹配(&lt;2条,建议补搜):{e("、".join(UNDERMATCHED))}</div>' if UNDERMATCHED else '')
       + '</div>'
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_script_matches_render.py`
Expected: `OK`

- [ ] **Step 8: 回归——确认无 script_matches.json 时仍正常出页**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_verdict_r1r2.py`
Expected: `OK`（该测试不带 script_matches.json，验证 SM 缺失分支不报错）

- [ ] **Step 9: 提交**

```bash
git add apply_verdicts.py tests/test_script_matches_render.py
git commit -m "feat(R3/R4): apply_verdicts 吃 script_matches → 自动勾选+人设/稿名标签+欠匹配提示"
```

---

## Task 6: apply_verdicts.py 前端 JS — /download 与 /clean 的 POST 带 script_name/persona(R4)

**Files:**
- Modify: `apply_verdicts.py:231`（下载 items.map）、`apply_verdicts.py:257`（清洗 items.map）
- Test: `tests/test_post_fields_in_html.py`

- [ ] **Step 1: 写失败测试**

`tests/test_post_fields_in_html.py`：
```python
#!/usr/bin/env python3
"""R4:生成的 filtered.html 里,两处 items.map 都带上 script_name/persona(从 data-script/data-persona 取)。"""
import json, os, subprocess, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with tempfile.TemporaryDirectory() as tmp:
    cands = [{"idx":0,"platform":"B站/YT","title":"t","url":"https://www.bilibili.com/video/BV30000000cc","page":"https://www.bilibili.com/video/BV30000000cc","cover":"","duration":90,"stage0":"need_llm"}]
    scores = [{"idx":0,"score":80,"reason":"x","need_cover":False,"person_primary":"none"}]
    spec = {"topic":"t","plat_thresholds":{"B站/YT":{"keep_hi":62,"review_lo":40}}}
    for n,o in [("candidates.json",cands),("scores_part0.json",scores),("relevance_spec.json",spec)]:
        json.dump(o, open(os.path.join(tmp,n),"w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(REPO,"apply_verdicts.py")],
                       env=dict(os.environ, BROLL_RES=tmp), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    h = open(os.path.join(tmp,"filtered.html"), encoding="utf-8").read()
    assert h.count("script_name:d.script") == 2, ("两处 map 都要带 script_name", h.count("script_name:d.script"))
    assert h.count("persona:d.persona") == 2, h.count("persona:d.persona")
print("OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_post_fields_in_html.py`
Expected: FAIL（count 为 0）

- [ ] **Step 3: 改下载 items.map**

`apply_verdicts.py:231`：
```python
  var items=checked.map(function(c){var d=c.dataset;return {platform:d.plat,page:d.page,url:d.url,title:d.title,verdict:d.verdict,score:d.score};});
```
改为：
```python
  var items=checked.map(function(c){var d=c.dataset;return {platform:d.plat,page:d.page,url:d.url,title:d.title,verdict:d.verdict,score:d.score,script_name:d.script||"",persona:d.persona||""};});
```

- [ ] **Step 4: 改清洗 items.map**

`apply_verdicts.py:257`：
```python
  var items=checked.map(function(c){var d=c.dataset;return {platform:d.plat,page:d.page,url:d.url,title:d.title,verdict:d.verdict,score:d.score};});
```
改为（同上加两字段）：
```python
  var items=checked.map(function(c){var d=c.dataset;return {platform:d.plat,page:d.page,url:d.url,title:d.title,verdict:d.verdict,score:d.score,script_name:d.script||"",persona:d.persona||""};});
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_post_fields_in_html.py`
Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add apply_verdicts.py tests/test_post_fields_in_html.py
git commit -m "feat(R4): filtered.html 的 /download 与 /clean POST 带上 script_name/persona"
```

---

## Task 7: download_server.py — 文件名加口播稿名前缀 + manifest 记 script_name/persona(R4)

**Files:**
- Modify: `download_server.py:271-276`（`process()` → 抽出 `build_name()`）、`download_server.py:286-295`（`manifest_append()`）
- Test: `tests/test_build_name.py`

- [ ] **Step 1: 写失败测试**

`tests/test_build_name.py`：
```python
#!/usr/bin/env python3
"""R4:文件名前缀。有 script_name → 前缀;无 → 维持原 平台_标题_id。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_server as ds

base = {"platform":"B站/YT","title":"国足米卢","page":"https://www.bilibili.com/video/BV19gpjeSEJ5","url":""}
# 无 script_name:保持原规则
assert ds.build_name(base) == "B站_国足米卢_BV19gpjeSEJ5", ds.build_name(base)
# 有 script_name:前缀(经 sanitize)
it = dict(base, script_name="开场白/稿一")
assert ds.build_name(it) == "开场白_稿一_B站_国足米卢_BV19gpjeSEJ5", ds.build_name(it)
# 空白 script_name 不加前缀
assert ds.build_name(dict(base, script_name="   ")) == "B站_国足米卢_BV19gpjeSEJ5"
print("OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_build_name.py`
Expected: FAIL with "module 'download_server' has no attribute 'build_name'"

- [ ] **Step 3: 抽出 build_name() 并在 process() 用**

`download_server.py:271-276`：
```python
def process(item, outdir):
    plat = platform_of(item)
    sid = stable_id(item.get("page"), item.get("url"))
    os.makedirs(outdir, exist_ok=True)
    name = f"{PLAT_CN.get(plat, plat)}_{sanitize(item.get('title'))}_{sid}"   # 人能看懂:平台_标题_ID
    outbase = os.path.join(outdir, name)
```
改为（新增 `build_name`，`process` 调用它）：
```python
def build_name(item):
    """文件名:有口播稿名则前缀(R4),否则 平台_标题_ID。"""
    plat = platform_of(item)
    sid = stable_id(item.get("page"), item.get("url"))
    base = f"{PLAT_CN.get(plat, plat)}_{sanitize(item.get('title'))}_{sid}"
    sn = (item.get("script_name") or "").strip()
    return f"{sanitize(sn, 30)}_{base}" if sn else base


def process(item, outdir):
    plat = platform_of(item)
    os.makedirs(outdir, exist_ok=True)
    name = build_name(item)
    outbase = os.path.join(outdir, name)
```

- [ ] **Step 4: manifest_append 记 script_name/persona**

`download_server.py:286-293` 的 `rec` 字典里，在 `"score": item.get("score", ""),` 之后加两字段：
```python
           "verdict": item.get("verdict", ""), "score": item.get("score", ""),
           "script_name": item.get("script_name", ""), "persona": item.get("persona", ""),
           "status": res["status"],
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_build_name.py`
Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add download_server.py tests/test_build_name.py
git commit -m "feat(R4): download_server 文件名加口播稿名前缀 + manifest 记 script_name/persona"
```

---

## Task 8: download_server.py — /clean payload 带 name_prefix/persona(R4 投递 node2)

**Files:**
- Modify: `download_server.py:487-490`（`handle_clean` 的 payload → 抽出 `_clean_item()`）
- Test: `tests/test_clean_item.py`

- [ ] **Step 1: 写失败测试**

`tests/test_clean_item.py`：
```python
#!/usr/bin/env python3
"""R4:/clean payload 每条带 name_prefix(口播稿名)+ persona,投递给 node2。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_server as ds

it = {"platform":"bilibili","title":"国足米卢","verdict":"keep","score":"88",
      "script_name":"开场白稿","persona":"老王"}
d = ds._clean_item("BV19gpjeSEJ5", it)
assert d["stable_id"] == "BV19gpjeSEJ5", d
assert d["name_prefix"] == "开场白稿", d
assert d["persona"] == "老王", d
assert d["title"] == "国足米卢" and d["verdict"] == "keep", d
# 无 script_name → name_prefix 空串
d2 = ds._clean_item("x", {"platform":"douyin","title":"t"})
assert d2["name_prefix"] == "" and d2["persona"] == "", d2
print("OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_clean_item.py`
Expected: FAIL with "has no attribute '_clean_item'"

- [ ] **Step 3: 抽出 _clean_item() 并在 payload 用**

`download_server.py:487-490`：
```python
        payload = {"topic": os.path.basename(outdir.rstrip("/")) or "broll",
                   "items": [{"stable_id": sid, "platform": it.get("platform", ""),
                              "title": it.get("title", ""), "verdict": it.get("verdict", ""),
                              "score": it.get("score", "")} for sid, it in pairs]}
```
改为（先在文件模块级——比如 `manifest_append` 之后——加 `_clean_item`，再让 payload 调用它）：

在 `download_server.py` 模块级函数区（`manifest_append` 之后、`resolve_outdir` 之前）新增：
```python
def _clean_item(sid, it):
    """投递 node2 的单条;name_prefix=口播稿名(R4,node2 honor 则成片带前缀),persona 备注。"""
    return {"stable_id": sid, "platform": it.get("platform", ""),
            "title": it.get("title", ""), "verdict": it.get("verdict", ""),
            "score": it.get("score", ""),
            "name_prefix": (it.get("script_name") or "").strip(),
            "persona": it.get("persona", "")}
```
把 `handle_clean` 里的 payload 改为：
```python
        payload = {"topic": os.path.basename(outdir.rstrip("/")) or "broll",
                   "items": [_clean_item(sid, it) for sid, it in pairs]}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/linxiao/workspace/af-material-search && python3 tests/test_clean_item.py`
Expected: `OK`

- [ ] **Step 5: 提交**

```bash
git add download_server.py tests/test_clean_item.py
git commit -m "feat(R4): /clean payload 带 name_prefix/persona 投递 node2(成片命名待node2 honor)"
```

---

## Task 9: 文档 — SKILL.md(/broll 步骤+规则) + CLAUDE.md(数据契约)

**Files:**
- Modify: `skills/broll/SKILL.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: SKILL.md — 接稿步加解析 scripts.json**

`skills/broll/SKILL.md:15`（"1. 接稿"那行）：
```markdown
1. **接稿**:稿子放 `topics/{选题}/` 或直接读。
```
改为：
```markdown
1. **接稿 + 解析 `results/scripts.json`**:稿子放 `topics/{选题}/` 或直接读。**逐条解析出 `[{script_id,persona,name,text,words}]`**——稿件正文一般自带标题与人设号名称,自己判:缺标题则总结一个短标题,缺人设号则 `persona` 留空。`words`=正文字数(决定该稿可配素材数上限)。
```

- [ ] **Step 2: SKILL.md — 判分步加 person_primary/need_frames**

`skills/broll/SKILL.md:27`（"5. 语义判分"那行）末尾追加：
```markdown
**外加 R1 人物主体判定**:每条多给 `person_primary∈{none,partial,dominant}`(none=物/景/事件主体,partial=人群/背景/手部,dominant=单人说话头/正脸占屏过半)+ `need_frames`(封面看不准时置 true)。封面看不准的跑 `python3 extract_early_frames.py` 下开头6秒抽帧到 `covers_frames/<idx>_f*.jpg`,看帧再定 `person_primary` 回填。降分由 apply_verdicts 按 `relevance_spec.json.person_penalty` 确定性执行。详见 `SCORING.md`。
```

- [ ] **Step 3: SKILL.md — 判决步后新增"逐稿匹配"步**

`skills/broll/SKILL.md` 第 28 行（"6. 判决+去重..."）之后、第 29 行（"7. 开页选片"）之前，插入新步骤 6b：
```markdown
6b. **逐稿匹配 + 自动选片(R3/R4)**:读 `results/scripts.json` + `results/verdicts.json`(keep + 高分 review 候选),**逐稿做语义匹配**,产出 `results/script_matches.json`:`{script_id:{persona,name,words,matched:[{idx,stable_id,reason,score}]}}`。**配额**:默认 2~5 条/稿(按稿需要镜头数判);正文 >500 字放宽(约每多 ~250 字 +1,封顶 ~10);**不硬凑**(优质匹配 <2 条记欠匹配,提示补搜)。**独占**:每个素材只挂最适配的一条稿(`idx` 不重复出现)。写完 **重跑 `python3 apply_verdicts.py`**——它吃 `script_matches.json` 重渲 `filtered.html`:匹配卡片自动勾选 + 卡片显示 `👤人设 ｜ 📄稿名`,checkbox 带 `data-script/data-persona`。
```

- [ ] **Step 4: SKILL.md — 下载步注明稿名前缀**

`skills/broll/SKILL.md:30`（第 8 步）里，把"文件名 `<平台>_<标题>_<id>.mp4`(人能看懂)"改为：
```markdown
文件名 `<口播稿名>_<平台>_<标题>_<id>.mp4`(匹配到稿的带稿名前缀;未匹配的退回 `<平台>_<标题>_<id>`)、同目录 `_manifest.jsonl`(= 入库接口,含 `script_name/persona`)
```

- [ ] **Step 5: SKILL.md — 8b 清洗步注明 name_prefix 限制**

`skills/broll/SKILL.md:32`（8b 末尾"坑:"前）追加一句：
```markdown
**R4 文件名前缀**:本地下载的源片已带口播稿名前缀;`/clean` 已把 `name_prefix` 随 payload 投递 node2,但**清洗成片是否带前缀取决于 node2 是否 honor 该字段**(共享后端,本仓不兜底,待对接)。
```

- [ ] **Step 6: CLAUDE.md — 数据契约加 scripts.json/script_matches.json/新字段**

`CLAUDE.md` 的"数据契约"小节，在 `results/scores_part*.json` 那条之后插入两条新文件、并补 scores_part 字段。具体在 `- \`results/scores_part*.json\`:语义分 ...` 这行的描述末尾把字段更新为含 `person_primary/need_frames`,并在其后加：
```markdown
- `results/scripts.json`:解析后的稿件 `[{script_id,persona,name,text,words}]`(接稿时 Claude 产;persona 可空,name 缺则总结)
- `results/script_matches.json`:逐稿匹配表 `{script_id:{persona,name,words,matched:[{idx,stable_id,reason,score}]}}`(判决后 Claude 产;独占·最佳匹配,2~5 条/稿,长稿更多;apply_verdicts 吃它做自动勾选+人设/稿名标签)
```
并把 scores_part 那条的字段从 `[{idx,score,scene,era,reason,need_cover}]` 改为 `[{idx,score,scene,era,reason,need_cover,person_primary,need_frames}]`。

- [ ] **Step 7: CLAUDE.md — 下载产物契约补稿名前缀**

`CLAUDE.md` 里 `~/Downloads/af素材/...` 那条契约中，把文件名格式 `<平台>_<标题>_<id>.mp4` 改为 `<口播稿名>_<平台>_<标题>_<id>.mp4`(匹配到稿的带前缀);`_manifest.jsonl` 每行字段补 `script_name,persona`。

- [ ] **Step 8: 验证文档落点**

Run:
```bash
cd /Users/linxiao/workspace/af-material-search && \
grep -c "script_matches" skills/broll/SKILL.md CLAUDE.md && \
grep -c "person_primary" skills/broll/SKILL.md CLAUDE.md && \
grep -c "6b" skills/broll/SKILL.md
```
Expected: 各文件计数 ≥ 1

- [ ] **Step 9: 提交**

```bash
git add skills/broll/SKILL.md CLAUDE.md
git commit -m "docs(broll): 9步流水线(加逐稿匹配)+ R1/R2/R4 规则 + scripts/script_matches 数据契约"
```

---

## Task 10: 端到端冒烟 + 全测试回归

**Files:**
- 无改动（仅验证）

- [ ] **Step 1: 跑全部新测试**

Run:
```bash
cd /Users/linxiao/workspace/af-material-search && \
for t in tests/test_verdict_r1r2.py tests/test_prefilter_duration.py tests/test_extract_frames.py \
         tests/test_script_matches_render.py tests/test_post_fields_in_html.py \
         tests/test_build_name.py tests/test_clean_item.py; do \
  echo "== $t =="; python3 "$t" || echo "FAIL $t"; done
```
Expected: 每个都打印 `OK`，无 `FAIL`

- [ ] **Step 2: download_server 可导入冒烟(douyin venv python)**

Run:
```bash
~/.local/share/uv/tools/douyin-mcp-server/bin/python -c "import download_server as d; print(d.build_name({'platform':'抖音','title':'测试','page':'https://www.douyin.com/video/7432108946253000000'})); print(d._clean_item('dy_x', {'platform':'douyin','title':'t','script_name':'稿A'}))"
```
Expected: 打印带前缀逻辑正确的文件名 + 含 `name_prefix` 的 dict，无异常

- [ ] **Step 3: 确认 .gitignore 不漏测试/计划/spec**

Run: `cd /Users/linxiao/workspace/af-material-search && git status --short`
Expected: 工作树干净（所有改动已提交）

- [ ] **Step 4: 最终提交（若有遗漏）**

```bash
git add -A && git commit -m "test(broll): 新功能全测试回归 OK" || echo "无遗漏"
```

---

## Self-Review 覆盖核对（写计划后已核）

- R1 人物主体降分 → Task 1(verdict 扣分) + Task 3(抽帧) + Task 4(规则文档) ✓
- R2 规避 >20min → Task 1(apply_verdicts drop) + Task 2(prefilter kill) ✓
- R3 逐稿匹配 2~5/自动勾选 → Task 5(渲染) + Task 9 步骤3(流水线 6b) ✓
- R4 人设/稿名展示 + 文件名前缀 → Task 5(标签) + Task 6(POST 字段) + Task 7(下载前缀) + Task 8(clean name_prefix) ✓
- 数据模型(scripts.json/script_matches.json/新字段) → Task 9(契约) ✓
- 类型一致:`script_name`(下载/manifest/POST)、`name_prefix`(仅 /clean payload)、`data-script`→`script_name`(前端 d.script 读 data-script)、`person_primary∈{none,partial,dominant}`、`need_frames` bool —— 全计划一致 ✓
- 幂等:apply_verdicts 无 script_matches.json 时走空 SM 分支(Task 5 Step 8 回归验证) ✓
