#!/usr/bin/env python3
"""选片→下载 的 loopback 端点(方案A)。filtered.html 勾选→POST /download→按平台分流下载到本地。
仿 writer_server:Chrome 对 127.0.0.1 免 mixed-content;CORS 全开;前端用 text/plain 免预检。
按平台分流(全用已装工具,零新依赖):
  - B站(bilibili.com):yt-dlp + ffmpeg + --cookies-from-browser chrome + 桌面UA + Referer(没 cookie 海外IP必 412;带 cookie 解锁到 1080P)
  - YouTube(youtube.com/youtu.be):yt-dlp(韩国出口IP 实际只拿到 360p / 403,硬限制)
  - 小红书(xiaohongshu.com):yt-dlp 原生提取器(走 explore SSR,绕开被封的搜索API;喂带 xsec_token 的 page;原生 avc1 mp4)
  - 抖音(douyin.com):从 page 的 19位 video_id 现解无水印URL(iesdouyin)+ requests 带 Referer 下载(3次重试抗CDN抖动);yt-dlp 下不了抖音
落地目录由前端传 `dir`(用户可在页面顶部改),默认 `~/Downloads/af素材/<选题>/`;文件名 `<平台>_<标题前40>_<稳定ID>.<ext>`(人能看懂)。
台账写 `<dir>/_manifest.jsonl`(= 后续入库知识库的对接口)。下完**自动打开文件夹**。NDJSON 流式回进度。
**必须用 douyin venv python 跑**(它有 requests 给抖音直下,又能 subprocess 出 yt-dlp):
  BROLL_RES=<dir> ~/.local/share/uv/tools/douyin-mcp-server/bin/python download_server.py   (run_in_background)
"""
import os, re, sys, json, time, threading, subprocess, urllib.request, urllib.parse, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
PORT = int(os.environ.get("DOWNLOAD_PORT", "8788"))
DEFAULT_DIR = os.path.expanduser(os.environ.get("DOWNLOAD_DIR") or "~/Downloads/af素材")
PLAT_CN = {"bilibili": "B站", "youtube": "YouTube", "xiaohongshu": "小红书", "douyin": "抖音"}

HOME = os.path.expanduser("~")

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


def sanitize(s, n=40):
    s = re.sub(r'[/\\:*?"<>|\n\r\t]+', "_", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip("_ .")
    return s[:n] or "untitled"


def stable_id(page, url):
    u = (page or "") + " " + (url or "")
    m = re.search(r"/video/(\d{10,})", u)                         # 抖音
    if m: return "dy_" + m.group(1)
    m = re.search(r"/explore/([0-9a-fA-F]{12,})", u)              # 小红书
    if m: return "xhs_" + m.group(1)
    m = re.search(r"/(BV[0-9A-Za-z]{8,})", u)                     # B站
    if m: return m.group(1)
    m = re.search(r"[?&]v=([\w-]{6,})", u) or re.search(r"youtu\.be/([\w-]{6,})", u)  # YT
    if m: return "yt_" + m.group(1)
    return "id_" + str(abs(hash(u)) % (10 ** 10))


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
    return _ytdlp(item.get("page") or item.get("url"), outbase, [
        "--cookies-from-browser", "chrome", "--user-agent", UA,
        "--add-header", "Referer:https://www.bilibili.com/",
        "-f", "bv*[vcodec^=avc1][height<=1080]+ba/bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4"])


def dl_youtube(item, outbase):
    # 韩国出口 IP 下 YT 只见 fmt18(360p)且数据下载被 403 封 —— 硬限制,需在国内节点跑才有高清。
    fn, err = _ytdlp(item.get("page") or item.get("url"), outbase, [
        "--user-agent", UA,
        "-f", "bv*[vcodec^=avc1][height<=1080]+ba/18/b[height<=1080]/b",
        "--merge-output-format", "mp4"])
    if not fn and ("403" in (err or "") or "Invalid data" in (err or "") or "segment" in (err or "").lower()):
        err = "YouTube 拒绝自动化下载媒体(403/反爬,请求被标 gcr=cn;非地域封锁)→ 走浏览器节点下"
    return fn, err


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

def dl_xiaohongshu(item, outbase):
    # 按 type 分流:图文(normal)直接下图片;视频(video)走 yt-dlp 失败就如实失败(不拿封面冒充视频);未知类型才兜底下图片
    info = _xhs_imgs_map().get(_xhs_note_id(item)) or {}
    t = info.get("t")
    if t == "normal":
        return dl_xhs_images(item, outbase)        # 图文 → 下图片(不试视频)
    fn, err = _ytdlp(item.get("page") or item.get("url"), outbase, ["--user-agent", UA])
    if fn:
        return fn, ""
    if not t and info.get("imgs"):                 # 仅"未知类型"且有图才兜底图片;video 失败就报错(多半 token 过期,重收割刷新)
        r = dl_xhs_images(item, outbase)
        if r[0]:
            return r
    return None, err


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


def process(item, outdir):
    plat = platform_of(item)
    sid = stable_id(item.get("page"), item.get("url"))
    os.makedirs(outdir, exist_ok=True)
    name = f"{PLAT_CN.get(plat, plat)}_{sanitize(item.get('title'))}_{sid}"   # 人能看懂:平台_标题_ID
    outbase = os.path.join(outdir, name)
    existing = _find_output(outbase)         # 幂等:已下过就跳过
    if existing and os.path.getsize(existing) > 1024:
        return {"platform": plat, "status": "skip", "file": existing, "bytes": os.path.getsize(existing)}
    fn, err = DOWNLOADERS[plat](item, outbase)
    if fn:
        return {"platform": plat, "status": "ok", "file": fn, "bytes": os.path.getsize(fn)}
    return {"platform": plat, "status": "fail", "file": None, "bytes": 0, "error": err}


def manifest_append(item, res, outdir):
    rec = {"ts": int(time.time()), "platform": res["platform"],
           "stable_id": stable_id(item.get("page"), item.get("url")),
           "title": item.get("title", ""), "page": item.get("page", ""), "url": item.get("url", ""),
           "verdict": item.get("verdict", ""), "score": item.get("score", ""),
           "status": res["status"],
           "file": (os.path.basename(res["file"]) if res.get("file") else ""),
           "bytes": res.get("bytes", 0), "error": res.get("error", "")}
    with open(os.path.join(outdir, "_manifest.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
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
        try:
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self.send_response(400); self._cors(); self.end_headers()
            self.wfile.write(("bad json: " + str(e)).encode()); return
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
            try:
                res = process(item, outdir)
            except Exception as e:
                res = {"platform": platform_of(item), "status": "fail", "file": None, "bytes": 0, "error": str(e)[:160]}
            manifest_append(item, res, outdir)
            if res["status"] == "ok": ok += 1
            elif res["status"] == "skip": skip += 1
            else: fail += 1
            self._line({"event": "item", "i": i + 1, "total": total,
                        "platform": res["platform"], "status": res["status"],
                        "title": (item.get("title") or "")[:50],
                        "mb": round(res.get("bytes", 0) / 1048576, 2), "error": res.get("error", "")})
        self._line({"event": "done", "ok": ok, "skip": skip, "fail": fail, "total": total, "dir": outdir})
        if ok + skip > 0:
            open_folder(outdir)              # 下完自动弹开 Finder 文件夹

    def log_message(self, *a): pass


if __name__ == "__main__":
    print(f"download_server on 127.0.0.1:{PORT}  默认下载目录={DEFAULT_DIR}", flush=True)
    print(f"  yt-dlp={YTDLP}  ffmpeg={FFMPEG}", flush=True)
    print(f"  抖音解析={'就绪' if dy_resolve else '不可用(' + _DY_ERR + ')——用 douyin venv python 跑'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
