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
