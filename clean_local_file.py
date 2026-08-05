#!/usr/bin/env python3
"""把一个【已在本地的】视频文件直接送进 node2 清洗+入库,绕过 download_server 的下载环节。
用途:某条抖音 snssdk play 端点 404(KR 出口失效),已用 yt-dlp+chrome cookies 在浏览器路由下到本地,
需要补做 清洗+入库。复用 download_server 的 /api/clean start+item 协议 + autorun_kb 的 resolve_kb/poll_clean_and_upload。
必须用 douyin venv python 跑(有 requests)。

参数(位置):  <本地mp4路径>  <account>  <kb_name>
环境: BROLL_RES(决定 manifest 路径+topic), MATCLEAN_CLEAN_URL, MATCLEAN_CLEAN_TOKEN
"""
import os, sys, json, time, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_server as D
import autorun_kb as A

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
CLEAN_BASE = os.environ.get("MATCLEAN_CLEAN_URL", "https://tool.alphafin.world").rstrip("/")
CLEAN_TOKEN = os.environ.get("MATCLEAN_CLEAN_TOKEN", "")
manifest_path = os.path.join(RES, "_autorun_manifest.jsonl")


def main():
    path, account, kb_name = sys.argv[1], sys.argv[2], sys.argv[3]
    assert os.path.exists(path), "本地文件不存在: " + path

    # 1. 还原这条 item(与 autorun_selected 里该条一致;page 决定 stable_id)
    it = json.load(open(os.path.join(RES, "autorun_selected.json"), encoding="utf-8"))
    sid_target = D.stable_id  # noqa
    # 从选中集里找到这条(按文件名里的 aweme_id)
    import re
    m = re.search(r"dy_(\d{10,})", os.path.basename(path)) or re.search(r"(\d{15,})", os.path.basename(path))
    aweme = m.group(1) if m else None
    item = next((x for x in it if aweme and aweme in (x.get("page") or "")), None)
    assert item, "选中集里找不到 aweme_id=%s 的条目" % aweme
    sid = D.stable_id(item.get("page"), item.get("url"))
    print("[local] 目标 stable_id=%s  title=%s" % (sid, (item.get("title") or "")[:40]))

    # 2. 校验账号+库名 → kb_id(复用 autorun_kb.resolve_kb)
    A.NODE2 = CLEAN_BASE
    kb_id, err = A.resolve_kb(account, kb_name)
    assert kb_id, "账号/库名解析失败: " + str(err)
    print("[local] kb_id=%s" % kb_id)

    # 3. POST /api/clean/start(1 条;payload 与 download_server.handle_clean 同构)
    topic = "broll-auto"
    try:
        spec = json.load(open(os.path.join(RES, "relevance_spec.json"), encoding="utf-8"))
        topic = (spec.get("topic") or topic).split("(")[0].split("·")[0].strip()[:40] or "broll-auto"
    except Exception:
        pass
    hdr = {"X-Clean-Token": CLEAN_TOKEN} if CLEAN_TOKEN else {}
    payload = {"topic": topic, "items": [D._clean_item(sid, item)]}
    r = requests.post(CLEAN_BASE + "/api/clean/start", json=payload, headers=hdr, timeout=30)
    job_id = r.json()["job_id"]
    print("[local] job_id=%s  进度页 %s/?job=%s" % (job_id, CLEAN_BASE, job_id))

    # 4. 上传本地文件(multipart,与 download_server._upload_item 同构)
    url = CLEAN_BASE + "/api/clean/%s/item" % job_id
    fname = "%s.mp4" % (D.build_name(item))
    up_ok = False
    for k in range(3):
        try:
            with open(path, "rb") as fh:
                ur = requests.post(url, files={sid: (fname, fh, "application/octet-stream")},
                                   data={"name": fname}, headers=hdr, timeout=900)
            if ur.status_code == 200:
                up_ok = True; break
            print("[local] 上传 HTTP %s,重试" % ur.status_code)
        except Exception as e:
            print("[local] 上传异常重试:", str(e)[:120])
        time.sleep(2 * (k + 1))
    assert up_ok, "本地文件上传 node2 失败"
    print("[local] ✅ 已上传 node2,文件名=%s,开始轮询清洗+入库" % fname)

    # 5. 轮询清洗 + 边洗边传入库(复用 autorun_kb.poll_clean_and_upload)
    A.NODE2 = CLEAN_BASE
    files = A.poll_clean_and_upload(job_id, 1, account, kb_id)
    f = next((v for v in files.values() if v.get("stable_id") == sid), {})
    cstat = f.get("status", "orphan"); kstat = f.get("kb_status")
    print("[local] 收尾: clean=%s kb=%s" % (cstat, kstat))

    # 6. 写台账(与 autorun_kb.run_one_link 同字段;uploaded 才算补成)
    with open(manifest_path, "a", encoding="utf-8") as mf:
        mf.write(json.dumps({
            "ts": int(time.time()), "kb_id": kb_id, "kb_name": kb_name, "account": account,
            "source_link": "", "platform": item.get("platform", ""),
            "script_name": item.get("script_name", ""), "stable_id": sid,
            "key": f.get("key") if isinstance(f, dict) else None, "clean_status": cstat,
            "kb_status": kstat or ("not_done" if cstat != "done" else "n/a"),
            "audit": item.get("audit", "pass"), "title": (item.get("title") or "")[:80],
            "err": f.get("err", "") if isinstance(f, dict) else "", "via": "yt-dlp-browser-cookies",
        }, ensure_ascii=False) + "\n")
    print("[local] 台账已写。结果:", "✅入库成功" if kstat == "uploaded" else "⚠️未入库(%s/%s)" % (cstat, kstat))


if __name__ == "__main__":
    main()
