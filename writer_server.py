#!/usr/bin/env python3
"""浏览器→磁盘 的稳健桥:本地 loopback 写服务(替代撞焦点/会卡死的 clipboard)。
页面里 fetch('http://127.0.0.1:8799/save?f=NAME',{method:'POST',body:...}) → 写到 BROLL_RES/NAME。
Chrome 对 loopback 免 mixed-content 拦截;CORS 全开;text/plain 不触发预检。
跑:BROLL_RES=<dir> python3 writer_server.py  (run_in_background)
"""
import os, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
RES = os.environ.get("BROLL_RES") or "results"
PORT = int(os.environ.get("WRITER_PORT", "8799"))

class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
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

print(f"writer_server on 127.0.0.1:{PORT} → {RES}", flush=True)
HTTPServer(("127.0.0.1", PORT), H).serve_forever()
