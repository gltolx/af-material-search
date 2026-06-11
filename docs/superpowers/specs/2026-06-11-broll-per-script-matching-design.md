# 设计：口播稿级素材自动选片 + 人物主体降分 + 时长规避

- 日期：2026-06-11
- 项目：af-material-search
- 范围：在现有 7 步 B-roll 流水线上，新增四条能力（R1~R4），插入 1 个"逐稿匹配"步骤（变 8 步）。

## 背景与现状

现有流水线：一批口播稿 → 提炼选题级 `relevance_spec.json`（概念词/查询词/阈值）→ 跨平台收割 → 预过滤 → AI 语义打分 → 三色判决+去重+出页（`filtered.html`）→ 选片勾选 → 下载/清洗。

调研结论（两端 Explore）：
- **无"稿/人设"维度**：候选全在一个混池（`scored.json`/`verdicts.json` 无 `script`/`persona` 字段），`relevance_spec.json` 只存选题级信息，原始稿件正文提炼完即丢。
- **无时长过滤**：duration 只展示不过滤（实测库里有 49 分钟的）。
- **无人物主体识别**：打分只看「标题为主 + 封面为辅」。
- **文件命名**：`download_server.py` 产 `<平台>_<标题40字>_<id>.mp4`，无稿名前缀。
- **勾选框默认全不选**；`/clean` 上传统一为 `<stable_id>.mp4`，成片名由共享后端 node2 生成。

## 需求（用户原话整理）

| # | 需求 |
|---|------|
| R1 | 规避/降分**人物为主体**的视频（真人 vs 数字人混剪打架）。**判定**：明显的和模棱的都降分，明显的降更多（不硬丢）。**识别**：看封面 + 看视频开头前几秒。 |
| R2 | 规避**超 20 分钟**（>1200s）的视频。 |
| R3 | 打分后**继续自动选片**：每稿匹配 **2~5 个**素材为宜，**>500 字**可更多；结果页**自动勾选**。 |
| R4 | 勾选素材显示**人设名 + 口播稿名**；批量下载/清洗时**口播稿名当文件名前缀**。 |

## 关键决策（已与用户确认）

1. **稿件输入**：稿件正文通常自带标题与人设号名称，由 Claude 解析；缺标题则总结一个短标题，缺人设号则留空。
2. **R1 力度**：明显的与模棱的都**降分**（不硬丢），明显的降更多；识别靠封面 + 视频开头前几秒（选择性抽帧）。
3. **一素材多稿**：**独占·按最佳匹配** —— 每个素材只挂最适配的那一条稿（卡片一个勾选框、一个文件名前缀）。
4. **匹配架构**：**方案 B（轻）** —— 收割/打分维持选题级混池；打分判决完后，由 Claude 读稿 + 候选池亲自做语义匹配，产出"稿→素材"分配表。不动收割层。

## 数据模型

两个新文件，`relevance_spec.json` 职责不变（仍是选题级单一事实源，仅新增阈值字段）。

### `results/scripts.json`（Claude 解析稿件后产出）
```json
[
  {"script_id": "s001", "persona": "老王", "name": "稿件短标题", "text": "口播稿全文…", "words": 320}
]
```
- `persona` 缺则空串 `""`；`name` 缺则 Claude 总结一个短标题；`words` = 正文字数（决定配额上限）。

### `results/script_matches.json`（Claude 匹配后产出，独占·最佳匹配）
```json
{
  "s001": {
    "persona": "老王",
    "name": "稿件短标题",
    "words": 320,
    "matched": [
      {"idx": 42, "stable_id": "BV1xx", "reason": "2002 世界杯现场镜头，贴主旨", "score": 88}
    ]
  }
}
```
- 候选侧 `verdicts.json` 不加 script 字段（保持混池）；关联只活在本文件。
- `idx` 对齐 `candidates.json`/`scores_part*.json`；`stable_id` 冗余存一份，便于下载侧对接。

### `relevance_spec.json` 新增字段（阈值单一事实源）
```json
{
  "max_duration_sec": 1200,
  "person_penalty": {"dominant": 35, "partial": 12}
}
```

### `scores_part*.json` 新增字段
```json
{"idx": 0, "score": 78, "scene": "…", "era": "…", "reason": "…", "need_cover": false,
 "person_primary": "none|partial|dominant", "need_frames": false}
```

## 各需求实现

### R1 人物主体（打分判定 + 选择性抽帧 + spec 驱动降分）
- 打分时 Claude 对每条候选多判 `person_primary`（`none`/`partial`=人群·背景·模糊/`dominant`=说话头·单人占屏过半），写入 `scores_part*.json`。
- **识别**：默认看静态封面；封面存疑标 `need_frames:true`。
- **新脚本 `extract_early_frames.py`**：仅对 `need_frames:true` 的候选，用 `yt-dlp --download-sections "*0-6"` + ffmpeg 抽 ~3 帧（0s/2s/4s）落到 `covers_frames/<id>_f{0,1,2}.jpg`，Claude 看帧后定稿 `person_primary`。**只对存疑子集抽，不给全池下片**（守"轻"）。平台不支持区间下载时回退仅看封面（best-effort）。
- **降分**：`apply_verdicts.py` 读 `person_penalty`，有效分 `= max(0, 原分 − penalty[person_primary])`，再套三色阈值。判决理由附"·人物主体"。

### R2 规避 >20 分钟
- `apply_verdicts.py` 在 duration 回填后，对 `duration > max_duration_sec` 的候选**强制判 drop**，理由"超20分钟"（仍显示在灰区，透明可查）。
- `score_candidates.py` 预过滤对**已知超长**的提前 `kill`，省 AI 打分（双保险，同读 `max_duration_sec`）。

### R3 自动选片（核心新步骤）
- 打分判决完 → Claude 读 `scripts.json` + `verdicts.json`（keep + 高分 review 候选）→ 逐稿语义匹配 → 写 `script_matches.json`。
- **配额**：默认 2~5 条/稿（按稿子需要的镜头数在区间内判）；正文 >500 字放宽，约每多 ~250 字 +1，**封顶 ~10**；**不硬凑**——优质匹配不足 2 条时记"⚠ 欠匹配"，提示补搜。
- **独占**：每个素材只挂最适配的一条稿。

### R4 展示 + 文件名前缀 + 清洗
- `apply_verdicts.py` 重渲 `filtered.html`（吃 `script_matches.json`）：匹配卡片 **自动 `checked`** + 卡片加标签 **`人设：xx ｜ 稿：xx`**（persona 空则只显示稿名）+ checkbox 多带 `data-script`/`data-persona`。欠匹配的稿在区头给提示。
- 前端 `/download`、`/clean` 的 POST items 多带 `script_name`/`persona`。
- `download_server.py`：文件名 `<sanitize(口播稿名)>_<平台>_<标题>_<id>.mp4`；`_manifest.jsonl` 多记 `script_name`/`persona`；`/clean` payload 也带上 `name_prefix`。
- **node2 成片命名（如实记录限制）**：批量下载路径文件名前缀 100% 由本仓控制、必生效。批量清洗路径：download_server 先把源片下到本地（同样带前缀，生效），再上传到 node2（上传文件名固定 `<stable_id>.mp4`），**清洗成片由 node2 命名且 download_server 不把成片拉回本地**——所以本地无成片可重命名。本仓能做的是把 `name_prefix` 随 `/clean` payload 投递给 node2；成片是否带前缀**取决于 node2 是否 honor 这个字段**，属共享后端依赖，本次作为待对接项记录，不在本仓兜底。

## 流水线顺序（7 步 → 8 步）
```
解析稿 → scripts.json
→ 收割 → scored.json
→ score_candidates（预过滤，含 R2 提前 kill）
→ Claude 打分（含 R1 person_primary）→ [存疑抽帧 extract_early_frames] → 定稿 scores_part
→ apply_verdicts（R1 降分 + R2 超时 drop → verdicts.json + 初版 HTML）
→ 【新】Claude 匹配 → script_matches.json
→ apply_verdicts 重渲（吃 script_matches → 自动勾选 + 人设/稿名标签）
→ 选片（已自动勾好）→ download_server（稿名前缀 + manifest + clean）
```
- `apply_verdicts.py` 设计为**幂等可二次运行**：`script_matches.json` 存在则渲染自动勾选+标签，不存在则渲染普通页。

## 改动文件清单
- **新增**：`extract_early_frames.py`
- **改**：`relevance_spec.json`（+`max_duration_sec`/`person_penalty`）、`apply_verdicts.py`（R1 降分 + R2 drop + 吃 script_matches 渲染 + 前端 POST 带 script/persona）、`download_server.py`（稿名前缀 + manifest + /clean name_prefix + 成片兜底重命名）、`score_candidates.py`（R2 提前 kill）、打分 prompt/schema（+`person_primary`/`need_frames`）
- **文档**：`SCORING.md`（人物主体 + 20 分钟规则）、`SKILL.md`（/broll：8 步 + 新规则）、`CLAUDE.md`（数据契约：scripts.json / script_matches.json / 新字段 / 文件名前缀）

## 边界与非目标
- 不改收割层、不引入 script_id 贯穿全链（方案 A 被否）。
- 不对全池抽帧；抽帧仅限 `need_frames` 子集，best-effort。
- node2 后端不在本仓改动范围；成片命名靠 `name_prefix` + 本地兜底重命名。
- 匹配的语义判断由 Claude 运行时完成，非新建打分模型。

## 验收
- 给一批带/不带标题与人设号的稿，跑完流水线后：`filtered.html` 中匹配素材**已自动勾选**，卡片显示人设/稿名；每稿 2~5 条（长稿更多）、独占不重复；明显人物主体素材落 drop/review；>20 分钟素材落 drop。
- 批量下载产物文件名以口播稿名为前缀；`_manifest.jsonl` 含 `script_name`/`persona`。
- 批量清洗：本地下载的源文件带前缀；`name_prefix` 随 payload 投递 node2（成片是否带前缀取决于 node2 是否 honor，记为待对接项）。
