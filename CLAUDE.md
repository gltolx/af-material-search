# af-material-search — 项目契约(新会话先读本文件)

**做什么:** 给一批口播稿 → 跨平台(抖音/小红书/B站/YouTube)搜 B-roll 素材 → 关联度**语义**打分 → 三色筛选(keep/review/drop)→ 选片 →(后续)入库到素材清洗知识库。

## 给一批稿子时怎么做
1. **先同步 + 自检**(先 `cd` 到 repo 根):① `bash ./selfupdate.sh`(每次用技能自动 ff-only 拉最新,免手动 git pull;离线/脏树自动跳过不阻塞)→ ② `bash ./preflight.sh`(挡掉所有环境坑;路径不写死,跟着 repo 走)。**报"缺失/坏"类红灯**(换机/环境被清)→ 先 `bash ./setup.sh` 一次性补装,再重跑 preflight;其余红灯(如登录态)按提示修或问用户。**全新机/换客户端**:先 `bash ./install.sh`(把技能装进 Claude/Codex + 装环境 + 自检,幂等)。
2. **走 `/broll` 技能**——它焊死了 7 步流水线、撞墙处置、坑→对策。照它做,别自己现摸。

## 铁律(本会话血泪,违反必踩坑)
1. **轻**:AI 自己当运行时(浏览器 + 已装工具);不上 Playwright/重框架/后台守护,不写签名爬虫,零运维。
2. **别重复犯错**:动手前先读本文件 + 跑 preflight.sh;细节坑见 SCORING.md / PLAN-B.md。
3. **复用别新造**:脚本/文档/工具都在(见下);改阈值/词表只改 `results/relevance_spec.json` 一处。
4. **uv 本体已被清**:稳态下禁止 `uv tool install`/`uvx`;复用已装 venv 的绝对路径;新依赖用 `pip3 install --user`。**唯一例外**:换机从零时 `setup.sh` 会临时用 uv 重建 douyin/xhs 这两个 tool venv(系统 py3.9 建不了 py3.10+),建完即回到本铁律——别在日常加依赖时动 uv。
5. **用对 Python**:抖音解析/出页用 douyin venv python;`score_candidates.py`/`apply_verdicts.py` 必须用 **system `python3`**(PIL/pHash 只在它那;用错 → pHash 去重被静默跳过 → 页面看着全是重复)。

## 环境(换机只改这一块)
- **换机/被清一键补齐**:`bash setup.sh`(幂等——已装的跳过;缺啥装啥:douyin/xhs venv + yt-dlp + ffmpeg + Pillow,剔 `--js-runtimes` 毒行,末尾自动跑 preflight)。macOS 实测过;Linux 分支 best-effort。下面是各组件落地路径(setup 装到这):
- 抖音解析/封面/出页:`~/.local/share/uv/tools/douyin-mcp-server/bin/python`(py3.12,含 requests+DouyinProcessor,无 PIL)
- 小红书:`~/.local/share/uv/tools/xiaohongshu-cli/bin/xhs`(已登录;`xhs search "kw" --type video --json`)
- B站/YT/小红书 下载:`~/Library/Python/3.9/bin/yt-dlp` + `~/.local/bin/ffmpeg`(优先 avc1;B站带 `--cookies-from-browser chrome` 解锁 1080P,没 cookie 海外 IP 412/封顶 480P;小红书喂带 token 的 page 走 explore SSR 直下;YT 韩国 IP 只 360p/403)
- 选片→下载端点:`download_server.py`(loopback :8788,**用 douyin venv python 跑**——直下抖音 + subprocess yt-dlp;`POST /download` 下载、`GET /preview?page=` 给小红书悬浮播放做 Range 代理)
- **批量清洗(2026-06,边下边清)**:filtered.html 除「⬇下载选中」还有「🧹批量清洗选中」——勾选 → `POST :8788/clean` → 秒回 task_id → Mac 按平台下载(四平台≤1080p)→ 边下边逐条传到清洗机 node2(matclean)→ 真 VLM 清字幕/logo/模板带 → 跳进度页 `https://tool.alphafin.world/?job=<task_id>` 看+下成片(每条 待清洗→清洗中→完成)。**清洗后端是共享的(node2,公网 tool.alphafin.world,node4 nginx→SSH隧道→node2:8848 systemd matclean),同事不用各自部署。** 起 download_server 时**必带这两个 env**(node2 强制 token,缺则 401):`MATCLEAN_CLEAN_URL=https://tool.alphafin.world MATCLEAN_CLEAN_TOKEN=14769e815e70a4be89ea98846ec2bf46 ~/.local/share/uv/tools/douyin-mcp-server/bin/python download_server.py`。注:小红书批量须当次新鲜 token(老 URL 可能失效);YT 长视频清洗按时长耗时(4.5min 片约 10min,正常非卡死)。
- 网络:Bash 出口在韩国,但浏览器走国内节点 → 抖音/小红书/B站可用。

## 数据契约(脚本间靠它对接,别改字段名)
- `results/scored.json`:候选 `[{platform,title,url,page,cover,duration}]`(四平台收割产物;`duration`=整数秒,B站/YT/抖音收割时自带,小红书靠 yt-dlp 补;旧数据用 `backfill_duration.py` 回填)
- `results/xhs_imgs.json`:小红书 `{note_id:{t(type:normal/video),imgs:[原图URL]}}`(merge_scored.py 从 xhs_raw.json **自动产**,backfill_xhs_token.py 也产;download_server 据 `t` 决定图文下图片/视频走 yt-dlp。**缺它则图文笔记被 yt-dlp 下成幻灯片 mp4**)
- `results/prefilter.json`:`{kill, need_enrich, need_llm}`(score_candidates.py 产)
- `results/scores_part*.json`:语义分 `[{idx,score,scene,era,reason,need_cover,person_primary,need_frames,eye_contact}]`(AI 亲自判;`person_primary∈{none,partial,dominant}`=R1 人物主体,`need_frames`=封面看不准需抽帧确认,`eye_contact`=正面半身/全身人脸且眼神盯镜头→R1b 由 apply_verdicts 硬丢)
- `results/scripts.json`:解析后的稿件 `[{script_id,persona,name,text,words}]`(接稿时 AI 产;persona 可空,name 缺则总结一个短标题)
- `results/script_matches.json`:逐稿匹配表 `{script_id:{persona,name,words,matched:[{idx,stable_id,reason,score}]}}`(判决后 AI 产;**独占·最佳匹配**;**配额按口播稿总数 N 分档**:N≥10→2~5/稿(常态)、5≤N≤9→4~6/稿、N<5→5~7/稿(少稿多配),同档内长稿取偏上限;apply_verdicts 吃它做自动勾选 + 人设/稿名标签 + 欠匹配提示)
- `results/filtered.html` + `verdicts.json`:三色判决 + 去重(apply_verdicts.py 产)。页面:卡片底中**时长角标**、右下重复角标;header **右上**=下载目录框+下载按钮;每区标题旁**一个三态全选框**(全选✓/部分=横线/空);**悬浮卡片自动播放**(B站/YT 官方 iframe、小红书走 :8788 `/preview` 代理、抖音静态封面);勾选框带 `data-plat/page/url/title/verdict/score`
- `~/Downloads/af素材/<选题>/<口播稿名>_<平台>_<标题>_<id>.mp4` + 同目录 `_manifest.jsonl`:选片后下载产物 + 清单(download_server.py 产;**匹配到稿的带口播稿名前缀,未匹配的退回 `<平台>_<标题>_<id>`**;默认下到系统下载目录·按选题归类,页面顶部路径框可改,下完自动开 Finder)。**`_manifest.jsonl` = 入库知识库对接口**,每行 `{ts,platform,stable_id,title,page,url,verdict,score,script_name,persona,status,file,bytes,error,warn}`(file=文件名;`warn`=如小红书无类型映射时的告警)
- 顺序:解析稿→scripts.json → 收割→scored.json → score_candidates.py(预过滤,含 R2 超时 kill)→ AI 判 need_llm(含 R1 person_primary)→ apply_verdicts.py(R1降分+R2超时弃+判决+去重+封面+出页)→ AI 逐稿匹配→script_matches.json → 重跑 apply_verdicts.py(吃 matches 自动勾选+人设/稿名标签)→ 选片(已自动勾好)→ download_server.py(稿名前缀分流下载+manifest)→(后续)读 manifest 入库

## 细节去哪看(单一事实源,别复制到这里)
- `PLAN-B.md` — 轻方案总纲 + 规模化方法(量靠干净平台 小红书/B站/YT,抖音做精搜补充)+ 工具链/反爬技巧
- `SCORING.md` — 关联度打分规则(标题为主+封面辅,三色阈值,扩词闭环)
- `ARCHITECTURE.md` §13 平台分工 / §14 查询姿势(描述≠召回,带年代/意图锚点)
- 记忆库(自动召回):跨会话软经验([[prefers-light-over-heavy]] / [[af-material-search-toolchain]] / [[search-query-posture]] / [[af-material-search-context]])
