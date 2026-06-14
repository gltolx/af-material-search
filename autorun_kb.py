#!/usr/bin/env python3
"""broll-auto 阶段二编排:批量清洗 → 等清洗完 → 自动入库知识库。无人值守 full-auto,绝不挂死。

链路(全程不停;只在"库名0/多匹配""建任务失败"这种干净 abort 处退出,带具名理由):
  0. **先**校验库名(fail-fast,0 成本):GET {node2}/api/kb/resolve?email=<acct> → 库名精确匹配 name→kb_id。
     0 个或 >1 个同名 → 立即 abort(不猜库),不浪费后面算力。
  1. 载入选中集(默认 RES/autorun_selected.json,= AI 选片+内容审查后只留 audit=pass 的项)。
     跨 run 去重:读 RES/_autorun_manifest.jsonl,(kb_id,stable_id) 已 uploaded 的跳过(不再重清洗/重入库)。
  2. 触发清洗:复用本机 download_server 的 /clean(读 RES/.dlport 拿真实端口)→ POST {items,dir} →
     读 NDJSON 流:首行 event==error → 整批 abort;event==start → 拿 job_id(整批一个);边下边清。
  3. 等清洗完成:轮询 {node2}/api/jobs/<job_id> 到 status=="finished"(所有 file 到 done/failed/cancelled)。
     **双保险防挂**:无进展看门狗(快照 STALL_S 不变且未 finished → 判卡死,仅卡住条记 orphan,已 done 继续)
     + 整 job 硬超时 HARD_S。node2 的 processing 无服务端 reaper,这里是唯一防线。
  4. 收集成功项:取 files 中 status=="done" 的 **key**(= 可读名,不是 out!),内嵌 stable_id 对账。
  5. 入库:POST {node2}/api/jobs/<job_id>/kb-upload {email,kb_id,items:[done keys]}(无需 clean token,job_id 即凭证)。
     返回 {queued,count};queued 可 < 发送数(被去重/非done跳过)→ 以 queued 为准。
  6. 等入库:再轮询 files[key].kb_status 到 uploaded/failed(独立第二道硬超时;kb 也无服务端 reaper)。
  7. 写 RES/_autorun_manifest.jsonl 台账(每行带 kb_id,= 去重依据 + 入库留痕)+ 打印汇总。

**必须用 douyin venv python 跑**(它有 requests):
  MATCLEAN_CLEAN_URL=https://tool.alphafin.world ~/.local/share/uv/tools/douyin-mcp-server/bin/python \
    autorun_kb.py --account <email> --kb <知识库名> [--res results] [--items results/autorun_selected.json]
"""
import os, re, sys, json, time, argparse, hashlib
import urllib.parse

try:
    import requests
except Exception as e:                                   # 必须 douyin venv python(有 requests)
    print("FATAL: 缺 requests —— 必须用 douyin venv python 跑 autorun_kb.py:", e, file=sys.stderr)
    sys.exit(2)

NODE2 = os.environ.get("MATCLEAN_CLEAN_URL", "https://tool.alphafin.world").rstrip("/")
HTTP_T = 60                                              # 所有出站请求统一超时(秒),绝不裸等
# 防挂死参数(node2 的 processing / kb_status 都无服务端 reaper,客户端兜底)
CLEAN_POLL_S = 10
CLEAN_STALL_S = 25 * 60                                  # 清洗快照连续无变化 → 判卡死
KB_POLL_S = 6
KB_STALL_S = 12 * 60
_TERMINAL = ("done", "failed", "cancelled")             # node2 文件终态(app.py:85)
_KB_TERMINAL = ("uploaded", "failed")                   # kb_status 终态(app.py:190/197)


def log(msg):
    print("[autorun] " + msg, flush=True)


def stable_id(page, url):
    # 契约4:与 download_server.stable_id / apply_verdicts.stable_id 两侧逐字一致
    s = (page or "") + " " + (url or "")
    m = re.search(r"/video/(\d{10,})", s)
    if m: return "dy_" + m.group(1)
    m = re.search(r"/explore/([0-9a-fA-F]{12,})", s)
    if m: return "xhs_" + m.group(1)
    m = re.search(r"(BV[0-9A-Za-z]{8,})", s)
    if m: return m.group(1)
    m = re.search(r"[?&]v=([\w-]{6,})", s) or re.search(r"youtu\.be/([\w-]{6,})", s)
    if m: return "yt_" + m.group(1)
    return "id_" + hashlib.md5(s.encode("utf-8")).hexdigest()[:10]


def get_json(url, timeout=HTTP_T):
    r = requests.get(url, timeout=timeout)
    try:
        body = r.json()
    except Exception:
        body = None
    return r.status_code, body


def resolve_kb(account, kb_name):
    """0 步:账号→KB 列表,库名精确匹配 name→kb_id。0/多匹配 → None+理由(abort)。"""
    url = NODE2 + "/api/kb/resolve?" + urllib.parse.urlencode({"email": account})
    try:
        code, body = get_json(url)
    except Exception as e:
        return None, "resolve 请求失败:" + str(e)[:140]
    if code != 200 or not isinstance(body, dict):
        msg = (body or {}).get("error") if isinstance(body, dict) else None
        return None, "账号校验失败(HTTP %s):%s" % (code, msg or "")
    kbs = body.get("kbs") or []
    hits = [k for k in kbs if (k.get("name") or "") == kb_name]
    if len(hits) == 0:
        names = ", ".join(sorted((k.get("name") or "") for k in kbs)) or "(空)"
        return None, "知识库名「%s」在账号 %s 下匹配 0 个;现有库:%s" % (kb_name, account, names)
    if len(hits) > 1:
        return None, "知识库名「%s」匹配 %d 个(重名),拒绝猜库;请用唯一库名" % (kb_name, len(hits))
    return hits[0].get("kb_id"), None


def load_manifest_uploaded(manifest_path, kb_id):
    """读历史台账,返回该 kb_id 下已 uploaded 的 stable_id 集合(跨 run 去重)。"""
    done = set()
    if not os.path.exists(manifest_path):
        return done
    try:
        with open(manifest_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("kb_id") == kb_id and r.get("kb_status") == "uploaded" and r.get("stable_id"):
                    done.add(r["stable_id"])
    except Exception as e:
        log("读台账失败(忽略,按未去重处理):" + str(e)[:120])
    return done


def read_dlport(res):
    p = os.path.join(res, ".dlport")
    try:
        return int(open(p, encoding="utf-8").read().strip())
    except Exception:
        return None


def trigger_clean(dlport, items, outdir):
    """POST download_server /clean,读 NDJSON 流。返回 (job_id, item_events) 或 (None, 理由)。"""
    url = "http://127.0.0.1:%d/clean" % dlport
    payload = {"items": items, "dir": outdir}
    job_id = None
    items_seen = []
    try:
        # text/plain 免 CORS 预检(download_server 不校验 content-type,纯 json.loads)
        r = requests.post(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "text/plain"}, stream=True, timeout=(30, 1800))
    except Exception as e:
        return None, "连不上 download_server /clean(:%d):%s" % (dlport, str(e)[:140])
    try:
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            etype = ev.get("event")
            if etype == "error":                        # 首行/任意行 error → 整批 abort(无 job 产生)
                return None, "node2 建清洗任务失败:" + str(ev.get("error", ""))[:160]
            if etype == "start":
                job_id = ev.get("job_id")
                log("清洗任务已建 job_id=%s total=%s  进度页 %s" % (job_id, ev.get("total"), ev.get("url")))
            elif etype == "item":
                items_seen.append(ev)
                log("  下载/上传 [%s/%s] %s %s %s" % (
                    ev.get("i"), ev.get("total"), ev.get("stable_id"), ev.get("status"),
                    ("· " + ev.get("warn")) if ev.get("warn") else ""))
            elif etype == "done":
                log("download_server 上传完毕(注:≠清洗完,下面轮询 node2)")
                break
    except Exception as e:
        # 流中断:若已拿到 job_id 仍可继续轮询 node2(清洗在服务端跑);否则 abort
        if job_id:
            log("清洗流读取中断(已拿 job_id,转轮询 node2):" + str(e)[:120])
        else:
            return None, "清洗流读取失败且未拿到 job_id:" + str(e)[:140]
    if not job_id:
        return None, "清洗流结束但未拿到 job_id"
    return job_id, items_seen


def _snap_sig(files):
    """快照指纹:文件状态 + 进度,用于无进展看门狗。"""
    return tuple(sorted((k, f.get("status"), f.get("elapsed"), f.get("kb_pct")) for k, f in files.items()))


def poll_clean(job_id, n_expected):
    """轮询 node2 到 status==finished。双保险防挂。返回最终 files 快照(dict)。"""
    base = NODE2 + "/api/jobs/" + job_id
    hard_s = max(2 * 3600, n_expected * 12 * 60)        # 整 job 硬超时
    t0 = time.time(); last_sig = None; last_change = t0
    while True:
        try:
            code, body = get_json(base)
        except Exception as e:
            log("轮询 /api/jobs 异常(重试):" + str(e)[:100]); time.sleep(CLEAN_POLL_S);
            if time.time() - t0 > hard_s:
                log("清洗硬超时(连轮询都拿不到),放弃等待"); return {}
            continue
        if code == 410:
            log("job 已过期(node2 3天清理),停止轮询"); return {}
        if code != 200 or not isinstance(body, dict):
            log("轮询返回异常 HTTP %s,稍后重试" % code); time.sleep(CLEAN_POLL_S); continue
        files = body.get("files") or {}
        status = body.get("status")
        done_n = sum(1 for f in files.values() if f.get("status") == "done")
        term_n = sum(1 for f in files.values() if f.get("status") in _TERMINAL)
        sig = _snap_sig(files)
        now = time.time()
        if sig != last_sig:
            last_sig = sig; last_change = now
            log("清洗进度 finished=%s  done=%d/%d  终态=%d/%d" % (status == "finished", done_n, len(files), term_n, len(files)))
        if status == "finished":
            log("✅ 清洗全部终态 finished"); return files
        if now - last_change > CLEAN_STALL_S:
            stuck = [k for k, f in files.items() if f.get("status") not in _TERMINAL]
            log("⚠️ 清洗无进展 %d 分钟,判卡死。卡住条记 orphan 不入库,已 done 条继续:%s" % (CLEAN_STALL_S // 60, stuck[:8]))
            return files
        if now - t0 > hard_s:
            log("⚠️ 清洗硬超时(%d分钟),停止等待,已 done 条继续入库" % (hard_s // 60))
            return files
        time.sleep(CLEAN_POLL_S)


def kb_upload(job_id, account, kb_id, keys):
    url = NODE2 + "/api/jobs/" + job_id + "/kb-upload"
    try:
        r = requests.post(url, json={"email": account, "kb_id": kb_id, "items": keys}, timeout=120)
        body = r.json()
    except Exception as e:
        return None, "kb-upload 请求失败:" + str(e)[:160]
    if r.status_code != 200:
        return None, "kb-upload HTTP %s:%s" % (r.status_code, (body or {}).get("error") if isinstance(body, dict) else "")
    return (body.get("queued") or []), None


def poll_kb(job_id, queued_keys):
    """轮询 files[key].kb_status 到 uploaded/failed。独立第二道硬超时。返回 {key: kb_status}。"""
    base = NODE2 + "/api/jobs/" + job_id
    hard_s = max(30 * 60, len(queued_keys) * 90)
    t0 = time.time(); last_sig = None; last_change = t0
    result = {}
    qset = set(queued_keys)
    while True:
        try:
            code, body = get_json(base)
        except Exception as e:
            log("轮询 kb_status 异常(重试):" + str(e)[:100]); time.sleep(KB_POLL_S)
            if time.time() - t0 > hard_s:
                return result
            continue
        if code != 200 or not isinstance(body, dict):
            time.sleep(KB_POLL_S); continue
        files = body.get("files") or {}
        cur = {k: (files.get(k) or {}).get("kb_status") for k in qset}
        done = {k: v for k, v in cur.items() if v in _KB_TERMINAL}
        sig = tuple(sorted(cur.items()))
        now = time.time()
        if sig != last_sig:
            last_sig = sig; last_change = now
            up = sum(1 for v in cur.values() if v == "uploaded")
            fl = sum(1 for v in cur.values() if v == "failed")
            log("入库进度 uploaded=%d failed=%d / %d" % (up, fl, len(qset)))
        if len(done) == len(qset):
            return cur
        if now - last_change > KB_STALL_S or now - t0 > hard_s:
            log("⚠️ 入库无进展/超时,停止等待;未终态记 timeout")
            return cur
        time.sleep(KB_POLL_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="入库账号 email")
    ap.add_argument("--kb", required=True, help="知识库名称(精确匹配,重名拒绝)")
    ap.add_argument("--res", default=os.environ.get("BROLL_RES", "results"), help="BROLL_RES 目录")
    ap.add_argument("--items", default=None, help="选中集 json(默认 RES/autorun_selected.json)")
    ap.add_argument("--dir", default=None, help="清洗过路件落盘目录(须可写;默认 ~/Downloads/af素材/<topic>)")
    ap.add_argument("--dry-run", action="store_true", help="只校验库名+载入选中集,不清洗不入库")
    args = ap.parse_args()

    res = os.path.abspath(args.res)
    manifest_path = os.path.join(res, "_autorun_manifest.jsonl")
    items_path = args.items or os.path.join(res, "autorun_selected.json")

    # topic(取 spec;决定默认落盘目录名 + node2 topic)
    topic = "broll-auto"
    try:
        spec = json.load(open(os.path.join(res, "relevance_spec.json"), encoding="utf-8"))
        topic = (spec.get("topic") or topic).split("(")[0].split("·")[0].strip()[:40] or "broll-auto"
    except Exception:
        pass
    outdir = args.dir or os.path.expanduser(os.path.join("~/Downloads/af素材", topic))
    os.makedirs(outdir, exist_ok=True)

    log("node2=%s  account=%s  kb=%s  res=%s" % (NODE2, args.account, args.kb, res))

    # 0. 先校验库名(fail-fast)
    kb_id, err = resolve_kb(args.account, args.kb)
    if not kb_id:
        log("❌ ABORT(库名校验):" + err); sys.exit(3)
    log("库名匹配 → kb_id=%s" % kb_id)

    # 1. 载入选中集 + 跨 run 去重
    try:
        selected = json.load(open(items_path, encoding="utf-8"))
        if not isinstance(selected, list):
            selected = selected.get("items") if isinstance(selected, dict) else []
    except Exception as e:
        log("❌ ABORT:载入选中集失败 %s:%s" % (items_path, str(e)[:120])); sys.exit(4)
    for it in selected:
        it["_sid"] = stable_id(it.get("page"), it.get("url"))
    already = load_manifest_uploaded(manifest_path, kb_id)
    fresh = [it for it in selected if it["_sid"] not in already]
    skipped_dup = len(selected) - len(fresh)
    log("选中 %d 条;跨run去重跳过 %d 条(已入库);本次清洗 %d 条" % (len(selected), skipped_dup, len(fresh)))
    if not fresh:
        log("✅ 无新素材需入库(全部已入库),结束"); return
    if len(fresh) >= 30:
        log("⚠️ 大批量 %d 条:占用共享 node2 GPU 较久,继续(无人值守不阻塞)" % len(fresh))

    if args.dry_run:
        log("--dry-run:到此为止(库名 OK,选中集 OK)"); return

    # 2. 触发清洗
    sid2item = {it["_sid"]: it for it in fresh}
    job_id, info = trigger_clean(dlport_required(res), fresh, outdir)
    if not job_id:
        log("❌ ABORT(清洗触发):" + info); sys.exit(5)

    # 3. 等清洗完成(双保险防挂)
    files = poll_clean(job_id, len(fresh))

    # 4. 收集 done 的 key(从快照取,勿客户端重构;用内嵌 stable_id 对账)
    done_keys = [k for k, f in files.items() if f.get("status") == "done"]
    key2sid = {k: (files.get(k) or {}).get("stable_id") for k in files}
    log("清洗完成:done %d 条 / 本批 %d 条" % (len(done_keys), len(fresh)))

    # 5. 入库(只传 done 的 key)
    kb_status_map = {}
    if done_keys:
        queued, err = kb_upload(job_id, args.account, kb_id, done_keys)
        if err:
            log("❌ 入库提交失败:" + err)
        else:
            log("入库已提交 queued=%d(发送 %d)" % (len(queued), len(done_keys)))
            # 6. 等入库
            kb_status_map = poll_kb(job_id, queued) if queued else {}
    else:
        log("无 done 条目,跳过入库")

    # 7. 写台账 + 汇总
    n_up = n_fail = n_clean_fail = 0
    ts = int(time.time())
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for it in fresh:
            sid = it["_sid"]
            key = next((k for k, s in key2sid.items() if s == sid), None)
            cstat = (files.get(key) or {}).get("status") if key else "orphan"
            kstat = kb_status_map.get(key) if key else None
            if cstat != "done":
                n_clean_fail += 1
            if kstat == "uploaded":
                n_up += 1
            elif key and key in kb_status_map and kstat != "uploaded":
                n_fail += 1
            mf.write(json.dumps({
                "ts": ts, "kb_id": kb_id, "platform": it.get("platform", ""),
                "script_name": it.get("script_name", ""), "stable_id": sid,
                "key": key, "clean_status": cstat or "unknown",
                "kb_status": kstat or ("timeout" if key in (kb_status_map or {}) else ("not_done" if cstat != "done" else "n/a")),
                "audit": it.get("audit", "pass"), "title": (it.get("title") or "")[:80],
                "err": (files.get(key) or {}).get("err", "") if key else "",
            }, ensure_ascii=False) + "\n")

    log("===== 汇总 =====")
    log("入库成功 uploaded=%d  入库失败/超时=%d  清洗未成功=%d  跨run去重跳过=%d" % (n_up, n_fail, n_clean_fail, skipped_dup))
    log("台账:%s" % manifest_path)
    log("进度页:%s/?job=%s" % (NODE2, job_id))


def dlport_required(res):
    p = read_dlport(res)
    if not p:
        log("❌ ABORT:读不到 %s/.dlport —— download_server 没起或没写端口旁车" % res)
        sys.exit(6)
    log("download_server 端口 = %d" % p)
    return p


if __name__ == "__main__":
    main()
