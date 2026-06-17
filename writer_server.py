#!/usr/bin/env python3
"""浏览器→磁盘 的稳健桥:本地 loopback 写服务(替代撞焦点/会卡死的 clipboard)。
页面里 fetch('http://127.0.0.1:<port>/save?f=NAME',{method:'POST',body:...}) → 写到 BROLL_RES/NAME。
Chrome 对 loopback 免 mixed-content 拦截;CORS 全开;text/plain 不触发预检。
端口自适应:从 WRITER_PORT(默认 8799)起向上探测空闲端口,实际端口写 BROLL_RES/.writerport
(收割 JS 同源读取此旁车拼 fetch,别硬编码 8799),atexit 退出删之;stdout 打印 WRITER_PORT=<n>。
跑:BROLL_RES=<dir> python3 writer_server.py  (run_in_background)
"""
import os, atexit, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
RES = os.environ.get("BROLL_RES") or "results"
PORT = int(os.environ.get("WRITER_PORT", "8799"))

class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        # 新版 Chrome 对 公网域名页面→loopback 强制 Private Network Access 预检,
        # 必须回这个头,否则 fetch 直接 "Failed to fetch"(收割落盘桥全废)。向后兼容、不需要的浏览器忽略。
        self.send_header("Access-Control-Allow-Private-Network", "true")
    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()
    def do_POST(self):
        q = urllib.parse.urlparse(self.path).query
        name = urllib.parse.parse_qs(q).get("f", ["clip_dump.json"])[0]
        name = os.path.basename(name)  # no path traversal
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        open(os.path.join(RES, name), "wb").write(data)
        self.send_response(200); self._cors(); self.end_headers()
        self.wfile.write(b"ok:" + str(len(data)).encode())
    def log_message(self, *a): pass

# 端口自适应:多会话并行时各起各的,从 WRITER_PORT 起向上探测第一个空闲端口(撞端口 +1,试 ~10 个),
# 绑定成功后写旁车 RES/.writerport(纯一行端口号,供收割 JS 同源读取),atexit 退出删之。
srv = None
for _p in range(PORT, PORT + 10):
    try:
        srv = HTTPServer(("127.0.0.1", _p), H)
        PORT = _p
        break
    except OSError:
        continue
if srv is None:
    raise SystemExit(f"writer_server: no free port in {PORT}..{PORT+9}")

_sidecar = os.path.join(RES, ".writerport")
try:
    os.makedirs(RES, exist_ok=True)
    with open(_sidecar, "w") as _f:
        _f.write(str(PORT))
    atexit.register(lambda: os.path.exists(_sidecar) and os.remove(_sidecar))
except OSError:
    pass

print(f"writer_server on 127.0.0.1:{PORT} → {RES}", flush=True)
print(f"WRITER_PORT={PORT}", flush=True)
srv.serve_forever()
