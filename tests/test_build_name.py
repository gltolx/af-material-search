#!/usr/bin/env python3
"""R4:文件名前缀。有 script_name → 前缀;无 → 维持原 平台_标题_id。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_server as ds

base = {"platform":"B站/YT","title":"国足米卢","page":"https://www.bilibili.com/video/BV19gpjeSEJ5","url":""}
# 无 script_name:保持原规则
assert ds.build_name(base) == "B站_国足米卢_BV19gpjeSEJ5", ds.build_name(base)
# 有 script_name:前缀(经 sanitize)
it = dict(base, script_name="开场白/稿一")
assert ds.build_name(it) == "开场白_稿一_B站_国足米卢_BV19gpjeSEJ5", ds.build_name(it)
# 空白 script_name 不加前缀
assert ds.build_name(dict(base, script_name="   ")) == "B站_国足米卢_BV19gpjeSEJ5"
print("OK")
