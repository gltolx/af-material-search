#!/usr/bin/env python3
"""选片→下载 的 loopback 端点(方案A)。filtered.html 勾选→POST /download→按平台分流下载到本地。
仿 writer_server:Chrome 对 127.0.0.1 免 mixed-content;CORS 全开;前端用 text/plain 免预检。
按平台分流(全用已装工具,零新依赖):
  - B站(bilibili.com):yt-dlp + ffmpeg + --cookies-from-browser chrome + 桌面UA + Referer(没 cookie 海外IP必 412;带 cookie 解锁到 1080P)
  - YouTube(youtube.com/youtu.be):yt-dlp(韩国出口IP 实际只拿到 360p / 403,硬限制)
  - 小红书(xiaohongshu.com):yt-dlp 原生提取器(走 explore SSR,绕开被封的搜索API;喂带 xsec_token 的 page;原生 avc1 mp4)
  - 抖音(douyin.com):从 page 的 19位 video_id 现解无水印URL(iesdouyin)+ requests 带 Referer 下载(3次重试抗CDN抖动);yt-dlp 下不了抖音
落地目录由前端传 `dir`(用户可在页面顶部改),默认 `~/Downloads/af素材/<选题>/`;文件名 `<平台>_<平台原生ID>_<口播稿名或标题>.<ext>`(人能看懂;原生ID稳定→重下幂等)。
台账写 `<dir>/_manifest.jsonl`(= 后续入库知识库的对接口)。下完**自动打开文件夹**。NDJSON 流式回进度。
**必须用 douyin venv python 跑**(它有 requests 给抖音直下,又能 subprocess 出 yt-dlp):
  BROLL_RES=<dir> ~/.local/share/uv/tools/douyin-mcp-server/bin/python download_server.py   (run_in_background)
"""
import os, re, sys, json, time, glob, shutil, atexit, signal, threading, subprocess, urllib.request, urllib.parse, urllib.error
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
PORT = int(os.environ.get("DOWNLOAD_PORT", "8788"))
DEFAULT_DIR = os.path.expanduser(os.environ.get("DOWNLOAD_DIR") or "~/Downloads/af素材")
PLAT_CN = {"bilibili": "B站", "youtube": "YouTube", "xiaohongshu": "小红书", "douyin": "抖音"}
CLEAN_BASE = os.environ.get("MATCLEAN_CLEAN_URL", "https://tool.alphafin.world").rstrip("/")
CLEAN_TOKEN = os.environ.get("MATCLEAN_CLEAN_TOKEN", "")

HOME = os.path.expanduser("~")

# ---- 取消 / 信号兜底的模块级状态(契约5 / ④多开 SIGTERM 兜底)----
_NEVER = threading.Event()                  # 默认"永不取消"哨兵(未注册 job_id 时返回它)
_cancel_flags = {}                          # job_id -> threading.Event(置位即请求取消该 clean job)
_cancel_lock = threading.Lock()
# 在途未确认 sid:{job_id: (set(待确认 sid), hdr)};收 SIGTERM/SIGINT 时遍历补发 _fail_item,
# 把"被 kill=孤儿 loading"降级为"被 kill=明确 failed 可重试"。
_inflight = {}
_inflight_lock = threading.Lock()
_DLPORT_FILE = ""                            # 端口旁车文件路径(__main__ 绑定后赋值;atexit/信号处理共用清理)


def _find_bin(name, env, fixed):  # 版本无关定位:env→PATH→固定候选(换机 system python≠3.9 / ffmpeg 走 brew 也不坏)
    import glob, shutil
    p = os.environ.get(env)
    if p and os.path.exists(p): return p
    p = shutil.which(name)
    if p: return p
    c = [x for x in fixed if os.path.exists(x)]
    return sorted(c, reverse=True)[0] if c else name

YTDLP = _find_bin("yt-dlp", "YTDLP",
                  __import__("glob").glob(os.path.expanduser("~/Library/Python/3.*/bin/yt-dlp")) + [os.path.expanduser("~/.local/bin/yt-dlp")])
FFMPEG = _find_bin("ffmpeg", "FFMPEG", [os.path.join(HOME, ".local/bin/ffmpeg")])
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 抖音解析复用 resolve_douyin(同目录;用 douyin venv python 跑才 import 得到 douyin_mcp_server + requests)
try:
    from resolve_douyin import resolve as dy_resolve, HDR as DY_HDR
    _DY_ERR = ""
except Exception as e:
    dy_resolve, DY_HDR, _DY_ERR = None, {}, str(e)[:120]


def sanitize(s, n=None):
    """清洗成可入文件名的片段:去 emoji/杂符、空格→_、# 和路径非法字符→_、合并多下划线。
    n 给定才截断(本批次截断逻辑上移到 build_name 按总长 budget 控制)。"""
    s = (s or "").strip()
    s = re.sub(r"[\U0001F000-\U0010FFFF]", "", s)                       # SMP 区 emoji(🔥😀 等)
    s = re.sub(r"[\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\u2600-\u26FF]", "", s)  # BMP 箭头/杂项符号/装饰符(\u2190-\u21FF 箭头 / \u2300-\u27BF 杂项技术+装饰 / \u2B00 \u2600 杂项符号)
    s = re.sub(r'[/\\:*?"<>|\n\r\t#]+', "_", s)                         # 路径非法字符 + # 话题 → _
    s = re.sub(r"\s+", "_", s)                                          # 空格 → _
    s = re.sub(r"_+", "_", s).strip("_. ")                             # 合并多下划线 + 去首尾杂字符
    if n is not None:
        s = s[:n]
    return s or "untitled"


def stable_id(page, url):
    # 契约4:与 apply_verdicts.stable_id 两侧逐字一致(查 dl_precheck.json / 进 manifest / 投 node2)。
    # 兜底用 md5(非内置 hash):hash() 受 PYTHONHASHSEED 随机化、跨进程不同,而 apply 跑 system py3、
    # download_server 跑 douyin venv py = 两进程,兜底分支若用 hash() 两侧 sid 必对不上。
    import hashlib
    s = (page or "") + " " + (url or "")
    m = re.search(r"/video/(\d{10,})", s)                         # 抖音
    if m: return "dy_" + m.group(1)
    m = re.search(r"/explore/([0-9a-fA-F]{12,})", s)              # 小红书
    if m: return "xhs_" + m.group(1)
    m = re.search(r"(BV[0-9A-Za-z]{8,})", s)                      # B站(无前导斜杠,裸 BV 号也命中)
    if m: return m.group(1)
    m = re.search(r"[?&]v=([\w-]{6,})", s) or re.search(r"youtu\.be/([\w-]{6,})", s)  # YT
    if m: return "yt_" + m.group(1)
    return "id_" + hashlib.md5(s.encode("utf-8")).hexdigest()[:10]


def platform_of(item):
    plat = item.get("platform", "") or ""
    host = (item.get("page") or "") + " " + (item.get("url") or "")
    if "抖音" in plat or "douyin.com" in host: return "douyin"
    if "小红书" in plat or "xiaohongshu.com" in host: return "xiaohongshu"
    if "youtube.com" in host or "youtu.be" in host: return "youtube"  # 先判 YT(host 准)
    if "bilibili.com" in host or re.search(r"/BV[0-9A-Za-z]{8,}", host): return "bilibili"
    return "bilibili"  # "B站/YT" 标签且 host 缺失时的兜底


def _find_output(outbase):
    d, base = os.path.dirname(outbase), os.path.basename(outbase)
    if not os.path.isdir(d): return None
    cands = [f for f in os.listdir(d) if f.startswith(base + ".") and not f.endswith(".part")]
    if not cands: return None
    return os.path.join(d, max(cands, key=lambda f: os.path.getsize(os.path.join(d, f))))


def _ytdlp(target, outbase, extra):
    cmd = [YTDLP, "--no-warnings", "--no-playlist", "--ffmpeg-location", FFMPEG,
           "-o", outbase + ".%(ext)s", "--no-overwrites", "--continue",
           "--retries", "10", "--fragment-retries", "10", "--retry-sleep", "3",
           "--socket-timeout", "40", "--concurrent-fragments", "4"] + extra + [target]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None, "yt-dlp 超时(600s)"
    f = _find_output(outbase)
    if f and os.path.getsize(f) > 1024:
        return f, ""
    tail = (p.stderr or p.stdout or "").strip().splitlines()
    return None, ("yt-dlp rc=%d %s" % (p.returncode, tail[-1] if tail else ""))[:200]


def dl_bilibili(item, outbase):
    # 按短边卡 1080(竖屏 1080p 的 height=1920,旧 [height<=1080] 会把它降到 480p);-S res=短边
    return _ytdlp(item.get("page") or item.get("url"), outbase, [
        "--cookies-from-browser", "chrome", "--user-agent", UA,
        "--add-header", "Referer:https://www.bilibili.com/",
        "-f", "bv*+ba/b", "-S", "res:1080,vcodec:avc1,acodec:m4a",
        "--merge-output-format", "mp4"])


YTDLP_NEW = os.path.expanduser("~/.local/bin/yt-dlp-new")   # 2026.03.17 独立二进制 + deno 本地 PO-token 破 SABR(见记忆 youtube-1080p-sabr-bypass)

def dl_youtube(item, outbase):
    # 真 1080p 走 yt-dlp-new(Deno 解 PO token)。普通 yt-dlp 封顶 ~720p HLS。按短边卡 1080。
    target = item.get("page") or item.get("url")
    if os.path.exists(YTDLP_NEW):
        env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", ""))
        cmd = [YTDLP_NEW, "--no-warnings", "--no-playlist", "--ffmpeg-location", FFMPEG,
               "--cookies-from-browser", "chrome",
               "-f", "bv*+ba/b", "-S", "res:1080,vcodec:avc1,acodec:m4a",
               "--merge-output-format", "mp4", "-o", outbase + ".%(ext)s",
               "--no-overwrites", "--retries", "10", target]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
        except subprocess.TimeoutExpired:
            pass
        f = _find_output(outbase)
        if f and os.path.getsize(f) > 1024:
            return f, ""
    # 兜底:普通 yt-dlp。YouTube 高清的关键是 web 登录态(Chrome 登录了 YouTube 就有高清,没登录才 360p,同 B站逻辑)。
    # → android client 兜底也带 --cookies-from-browser chrome(用拍板②:登录态拿高清,源无高清才退低清)。
    fn, err = _ytdlp(target, outbase, [
        "--cookies-from-browser", "chrome",
        "--extractor-args", "youtube:player_client=android,web_safari",
        "--user-agent", UA,
        "-f", "bv*+ba/b/18", "-S", "res:1080,vcodec:avc1,acodec:m4a", "--merge-output-format", "mp4"])
    if fn:
        return fn, ""
    fn2, err2 = _ytdlp(target, outbase, [
        "--cookies-from-browser", "chrome", "--user-agent", UA,
        "-f", "bv*+ba/b", "-S", "res:1080,vcodec:avc1,acodec:m4a", "--merge-output-format", "mp4"])
    return fn2, (err2 or err or "")


_XHS_IMGS = None
def _xhs_imgs_map():
    # id -> [图片URL],由收割时捕获的 imageList 写进 RES/xhs_imgs.json(图文笔记下载图片用)
    global _XHS_IMGS
    if _XHS_IMGS is None:
        try:
            _XHS_IMGS = json.load(open(os.path.join(RES, "xhs_imgs.json"), encoding="utf-8"))
        except Exception:
            _XHS_IMGS = {}
    return _XHS_IMGS

def _xhs_note_id(item):
    m = re.search(r"/explore/([0-9a-fA-F]{12,})", (item.get("page") or "") + " " + (item.get("url") or ""))
    return m.group(1) if m else None

def dl_xhs_images(item, outbase):
    # 图文笔记:按 note id 查收割到的图片 URL,逐张下载(xhsCDN 不要 Referer);存 <outbase>_01.jpg ...
    import requests
    nid = _xhs_note_id(item)
    info = _xhs_imgs_map().get(nid) or {}
    urls = info.get("imgs") or []
    if not urls:
        return None, "图文笔记且无图片URL — 需用带 imageList 的新收割补"
    saved, hdr = [], {"User-Agent": UA}
    for i, u in enumerate(urls, 1):
        if not u:
            continue
        try:
            r = requests.get(u, headers=hdr, timeout=40)
            if r.status_code == 200 and len(r.content) > 1024:
                ext = ".png" if r.content[:4] == b"\x89PNG" else ".jpg"
                fp = "%s_%02d%s" % (outbase, i, ext)
                open(fp, "wb").write(r.content); saved.append(fp)
        except Exception:
            pass
    if saved:
        return saved[0], ""          # 返回首张作代表;同名 _NN 并排(共多张)
    return None, "图文图片下载失败(图片URL可能失效)"

def _xhs_ssr_direct(page, outbase):
    """⑥c 独立于 yt-dlp 的小红书直连兜底:requests.get 带 xsec_token 的 explore SSR 页,
    正则抠 __INITIAL_STATE__ 里 video.media.stream 的 h264[].masterUrl/backupUrls,裸 requests GET 直下落盘。
    注意:SSR 是小红书页面结构,改版即失效 → 如实报错(返回 None+原因),绝不拿封面冒充视频。"""
    import requests
    if not page or "xiaohongshu.com" not in page:
        return None, "非小红书页面"
    hdr = {"User-Agent": UA, "Referer": "https://www.xiaohongshu.com/"}
    try:
        html = requests.get(page, headers=hdr, timeout=40).text
    except Exception as e:
        return None, "SSR 取页失败:" + str(e)[:80]
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        return None, "SSR 无 __INITIAL_STATE__(页面改版/未登录)"
    raw = m.group(1).replace("undefined", "null")     # SSR 里常含裸 undefined,转成 JSON 合法的 null
    state = None
    try:
        state = json.loads(raw)
    except Exception:
        state = None
    cand = []
    if state is not None:                              # 优先结构化取 video.media.stream.h264[].masterUrl/backupUrls
        try:
            note = state.get("note") or {}
            note_map = note.get("noteDetailMap") or note.get("note") or {}
            for v in (note_map.values() if isinstance(note_map, dict) else []):
                nd = (v or {}).get("note") or v or {}
                stream = (((nd.get("video") or {}).get("media") or {}).get("stream")) or {}
                for codec in ("h264", "h265", "av1"):
                    for s in (stream.get(codec) or []):
                        for k in ("masterUrl", "backupUrls"):
                            x = s.get(k)
                            if isinstance(x, str):
                                cand.append(x)
                            elif isinstance(x, list):
                                cand += [u for u in x if isinstance(u, str)]
        except Exception:
            pass
    if not cand:                                       # 结构化失败再退到正则直抠 masterUrl/backupUrls(防 JSON 解析失败)
        cand = re.findall(r'"masterUrl"\s*:\s*"([^"]+)"', raw) + \
               re.findall(r'"backupUrls"\s*:\s*\[\s*"([^"]+)"', raw)
    cand = [u.replace("\\u002F", "/").replace("\\/", "/") for u in cand if u]
    if not cand:
        return None, "SSR 未抠到视频直链(可能是图文/改版)"
    out = outbase + ".mp4"
    ok, err = _dl_once(cand[0], hdr, out)              # 裸 GET 直下首个直链(_dl_once 自带 3 次重试)
    if ok:
        return out, ""
    return None, "SSR 直链下载失败:" + err


def dl_xiaohongshu(item, outbase):
    # 按 type 分流:图文(normal)直接下图片;视频(video)走 yt-dlp;失败再走 SSR 直连兜底;
    # token 过期的 video / 未知类型且有图 → 降级下图(不拿封面冒充视频)
    page = item.get("page") or item.get("url")
    info = _xhs_imgs_map().get(_xhs_note_id(item)) or {}
    t = info.get("t")
    if t == "normal":
        return dl_xhs_images(item, outbase)        # 图文 → 下图片(不试视频)
    fn, err = _ytdlp(page, outbase, ["--user-agent", UA])
    if fn:
        return fn, ""
    fn2, err2 = _xhs_ssr_direct(page, outbase)     # ⑥c yt-dlp 失败 → SSR 直连兜底(独立提取逻辑)
    if fn2:
        return fn2, ""
    # ② 放宽降级图片条件:token 过期的 video(yt-dlp 报 "No video formats")也能降级下图
    if (not t or "No video formats" in (err or "")) and info.get("imgs"):
        r = dl_xhs_images(item, outbase)
        if r[0]:
            return r
    return None, (err or err2 or "") + "(xhsToken可能过期,重收割刷新)"


def _set_ratio(u, r):
    return re.sub(r"ratio=[^&]*", "ratio=" + r, u) if "ratio=" in u else u + ("&" if "?" in u else "?") + "ratio=" + r


def _mp4_short_side(path):                   # 读 mp4 tkhd 量真实短边(不依赖 ffprobe);失败回 0
    import struct
    try:
        data = open(path, "rb").read()
    except Exception:
        return 0
    best, i = (0, 0), 0
    while True:
        j = data.find(b"tkhd", i)
        if j < 0:
            break
        try:
            sz = struct.unpack(">I", data[j - 4:j])[0]; box = data[j - 4:j - 4 + sz]
            if len(box) >= 8:
                w = struct.unpack(">I", box[-8:-4])[0] >> 16; hh = struct.unpack(">I", box[-4:])[0] >> 16
                if w * hh > best[0] * best[1]: best = (w, hh)
        except Exception:
            pass
        i = j + 4
    return min(best) if best[0] and best[1] else 0


def _dl_once(play, h, out):
    import requests
    err = ""
    for i in range(3):                       # douyinvod CDN 经韩国出口偶发 SSL/Proxy 抖动,必须重试退避
        try:
            resp = requests.get(play, headers=h, timeout=90, stream=True)
            resp.raise_for_status()
            with open(out, "wb") as f:
                for c in resp.iter_content(1 << 16):
                    if c: f.write(c)
            if os.path.getsize(out) > 1024:
                return True, ""
            err = "文件过小"
        except Exception as e:
            err = str(e)[:80]
        time.sleep(2 * (i + 1))
    return False, err


def dl_douyin(item, outbase):
    if dy_resolve is None:
        return None, "抖音不可用(没用 douyin venv python 跑?):" + _DY_ERR
    u = (item.get("page") or "") + " " + (item.get("url") or "")
    m = re.search(r"/video/(\d{10,})", u)
    if not m:
        return None, "page 里无 19位 video_id"
    vid = m.group(1)
    r = dy_resolve(vid)                      # 现解(verdicts 里缓存的 play 链已过期,必须现解)
    play = r.get("play")
    if not play:                             # ②轻兜底:空 play 时 sleep 5s 再现解一次(共 2 次)抗瞬时限流
        time.sleep(5)
        r = dy_resolve(vid)
        play = r.get("play")
    if not play:                             # ⑥d 0 成本兜底:仍空则回退试一次 item 自带 url(0 次新解析)
        play = (item.get("url") or "").strip() or None
    if not play:
        return None, "解析无 play(KR限流/改版):" + (r.get("err", "") or "")
    h = {**DY_HDR, "Referer": "https://www.douyin.com/"}
    out = outbase + ".mp4"
    # 要求:有更高清就不低于1080p。先取1080p,量真实分辨率;若<1080(约30%新闻片无1080p无水印转码,
    # 强抬会被甩到576p实验流)则回退到真720p,保留较大者。源即≤720p的视频两档相同,无副作用。
    ok, err = _dl_once(_set_ratio(play, "1080p"), h, out)
    if not ok:
        return None, "下载失败:" + err
    if _mp4_short_side(out) >= 1080:
        return out, ""
    alt = out + ".720"
    ok2, _ = _dl_once(_set_ratio(play, "720p"), h, alt)
    if ok2 and _mp4_short_side(alt) > _mp4_short_side(out):
        os.replace(alt, out)
    elif os.path.exists(alt):
        os.remove(alt)
    return out, ""


DOWNLOADERS = {"bilibili": dl_bilibili, "youtube": dl_youtube,
               "xiaohongshu": dl_xiaohongshu, "douyin": dl_douyin}


def xhs_download_warn(item):
    """小红书无类型映射(缺 xhs_imgs.json 或该 note 不在图里)时的告警:
    yt-dlp 无法区分图文/视频笔记,会把图文笔记下成平台自动合成的幻灯片 mp4(非静态图)。
    有 type(normal/video)→ 路由可靠,不告警;非小红书 → 不告警。"""
    if platform_of(item) != "xiaohongshu":
        return ""
    info = _xhs_imgs_map().get(_xhs_note_id(item)) or {}
    if info.get("t"):
        return ""
    return "无类型映射(缺xhs_imgs.json),图文笔记可能被下成幻灯片mp4——带imageList重收割小红书再下可得静态图"


MAX_FNAME = 70   # 含 .mp4 在内的文件名总长上限(字符数)


def native_id(item):
    """平台原生序列号(非自增、稳定不重复):复用 stable_id 抽出的平台 ID,去掉我们自己加的前缀。
    B站 BV 号本就是平台原生序列(stable_id 不加前缀,保留);抖音/小红书/YT/兜底去掉 dy_/xhs_/yt_/id_。"""
    sid = stable_id(item.get("page"), item.get("url"))
    return re.sub(r"^(dy_|xhs_|yt_|id_)", "", sid)


def build_name(item):
    """文件名(用户拍板 2026-06-13):{平台中文}_{平台原生ID}_{口播稿标题或素材标题},总长(含 .mp4)≤70。
    原生 ID 取平台自带那串字母/数字(B站 BVxxxx、抖音 19 位、小红书 hex、YT id),不再自增 01/02/03
    (自增会重复且破坏幂等)。副作用(好的):同一视频每次文件名一致 → process() 的 skip 幂等恢复有效。
    stable_id 仍作独立字段进 manifest/node2,不受文件名格式影响。
    mid=口播稿名(无则素材标题)经 sanitize,按剩余 budget 截断,保证无空格/emoji/#/乱码。"""
    plat = platform_of(item)
    pcn = PLAT_CN.get(plat, plat)
    nid = native_id(item)
    sn = (item.get("script_name") or "").strip()
    mid = sanitize(sn) if sn else sanitize(item.get("title"))
    fixed = len(pcn) + len(nid) + 2 + 4        # 平台名 + 原生ID + 两个 "_" + ".mp4"
    budget = max(MAX_FNAME - fixed, 4)
    mid = (mid[:budget] or "untitled")
    return f"{pcn}_{nid}_{mid}"


def process(item, outdir):
    plat = platform_of(item)
    os.makedirs(outdir, exist_ok=True)
    name = build_name(item)                  # 人能看懂:平台+原生ID_口播稿标题/素材标题(原生ID稳定→下方 skip 幂等有效)
    outbase = os.path.join(outdir, name)
    warn = xhs_download_warn(item)           # 小红书无类型映射 → 图文可能被下成幻灯片 mp4,告警(不阻断)
    existing = _find_output(outbase)         # 幂等:已下过就跳过
    if existing and os.path.getsize(existing) > 1024:
        return {"platform": plat, "status": "skip", "file": existing, "bytes": os.path.getsize(existing), "warn": warn}
    fn, err = DOWNLOADERS[plat](item, outbase)
    if fn:
        return {"platform": plat, "status": "ok", "file": fn, "bytes": os.path.getsize(fn), "warn": warn}
    return {"platform": plat, "status": "fail", "file": None, "bytes": 0, "error": err}


def manifest_append(item, res, outdir):
    rec = {"ts": int(time.time()), "platform": res["platform"],
           "stable_id": stable_id(item.get("page"), item.get("url")),
           "title": item.get("title", ""), "page": item.get("page", ""), "url": item.get("url", ""),
           "verdict": item.get("verdict", ""), "score": item.get("score", ""),
           "script_name": item.get("script_name", ""), "persona": item.get("persona", ""),
           "status": res["status"],
           "file": (os.path.basename(res["file"]) if res.get("file") else ""),
           "bytes": res.get("bytes", 0), "error": res.get("error", ""), "warn": res.get("warn", "")}
    with open(os.path.join(outdir, "_manifest.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _clean_item(sid, it):
    """投递 node2 的单条;name=可读文件名(平台+原生ID_稿名/标题.mp4;/item 上传时按真实后缀重定,图文→.jpg);
    name_prefix/persona 备注。stable_id 字段保留不动(去重/入库靠它,不受文件名格式影响)。
    name 由 build_name(it) 算,与真实下载用的 process(it,outdir) 同一逻辑,天然一致(原生ID 稳定无需预生成 seq)。"""
    return {"stable_id": sid, "platform": it.get("platform", ""),
            "title": it.get("title", ""), "verdict": it.get("verdict", ""),
            "score": it.get("score", ""),
            "name": build_name(it) + ".mp4",
            "name_prefix": (it.get("script_name") or "").strip(),
            "persona": it.get("persona", "")}


def resolve_outdir(raw):
    d = (raw or "").strip()
    d = os.path.expanduser(d) if d else DEFAULT_DIR
    if not os.path.isabs(d):                 # 相对路径锚到家目录,别落进项目目录
        d = os.path.join(HOME, d)
    return d


def open_folder(d):
    try:
        if sys.platform == "darwin": subprocess.Popen(["open", d])
        elif sys.platform.startswith("linux"): subprocess.Popen(["xdg-open", d])
    except Exception:
        pass


# ---- 悬浮预览代理(小红书):裸 <video> 直连 xhsCDN 因 CDN 不暴露 Content-Range 而卡死;
#      经本端点同源中转(补 Content-Range/Accept-Ranges)即可播。yt-dlp -j 现解 CDN 并缓存 page→url。
_CDN_CACHE = {}
_LAST_PICK = {"t": 0.0, "path": ""}   # /pickdir 3秒去重,防双弹文件选择器
_PICK_LOCK = threading.Lock()         # 多线程下保证同时只开一个文件选择框(下载/预览各自线程,不再阻塞选择框)
def resolve_preview(page):
    """返回 (可直拉的视频直链, 拉流额外请求头)。小红书→yt-dlp -j 取 CDN;抖音→DouyinProcessor 现解无水印 play_addr(下游 CDN 要带 Referer)。"""
    if not page: return None, {}
    if page in _CDN_CACHE: return _CDN_CACHE[page]
    m = re.search(r"/video/(\d{10,})", page)
    if "douyin.com" in page and m and dy_resolve:   # 抖音:现解 play_addr,带 Referer 才下得动字节 CDN
        try:
            url = (dy_resolve(m.group(1)) or {}).get("play")
        except Exception:
            url = None
        if url:
            res = (url, {"User-Agent": UA, "Referer": "https://www.douyin.com/"})
            _CDN_CACHE[page] = res; return res
        return None, {}
    try:                                            # 小红书等:yt-dlp 原生提取器拿 CDN 直链
        out = subprocess.run([YTDLP, "-j", "--no-warnings", "--no-playlist", page],
                             capture_output=True, text=True, timeout=60).stdout
        url = json.loads(out).get("url")
        if url:
            res = (url, {"User-Agent": UA}); _CDN_CACHE[page] = res; return res
    except Exception:
        pass
    return None, {}


# ---- 下前预检(契约3):不真拉整片,只试解直链/查映射,产 RES/dl_precheck.json ----
def _yt_simulate(target, use_cookies=False):
    """B站/YT 用 yt-dlp -j --simulate:只走 extractor 不下字节,看能否拿到 formats/url。
    B站海外 IP 无 cookie 必 412 → 与 dl_bilibili 一致带 --cookies-from-browser chrome,否则 keep 区 B站全被误标 fail。"""
    cmd = [YTDLP, "-j", "--simulate", "--no-warnings", "--no-playlist", "--socket-timeout", "20"]
    if use_cookies:
        cmd += ["--cookies-from-browser", "chrome", "--user-agent", UA,
                "--add-header", "Referer:https://www.bilibili.com/"]
    cmd += [target]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return {"ok": True, "reason": "预检超时(默认可下,真下时重试)", "level": "warn"}
    except Exception as e:
        return {"ok": False, "reason": "预检异常:" + str(e)[:80], "level": "fail"}
    out = (p.stdout or "").strip()
    if out:
        try:
            j = json.loads(out.splitlines()[0])
            if j.get("formats") or j.get("url"):
                return {"ok": True, "reason": "可解直链", "level": "ok"}
        except Exception:
            pass
        return {"ok": True, "reason": "可解(无 formats 字段,真下时由 yt-dlp 决定)", "level": "warn"}
    tail = (p.stderr or "").strip().splitlines()
    msg = tail[-1] if tail else ("rc=%d" % p.returncode)
    return {"ok": False, "reason": "extractor 失败:" + msg[:120], "level": "fail"}


def _precheck_item(item):
    """单条预检 → {"ok":bool,"reason":str,"level":"ok"|"warn"|"fail"}。同平台同出口成败一致,
    抖音烧额度故按平台抽样(此处只对被抽中的条目真解),小红书因 token 逐条判。"""
    plat = platform_of(item)
    page = item.get("page") or item.get("url") or ""
    if plat in ("bilibili", "youtube"):
        return _yt_simulate(page, use_cookies=(plat == "bilibili"))
    if plat == "xiaohongshu":
        info = _xhs_imgs_map().get(_xhs_note_id(item)) or {}
        t = info.get("t")
        if t == "normal":                            # 图文:零网络,查映射有图即可下
            if info.get("imgs"):
                return {"ok": True, "reason": "图文笔记,本地有图片URL映射", "level": "ok"}
            return {"ok": False, "reason": "图文笔记但无图片URL映射(带 imageList 重收割)", "level": "fail"}
        if not t:                                    # 无类型映射:警告(可能被下成幻灯片 mp4)
            return {"ok": True, "reason": "无类型映射(缺xhs_imgs.json),建议重收割小红书", "level": "warn"}
        cdn, _ = resolve_preview(page)               # 视频:用已有 resolve_preview 探 CDN 直链
        if cdn:
            return {"ok": True, "reason": "视频笔记,CDN 直链可解", "level": "ok"}
        return {"ok": False, "reason": "视频笔记无法解 CDN(xhsToken可能过期,重收割刷新)", "level": "fail"}
    if plat == "douyin":
        if dy_resolve is None:
            return {"ok": False, "reason": "抖音解析不可用(未用 douyin venv python 跑)", "level": "fail"}
        m = re.search(r"/video/(\d{10,})", page)
        if not m:
            return {"ok": False, "reason": "page 里无 19位 video_id", "level": "fail"}
        try:
            r = dy_resolve(m.group(1)) or {}
        except Exception as e:
            return {"ok": True, "reason": "抖音预解异常(真下时重试):" + str(e)[:60], "level": "warn"}
        if r.get("play"):
            return {"ok": True, "reason": "可解无水印 play", "level": "ok"}
        return {"ok": False, "reason": "解析无 play(KR限流/改版):" + (r.get("err", "") or ""), "level": "fail"}
    return {"ok": True, "reason": "未知平台,默认可下", "level": "warn"}


def run_precheck():
    """读 RES/verdicts.json(以 keep 区为主),对每条产预检结果,写 RES/dl_precheck.json。
    抖音按平台抽样 1~2 条真解(其余沿用抽样结论,省解析额度);其他平台逐条。25s 单条超时、16 并发。"""
    try:
        verds = json.load(open(os.path.join(RES, "verdicts.json"), encoding="utf-8"))
    except Exception as e:
        return {"_error": "读 verdicts.json 失败:" + str(e)[:120]}
    if not isinstance(verds, list):
        verds = []
    # 以 keep 区为主(无 keep 才退而预检全部,避免空跑)
    keeps = [it for it in verds if (it.get("verdict") or "") == "keep"]
    targets = keeps or verds
    # 抖音抽样:同出口成败一致,真解前 2 条,其余复用抽样结论(烧额度/触限流→抽样)
    dy_items = [it for it in targets if platform_of(it) == "douyin"]
    dy_sample = dy_items[:2]
    other_items = [it for it in targets if platform_of(it) != "douyin"]

    out = {}
    out_lock = threading.Lock()

    def _do(item):
        sid = stable_id(item.get("page"), item.get("url"))
        res = _precheck_item(item)
        with out_lock:
            out[sid] = res

    # 抖音:只真解抽样条,得抽样结论;其余抖音条目复用该结论(不再真解)
    dy_verdict = None
    if dy_sample:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(lambda it: out.__setitem__(stable_id(it.get("page"), it.get("url")), _precheck_item(it)),
                        dy_sample))
        # 抽样里只要有一条 ok,就认为抖音出口可用(沿用给其余条目)
        sample_results = [out[stable_id(it.get("page"), it.get("url"))] for it in dy_sample]
        if any(s.get("ok") for s in sample_results):
            dy_verdict = {"ok": True, "reason": "抖音抽样可解(本条沿用抽样结论)", "level": "warn"}
        else:
            dy_verdict = {"ok": False, "reason": "抖音抽样均不可解(KR限流/改版,本条沿用)", "level": "fail"}
    for it in dy_items:
        sid = stable_id(it.get("page"), it.get("url"))
        if sid not in out and dy_verdict is not None:
            out[sid] = dict(dy_verdict)

    # 其他平台:逐条 16 并发
    if other_items:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            futs = [ex.submit(_do, it) for it in other_items]
            for fu in concurrent.futures.as_completed(futs):
                try:
                    fu.result()
                except Exception:
                    pass
    try:
        with open(os.path.join(RES, "dl_precheck.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:
        pass
    return out


def _post_fail_item(job_id, sid, hdr):
    """向 node2 回执某条 failed(3 次重试退避)。模块级:handler 方法与信号处理器共用。"""
    import requests
    url = CLEAN_BASE + "/api/clean/%s/item?status=failed&stable_id=%s" % (
        job_id, urllib.parse.quote(sid))
    for k in range(3):
        try:
            if requests.post(url, headers=hdr, timeout=30).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2 * (k + 1))
    return False


def _drain_inflight_on_signal(signum, frame):
    """④ SIGTERM/SIGINT:退出前对当前 handle_clean 未确认的 sid 批量补一轮 _fail_item,
    把被 kill 的孤儿 loading 降级为 failed(可重试),再正常退出。"""
    try:
        with _inflight_lock:
            jobs = list(_inflight.items())
        for job_id, (sids, hdr) in jobs:
            for sid in list(sids):
                try:
                    _post_fail_item(job_id, sid, hdr)
                except Exception:
                    pass
    finally:
        try:                                 # os._exit 不触发 atexit,这里显式删端口旁车
            if _DLPORT_FILE and os.path.exists(_DLPORT_FILE):
                os.remove(_DLPORT_FILE)
        except Exception:
            pass
        os._exit(0)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # 末尾关连接即表示流结束,fetch 可边读边显示

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def _pickdir(self):
        # 唤起本机原生「选择文件夹」对话框(macOS osascript),回传所选目录的 POSIX 路径(取消则空串)
        # 3 秒去重:同一次操作若被双触发/重试,直接回上次结果,绝不弹第二个框
        now = time.time()
        if now - _LAST_PICK["t"] < 3 or not _PICK_LOCK.acquire(blocking=False):
            path = _LAST_PICK["path"]              # 3秒内重复 / 已有框开着 → 回上次,绝不弹第二个框
        else:
            try:
                path = ""
                try:
                    script = ('POSIX path of (choose folder with prompt "选择素材下载文件夹" '
                              'default location (path to downloads folder))')
                    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
                    path = (r.stdout or "").strip()
                except Exception:
                    path = ""
                _LAST_PICK["t"] = time.time(); _LAST_PICK["path"] = path
            finally:
                _PICK_LOCK.release()
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
        try: self.wfile.write(path.encode("utf-8"))
        except Exception: pass

    def _precheck(self):
        # 契约3:读 RES/verdicts.json 产 RES/dl_precheck.json,回 JSON 对象(键=stable_id)
        try:
            out = run_precheck()
        except Exception as e:
            out = {"_error": "预检异常:" + str(e)[:120]}
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers()
        try:
            self.wfile.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/ping":
            # 契约2:返回本会话 RES 选题目录名(纯文本),供出页校验"页面连的是不是自己这个 server"
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
            try: self.wfile.write(os.path.basename(RES).encode("utf-8"))
            except Exception: pass
            return
        if parsed.path == "/precheck":
            return self._precheck()
        if parsed.path == "/pickdir":
            return self._pickdir()
        if parsed.path != "/preview":
            self.send_response(404); self._cors(); self.end_headers(); return
        if (q.get("warm") or [""])[0]:           # 滚动预解析:只现解+缓存 CDN,不拉流
            resolve_preview((q.get("page") or [""])[0])
            self.send_response(200); self._cors(); self.end_headers()
            try: self.wfile.write(b"warmed")
            except Exception: pass
            return
        direct = (q.get("url") or [""])[0]
        if direct:
            cdn, up_hdr = direct, {"User-Agent": UA}
        else:
            cdn, up_hdr = resolve_preview((q.get("page") or [""])[0])
        if not cdn:
            self.send_response(502); self._cors(); self.end_headers()
            try: self.wfile.write(b"resolve failed")
            except Exception: pass
            return
        hdr = dict(up_hdr)
        rng = self.headers.get("Range")
        if rng: hdr["Range"] = rng
        try:
            up = urllib.request.urlopen(urllib.request.Request(cdn, headers=hdr), timeout=30)
        except urllib.error.HTTPError as e:
            up = e            # 416 等以 HTTPError 形式回来,仍可转发状态/体
        except Exception:
            self.send_response(502); self._cors(); self.end_headers(); return
        self.send_response(getattr(up, "status", None) or up.getcode())
        self._cors()
        for h in ("Content-Type", "Content-Range", "Content-Length"):
            v = up.headers.get(h)
            if v: self.send_header(h, v)
        self.send_header("Accept-Ranges", "bytes")   # 关键:补上 CDN 没暴露的,媒体元素才能起播
        self.end_headers()
        try:
            while True:
                chunk = up.read(1 << 16)
                if not chunk: break
                self.wfile.write(chunk)
        except Exception:
            pass

    def _line(self, obj):
        # 线程安全:并发清洗时多个 worker 线程同时回写进度,需加锁避免 NDJSON 行交错/半行。
        # 锁由 handle_clean 在起线程池【之前】一次性建好(self._wlock);单线程路径(/download)不建锁、直接写。
        # 不在此惰性建锁——并发首次进入会各建一把互相覆盖、锁形同虚设。
        lock = getattr(self, "_wlock", None)
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        if lock is None:
            try:
                self.wfile.write(data); self.wfile.flush()
            except Exception:
                pass
            return
        with lock:
            try:
                self.wfile.write(data); self.wfile.flush()
            except Exception:
                pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self.send_response(400); self._cors(); self.end_headers()
            self.wfile.write(("bad json: " + str(e)).encode()); return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/cancel_clean":
            # ③ 取消(契约5):body {"job_id":...} → 置该 job 取消 Event;handle_clean 主循环每轮开头检查到则停投递
            jid = (body.get("job_id") if isinstance(body, dict) else None) or ""
            with _cancel_lock:
                ev = _cancel_flags.get(jid)
                if ev is not None:
                    ev.set()
            known = ev is not None
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers()
            try:
                self.wfile.write(json.dumps({"ok": True, "job_id": jid,
                                             "known": known}, ensure_ascii=False).encode("utf-8"))
            except Exception:
                pass
            return
        if parsed.path == "/clean":
            if isinstance(body, dict):
                citems, coutdir = (body.get("items") or []), resolve_outdir(body.get("dir"))
            else:
                citems, coutdir = (body or []), DEFAULT_DIR
            os.makedirs(coutdir, exist_ok=True)
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8"); self.end_headers()
            return self.handle_clean(citems, coutdir)
        if isinstance(body, list):           # 兼容老格式:纯数组
            items, outdir = body, DEFAULT_DIR
        elif isinstance(body, dict):
            items, outdir = (body.get("items") or []), resolve_outdir(body.get("dir"))
        else:
            items, outdir = [], DEFAULT_DIR
        os.makedirs(outdir, exist_ok=True)
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.end_headers()
        total = len(items); ok = skip = fail = 0
        self._line({"event": "start", "total": total, "dir": outdir})
        for i, item in enumerate(items):
            plat = platform_of(item)
            try:
                res = process(item, outdir)
            except Exception as e:
                res = {"platform": plat, "status": "fail", "file": None, "bytes": 0, "error": str(e)[:160]}
            manifest_append(item, res, outdir)
            if res["status"] == "ok": ok += 1
            elif res["status"] == "skip": skip += 1
            else: fail += 1
            self._line({"event": "item", "i": i + 1, "total": total,
                        "platform": res["platform"], "status": res["status"],
                        "title": (item.get("title") or "")[:50],
                        "mb": round(res.get("bytes", 0) / 1048576, 2), "error": res.get("error", ""),
                        "warn": res.get("warn", "")})
        self._line({"event": "done", "ok": ok, "skip": skip, "fail": fail, "total": total, "dir": outdir})
        if ok + skip > 0:
            open_folder(outdir)              # 下完自动弹开 Finder 文件夹

    def _delete_local(self, outbase):
        # 上传成功后删本地过路件:按 outbase 前缀删全部同名产物(覆盖图文 _01/_02 多张 + .mp4 + 临时档)
        try:
            for f in glob.glob(glob.escape(outbase) + "*"):
                try:
                    if os.path.isfile(f):
                        os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass

    def handle_clean(self, items, outdir):
        import requests
        # ① 磁盘隔离:清洗过路件落 outdir/.clean_tmp/<job_id>(点开头 Finder 隐藏),与 /download 留档物理隔离。
        #   按 job_id 分子目录 → 多会话/多批次同选题并发也永不撞名误删;tmpdir 在拿到 job_id 后才定(见下)。
        seen = {}
        for it in items:                                  # 按 stable_id 批内去重
            sid = stable_id(it.get("page"), it.get("url"))
            seen.setdefault(sid, it)
        pairs = list(seen.items())
        # ⑤ 文件名:用平台原生ID(稳定不重复),投 node2 的 name 与真实下载用的 name 同 build_name 逻辑,天然一致
        hdr = {"X-Clean-Token": CLEAN_TOKEN} if CLEAN_TOKEN else {}
        payload = {"topic": os.path.basename(outdir.rstrip("/")) or "broll",
                   "items": [_clean_item(sid, it) for sid, it in pairs]}
        try:
            r = requests.post(CLEAN_BASE + "/api/clean/start", json=payload, headers=hdr, timeout=30)
            job_id = r.json()["job_id"]
        except Exception as e:
            self._line({"event": "error", "error": "建清洗任务失败:" + str(e)[:140]}); return
        clean_url = CLEAN_BASE + "/?job=" + job_id
        self._line({"event": "start", "job_id": job_id, "total": len(pairs), "url": clean_url})

        # ① 本 job 专属过路目录(job_id 命名空间:多会话/多批同选题并发不撞名,_delete_local 只命中本批)
        tmpdir = os.path.join(outdir, ".clean_tmp", job_id[:8])
        os.makedirs(tmpdir, exist_ok=True)
        self._wlock = threading.Lock()      # ⑥ 起线程池【前】一次性建写锁(_line 并发安全;不在 _line 里惰性建)

        # ③ 取消:为本 job 注册取消 Event(/cancel_clean 收到 job_id 即置位)
        flag = threading.Event()
        with _cancel_lock:
            _cancel_flags[job_id] = flag
        # ④ 在途未确认 sid 登记(收 SIGTERM/SIGINT 时遍历补发 _fail_item,孤儿 loading→failed 可重试)
        unconfirmed = set(sid for sid, _ in pairs)
        with _inflight_lock:
            _inflight[job_id] = (unconfirmed, hdr)

        pending = []
        pending_lock = threading.Lock()
        done_n = {"v": 0}

        def _work(idx, sid, it):
            # 派发前再查一次取消 flag(已取消则不下载、不上传,直接降级 failed 收尾)
            if flag.is_set():
                self._fail_item(job_id, sid, hdr)
                with _inflight_lock:
                    unconfirmed.discard(sid)
                return
            outbase = os.path.join(tmpdir, build_name(it))
            try:
                res = process(it, tmpdir)
            except Exception as e:
                res = {"platform": platform_of(it), "status": "fail", "file": None, "error": str(e)[:160]}
            with pending_lock:
                done_n["v"] += 1
                i = done_n["v"]
            if res["status"] in ("ok", "skip") and res.get("file"):
                up_ok = self._upload_item(job_id, sid, res["file"], hdr)
                if up_ok:
                    self._delete_local(outbase)          # ① 上传成功 → 删本地全部同名产物(本地零留存)
                else:
                    with pending_lock:
                        pending.append(sid)              # 上传失败 → 保留本地 + 入 pending 补发 failed
                self._line({"event": "item", "i": i, "total": len(pairs), "stable_id": sid,
                            "status": "uploaded" if up_ok else "upload_fail",
                            "title": (it.get("title") or "")[:50], "warn": res.get("warn", "")})
            else:
                f_ok = self._fail_item(job_id, sid, hdr)
                if not f_ok:
                    with pending_lock:
                        pending.append(sid)
                self._line({"event": "item", "i": i, "total": len(pairs), "stable_id": sid,
                            "status": "download_fail", "error": res.get("error", ""),
                            "title": (it.get("title") or "")[:50]})
            with _inflight_lock:
                unconfirmed.discard(sid)                 # 已确认(上传/失败回执都发过)→ 移出在途

        try:
            # ⑥ 串行改并发:单条卡 600/900s 不再阻塞全批;_line 已加锁线程安全
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                futs = []
                for idx, (sid, it) in enumerate(pairs):
                    if flag.is_set():
                        # ③ 已取消:未派发的直接走 _fail_item 收尾,不再下载
                        self._fail_item(job_id, sid, hdr)
                        with _inflight_lock:
                            unconfirmed.discard(sid)
                        continue
                    futs.append(ex.submit(_work, idx, sid, it))
                for fu in concurrent.futures.as_completed(futs):
                    try:
                        fu.result()
                    except Exception:
                        pass
            for sid in pending:                           # 收尾补发:未确认条目再回一轮 failed(防二阶永 loading)
                self._fail_item(job_id, sid, hdr)
        finally:
            with _cancel_lock:
                _cancel_flags.pop(job_id, None)
            with _inflight_lock:
                _inflight.pop(job_id, None)
        self._line({"event": "done", "job_id": job_id, "url": clean_url})

    def _upload_item(self, job_id, sid, path, hdr):
        import requests
        url = CLEAN_BASE + "/api/clean/%s/item" % job_id
        fname = os.path.basename(path)                    # 真实文件名(含正确后缀:视频.mp4/图文.jpg)→ matclean 据此重定 key
        for k in range(3):
            try:
                with open(path, "rb") as fh:
                    r = requests.post(url, files={sid: (fname, fh, "application/octet-stream")},
                                      data={"name": fname}, headers=hdr, timeout=900)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(2 * (k + 1))
        return False

    def _fail_item(self, job_id, sid, hdr):
        return _post_fail_item(job_id, sid, hdr)

    def log_message(self, *a): pass


def _bind_server():
    """④ 多开:从 PORT 起 +1 向上探测第一个可 bind 的空闲端口(试约 10 个到 PORT+10)。
    返回 (httpd, 实际端口);全占用则抛 OSError。"""
    last = None
    for p in range(PORT, PORT + 11):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), H), p
        except OSError as e:
            last = e
            continue
    raise last or OSError("no free port in [%d, %d]" % (PORT, PORT + 10))


if __name__ == "__main__":
    # ① 启动兜底:清掉【陈旧】的 .clean_tmp/<job> 过路件(>24h)。
    #   ④ 多开常态:绝不无差别 rmtree 整棵 .clean_tmp——并发会话的 job 子目录是分钟级,删它会毁掉对方在途下载;
    #   只删 mtime>24h 的 job 子目录(活跃会话绝不可能这么旧),既回收崩溃残留又不误伤并行会话。
    try:
        cutoff = time.time() - 24 * 3600
        for base in glob.glob(os.path.join(DEFAULT_DIR, "**", ".clean_tmp"), recursive=True):
            if not os.path.isdir(base):
                continue
            for sub in glob.glob(os.path.join(base, "*")):
                try:
                    if os.path.getmtime(sub) < cutoff:
                        shutil.rmtree(sub, ignore_errors=True) if os.path.isdir(sub) else os.remove(sub)
                except Exception:
                    pass
            try:
                if not os.listdir(base):        # 空的 .clean_tmp 顺手清掉
                    os.rmdir(base)
            except Exception:
                pass
    except Exception:
        pass

    httpd, actual_port = _bind_server()

    # ④ 端口旁车(契约1):写 RES/.dlport,atexit 删之;出页同源 fetch('.dlport') 读真实端口
    _DLPORT_FILE = os.path.join(RES, ".dlport")
    try:
        os.makedirs(RES, exist_ok=True)
        with open(_DLPORT_FILE, "w", encoding="utf-8") as f:
            f.write(str(actual_port))
    except Exception:
        pass

    def _cleanup_sidecar():
        try:
            if _DLPORT_FILE and os.path.exists(_DLPORT_FILE):
                os.remove(_DLPORT_FILE)
        except Exception:
            pass
    atexit.register(_cleanup_sidecar)

    # ④ SIGTERM/SIGINT:退出前把在途未确认 sid 降级为 failed(被 kill 不再留孤儿 loading)
    try:
        signal.signal(signal.SIGTERM, _drain_inflight_on_signal)
        signal.signal(signal.SIGINT, _drain_inflight_on_signal)
    except Exception:
        pass

    print(f"download_server on 127.0.0.1:{actual_port}  默认下载目录={DEFAULT_DIR}", flush=True)
    print(f"DOWNLOAD_PORT={actual_port}", flush=True)   # 机器可读:供调用方/出页发现真实端口
    print(f"  yt-dlp={YTDLP}  ffmpeg={FFMPEG}", flush=True)
    print(f"  抖音解析={'就绪' if dy_resolve else '不可用(' + _DY_ERR + ')——用 douyin venv python 跑'}", flush=True)
    httpd.serve_forever()
