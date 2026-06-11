#!/usr/bin/env python3
"""小红书 note_id → {t(=type), imgs} 映射构建。
单一事实源:供 merge_scored.py(收割合并时自动产 xhs_imgs.json)+ backfill_xhs_token.py 复用,别两处各写一遍。
输入 xhs_raw.json 每条:{p: charCode 点分隔编码的带 token explore URL, type, cover, title, imgs}。
download_server 据 t 决定图文(normal→下图片)还是视频(video→yt-dlp);缺这张图 → 图文笔记会被 yt-dlp 下成幻灯片 mp4。
"""
import re


def dec(code):
    """charCode 数字点分隔 → 原串(收割端为绕工具屏蔽长串做的编码)。"""
    return "".join(chr(int(x)) for x in code.split(".")) if code else ""


def build_imgmap(raw):
    """raw: xhs_raw.json 列表 → {note_id: {"t": type, "imgs": [...]}}。p 解不出 note id 的跳过。"""
    m = {}
    for x in raw:
        url = dec(x.get("p", ""))
        mt = re.search(r"/explore/([0-9a-fA-F]{12,})", url)
        if mt:
            m[mt.group(1)] = {"t": x.get("type", ""), "imgs": x.get("imgs") or []}
    return m
