#!/usr/bin/env python3
"""Fix B:小红书无类型映射(缺 xhs_imgs.json/该 note 不在图里)→ 告警(图文会被下成幻灯片mp4)。
有映射(normal/video 已知)或非小红书 → 不告警。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_server as ds

xhs = {"platform":"小红书","page":"https://www.xiaohongshu.com/explore/abc123def456?xsec_token=T","url":""}

ds._XHS_IMGS = {}                                            # 无映射
assert ds.xhs_download_warn(xhs), "无映射应告警"

ds._XHS_IMGS = {"abc123def456":{"t":"video","imgs":[]}}      # 已知视频
assert ds.xhs_download_warn(xhs) == "", "已知类型不应告警"

ds._XHS_IMGS = {"abc123def456":{"t":"normal","imgs":["u"]}}  # 已知图文(会走下图片)
assert ds.xhs_download_warn(xhs) == "", "已知图文不应告警"

ds._XHS_IMGS = {"abc123def456":{"t":"","imgs":[]}}           # 类型空 = 不可靠 → 告警
assert ds.xhs_download_warn(xhs), "空类型应告警"

ds._XHS_IMGS = {}
assert ds.xhs_download_warn({"platform":"B站/YT","page":"https://www.bilibili.com/video/BV10000000aa"}) == "", "非小红书不告警"
print("OK")
