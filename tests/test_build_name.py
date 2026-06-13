#!/usr/bin/env python3
"""⑤ 文件名(用户拍板 2026-06-13):{平台中文}_{平台原生ID}_{口播稿标题或素材标题},总长(含.mp4)≤70,
无空格/emoji/#/乱码;原生ID 取平台自带那串字母/数字(不再自增 01/02/03,稳定不重复→重下幂等)。
stable_id 仍作独立字段进 manifest/node2。"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_server as ds

base = {"platform": "B站/YT", "title": "国足米卢",
        "page": "https://www.bilibili.com/video/BV19gpjeSEJ5", "url": ""}

# B站:原生 ID = BV 号本身(stable_id 不加前缀)
assert ds.build_name(base) == "B站_BV19gpjeSEJ5_国足米卢", ds.build_name(base)
# 有 script_name → 用口播稿标题取代标题段
assert ds.build_name(dict(base, script_name="开场白稿一")) == "B站_BV19gpjeSEJ5_开场白稿一", ds.build_name(dict(base, script_name="开场白稿一"))
# sanitize:/ 等非法字符 → _
assert ds.build_name(dict(base, script_name="开场白/稿一")) == "B站_BV19gpjeSEJ5_开场白_稿一", ds.build_name(dict(base, script_name="开场白/稿一"))
# 空白 script_name → 回退素材标题
assert ds.build_name(dict(base, script_name="   ")) == "B站_BV19gpjeSEJ5_国足米卢"
# 同一视频每次文件名一致(原生ID稳定 → process 幂等 skip 有效)
assert ds.build_name(base) == ds.build_name(base)

# 去空格/emoji/#话题,总长(含.mp4)≤70,且含平台原生 ID
nasty = dict(base, title="🔥国足 米卢 #怀旧 ⚽→✨", script_name="")
n = ds.build_name(nasty); full = n + ".mp4"
for bad in (" ", "#", "🔥", "⚽", "→", "✨"):
    assert bad not in full, (bad, full)
assert len(full) <= 70, (len(full), full)
assert "BV19gpjeSEJ5" in n, n

# 超长口播稿标题截断到 ≤70(原生ID 段不被截断,仍在名内)
longsn = "这是一个特别特别特别特别特别特别特别特别特别特别特别特别长的口播稿标题用来测试截断" * 3
ln = ds.build_name(dict(base, script_name=longsn))
assert len(ln + ".mp4") <= 70
assert "BV19gpjeSEJ5" in ln, ln

# 各平台中文名 + 原生 ID(抖音去 dy_ / 小红书去 xhs_ / YT 去 yt_)
dy = {"platform": "抖音", "title": "夏天", "page": "https://www.douyin.com/video/7569094697223595365", "url": ""}
assert ds.build_name(dy) == "抖音_7569094697223595365_夏天", ds.build_name(dy)
xhs = {"platform": "小红书", "title": "笔记", "page": "https://www.xiaohongshu.com/explore/6a2915a9000000002202756b", "url": ""}
assert ds.build_name(xhs) == "小红书_6a2915a9000000002202756b_笔记", ds.build_name(xhs)
yt = {"platform": "B站/YT", "title": "video", "page": "https://www.youtube.com/watch?v=BX50N4JdoNo", "url": ""}
assert ds.build_name(yt) == "YouTube_BX50N4JdoNo_video", ds.build_name(yt)

# 兜底(无可识别 ID):stable_id 走 id_<md5>,native_id 去掉 id_ 前缀,仍稳定不重复
fb = {"platform": "B站/YT", "title": "无ID", "page": "https://example.com/x", "url": ""}
nfb = ds.build_name(fb)
assert nfb.startswith("B站_"), nfb
assert "id_" not in nfb, nfb            # id_ 前缀已被 native_id 去掉
assert len(nfb + ".mp4") <= 70

# 随机 1000 组:任意稿名/标题/平台,断言 len(name+ext)<=70 且无空格/#
plats = [
    ("抖音", "https://www.douyin.com/video/%d"),
    ("小红书", "https://www.xiaohongshu.com/explore/%012xabcdef0000"),
    ("B站/YT", "https://www.bilibili.com/video/BV%d"),
    ("B站/YT", "https://www.youtube.com/watch?v=%s"),
]
chars = "国足米卢夏天笔记开场白稿一二三四五🔥⚽→✨ #怀旧/\\:*?abcXYZ"
exts = (".mp4", ".jpg", ".png")
rng = random.Random(20260613)
for _ in range(1000):
    plat, pat = rng.choice(plats)
    if "%012x" in pat:
        page = pat % rng.randrange(16**8)
    elif "%s" in pat:
        page = pat % "".join(rng.choice("ABCDEFGabcdefg0123456789-_") for _ in range(rng.randint(6, 11)))
    else:
        page = pat % rng.randrange(10**9, 10**18)
    title = "".join(rng.choice(chars) for _ in range(rng.randint(0, 60)))
    sn = "".join(rng.choice(chars) for _ in range(rng.randint(0, 60))) if rng.random() < 0.5 else ""
    it = {"platform": plat, "title": title, "script_name": sn, "page": page, "url": ""}
    name = ds.build_name(it)
    ext = rng.choice(exts)
    full = name + ext
    assert len(full) <= 70, (len(full), full)
    assert " " not in full and "#" not in full, full

print("OK")
