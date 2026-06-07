#!/usr/bin/env python3
"""撞码【强提醒】:置顶模态弹窗(带声、不点不消、可重弹)+ QuickLook 弹出验证码截图 + 一键聚焦到【真】验证码标签页。
**Claude/本脚本永不解码**:弹窗只"看图 + 把你一键带到真页面",绝不收答案/回填/relay 坐标(那是 bot-detection 绕过,禁止,且 relay 的点击更像机器人会害你号)。
用户在真实页面亲解后点「我已解决」→ 写 captcha_solved.flag → 收割主循环下一回合 check flag + 跑 detect_captcha 确认已清 → 断点续跑。

非阻塞用法(由收割主循环 run_in_background 起,不卡流水线):
  BROLL_RES=<dir> python3 captcha_alert.py <platform> <type> <prompt> <shot_path> <tab_url_substr>
"""
import sys, os, json, time, subprocess, signal

a = sys.argv + [""] * 6
platform, ctype, prompt, shot, taburl = a[1] or "?", a[2] or "captcha", a[3], a[4], a[5]
RES = os.environ.get("BROLL_RES") or "results"
os.makedirs(RES, exist_ok=True)
QUEUE = os.path.join(RES, "captcha_queue.jsonl")
FLAG = os.path.join(RES, "captcha_solved.flag")

def log(status):
    with open(QUEUE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"platform": platform, "type": ctype, "prompt": prompt,
                            "tab": taburl, "shot": shot, "status": status, "ts": time.time()}, ensure_ascii=False) + "\n")

def show_image():
    """用 Preview 打开验证码截图并置前(qlmanage -p 从后台拉起常不显示窗,弃用)。"""
    if shot and os.path.exists(shot):
        try:
            subprocess.Popen(["open", "-a", "Preview", shot], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.6)
            subprocess.Popen(["osascript", "-e", 'tell application "Preview" to activate'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            try: subprocess.Popen(["open", shot])
            except Exception: pass

def beep():
    try: subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass

def focus_tab():
    """一键把用户带到真验证码标签页(只切前台,不在页面上做任何动作)。"""
    sub = taburl or "captcha"
    osa = '''on run argv
set sub to item 1 of argv
tell application "Google Chrome"
  activate
  repeat with w in windows
    set i to 0
    repeat with t in tabs of w
      set i to i + 1
      if (URL of t) contains sub then
        set active tab index of w to i
        set index of w to 1
        return "focused"
      end if
    end repeat
  end repeat
end tell
return "tab-not-found"
end run'''
    try: subprocess.run(["osascript", "-e", osa, sub], timeout=15)
    except Exception: pass

def dialog():
    body = (f"{ctype}\n{prompt}\n\n请在 Chrome【本人】解这个验证码;解完点「我已解决」我自动续跑。\n"
            f"(我不替你点/输——relay 会让你的号被风控标记得更狠,真页面亲解最安全)")
    osa = '''on run argv
display dialog (item 1 of argv) with title (item 2 of argv) buttons {"稍后", "去解·聚焦标签页", "我已解决"} default button "去解·聚焦标签页" with icon caution giving up after 30
return button returned of result
end run'''
    try:
        r = subprocess.run(["osascript", "-e", osa, body, f"🧩 验证码挡住了 · {platform}"],
                           capture_output=True, text=True, timeout=120)
        return (r.stdout or "").strip()
    except Exception:
        return ""

show_image()
log("pending")
rounds = 0
while rounds < 30:
    beep()
    btn = dialog()                       # 超时(giving up after 30s)返回空 → 视作未处理,重弹
    if btn == "我已解决":
        open(FLAG, "w").write(json.dumps({"platform": platform, "ts": time.time()}))
        log("resolved"); print("resolved"); break
    if btn == "去解·聚焦标签页":
        focus_tab()                      # 切到真页面;切完短歇再弹"我已解决"确认
        time.sleep(8); rounds += 1; continue
    # 稍后 / 超时未处理 → 退避重弹(3 轮后放缓到 90s,不锁死也不停催)
    log("snoozed")
    time.sleep(30 if rounds < 3 else 90)
    rounds += 1
else:
    print("alert ended (max rounds)")
