#!/usr/bin/env python3
"""R4:/clean payload 每条带 name_prefix(口播稿名)+ persona,投递给 node2。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_server as ds

it = {"platform":"bilibili","title":"国足米卢","verdict":"keep","score":"88",
      "script_name":"开场白稿","persona":"老王"}
d = ds._clean_item("BV19gpjeSEJ5", it)
assert d["stable_id"] == "BV19gpjeSEJ5", d
assert d["name_prefix"] == "开场白稿", d
assert d["persona"] == "老王", d
assert d["title"] == "国足米卢" and d["verdict"] == "keep", d
# 无 script_name → name_prefix 空串
d2 = ds._clean_item("x", {"platform":"douyin","title":"t"})
assert d2["name_prefix"] == "" and d2["persona"] == "", d2
print("OK")
