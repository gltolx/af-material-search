#!/usr/bin/env python3
"""/clean payload 每条:stable_id(去重/入库锚)+ 新格式 name + name_prefix(口播稿名)+ persona,投 node2。
⑤ 2026-06-13:name 改 {平台中文}_{平台原生ID}_{稿名/标题}.mp4(原生ID 稳定,无自增 seq),stable_id 仍独立保留。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_server as ds

it = {"platform": "bilibili", "title": "国足米卢", "verdict": "keep", "score": "88",
      "script_name": "开场白稿", "persona": "老王",
      "page": "https://www.bilibili.com/video/BV19gpjeSEJ5", "url": ""}
d = ds._clean_item("BV19gpjeSEJ5", it)
assert d["stable_id"] == "BV19gpjeSEJ5", d          # stable_id 独立字段仍在(去重/入库锚,不受文件名格式影响)
assert d["name_prefix"] == "开场白稿", d
assert d["persona"] == "老王", d
assert d["title"] == "国足米卢" and d["verdict"] == "keep", d
assert d["name"] == "B站_BV19gpjeSEJ5_开场白稿.mp4", d["name"]   # 新格式:平台+原生ID_稿名(与下载 process(it,outdir) 同 build_name)

# 无 script_name → name_prefix 空串;name 用素材标题 + 抖音原生 19 位 ID
d2 = ds._clean_item("dy_7569094697223595365",
                    {"platform": "抖音", "title": "夏天",
                     "page": "https://www.douyin.com/video/7569094697223595365", "url": ""})
assert d2["name_prefix"] == "" and d2["persona"] == "", d2
assert d2["name"] == "抖音_7569094697223595365_夏天.mp4", d2["name"]
print("OK")
