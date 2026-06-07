#!/usr/bin/env python3
"""撞码时:发 macOS 系统通知(带声音)+ 入待办队列。
**Claude 永不解码**——本脚本只负责"喊用户来解",不做任何识别/点选/绕过。
用法: BROLL_RES=<dir> python3 captcha_notify.py <platform> <type> <prompt> <tabId> [shot_path]
解完后用户回一声 → Claude 跑 detect 确认已清 → 从 _progress.json 断点续跑。
"""
import sys, json, os, subprocess

RES = os.environ.get("BROLL_RES") or "results"
a = sys.argv + [""] * 6
platform, ctype, prompt, tab, shot = a[1] or "?", a[2] or "captcha", a[3], a[4], a[5]

title = f"🧩 验证码待解 · {platform}"
msg = f"{ctype}:{prompt}".strip("：: ")[:180] + f" — tab {tab},解完回 Claude 续跑"

# macOS 系统通知(argv 传参,免引号注入);失败不阻塞
try:
    subprocess.run(["osascript",
        "-e", "on run argv",
        "-e", 'display notification (item 1 of argv) with title (item 2 of argv) sound name "Glass"',
        "-e", "end run", msg, title], timeout=10)
except Exception as e:
    print("⚠️ osascript 通知失败(非 macOS?):", e)

# 入待办队列(你定期扫,不必实时盯)
os.makedirs(RES, exist_ok=True)
rec = {"platform": platform, "type": ctype, "prompt": prompt, "tab": tab, "shot": shot, "status": "pending"}
with open(os.path.join(RES, "captcha_queue.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("已通知 + 入队:", json.dumps(rec, ensure_ascii=False))
