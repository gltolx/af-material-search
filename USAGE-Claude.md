# /broll 使用说明 · Claude Code 用户

> ⚠️ **必须用 Claude Code(本机版:终端 CLI 或 VS Code 扩展)。**
> **不能**用 claude.ai 网页 / Claude 桌面 App / claude.ai/code 网页版——这些是**云端**,技能在云沙箱里跑,碰不到你本机的 Chrome、工具和下载目录,这个技能用不了。
> 不爱终端就装 **Claude Code 的 VS Code 扩展**(图形界面,同样在本机跑)。

## 前置
- macOS + Google Chrome
- Claude Code(CLI 或 VS Code 扩展)
- **Claude-in-Chrome** 插件,连上

## 安装(一次)
```bash
git clone https://github.com/gltolx/af-material-search.git
cd af-material-search
xcode-select --install      # 仅全新 Mac 需要(装完点完弹窗)
bash install.sh             # 装技能到 ~/.claude/skills + 装环境(yt-dlp/ffmpeg/venv…)+ 自检
```
看到 `✅ READY` 即可。红灯多半是没登录/没装 Chrome,按提示修。

## 登录(一次,人工)
在 Chrome 登录 **小红书 / B站 / 抖音 / YouTube**(采集账号),保持 Chrome 开着。B站/YouTube 高清下载依赖 Chrome web 登录态。

## 用
1. `cd` 进 `af-material-search` 目录,打开 Claude Code。
2. 把口播稿/选题丢给它,说「**用 /broll**」。
3. 它会:自动拉最新代码 → 自检 → 跑四平台收割 → 关联度三色筛选 → 出网页 → 你勾选 → 下载到 `~/Downloads/af素材/<选题>/`。
4. **你只做两件人工**:选片勾选、撞验证码时在 Chrome 里手动过(它绝不自动解)。

## 更新
不用管——每次用 `/broll` 它自动 `git pull` 最新。要手动:`git pull && bash install.sh`。
