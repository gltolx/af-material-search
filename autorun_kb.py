#!/usr/bin/env python3
"""broll-auto 阶段二编排:批量清洗 → 等清洗完 → 自动入库知识库。无人值守 full-auto,绝不挂死。

**按口播稿链接分库(2026-06-17)**:选中集每条可带 source_link/account/kb_name(build_autorun_selected 从
kb_routing.json 盖章)。一个 source_link = 一套 账号+知识库 = 一个清洗任务,串行逐链接跑(每链接复用整条
清洗+入库闭包)。不带路由字段的项 → 落兜底组,用 --account/--kb 老单库行为(向后兼容)。搜索仍是合并一趟,
分库只在本编排层发生。

链路(全程不停;只在"全部链接库名都失败""建任务失败"这种干净 abort 处退出,带具名理由):
  1. 载入选中集(默认 RES/autorun_selected.json,= AI 选片+内容审查后只留 audit=pass 的项),按 source_link 分组。
  2. 逐链接校验 账号+库名(fail-fast):GET {node2}/api/kb/resolve?email=<acct> 精确匹配 name→kb_id。
     某链接 0/多匹配或缺账号库名 → **只跳过该链接**(记日志+汇总),其余照入;全部链接都失败才整批 abort。
  3. 每链接内:跨 run 去重(读 RES/_autorun_manifest.jsonl,该 kb_id 下 (kb_id,stable_id) 已 uploaded 跳过)。
  2. 触发清洗:复用本机 download_server 的 /clean(读 RES/.dlport 拿真实端口)→ POST {items,dir} →
     读 NDJSON 流:首行 event==error → 整批 abort;event==start → 拿 job_id(整批一个);边下边清。
  3. 交织轮询·边洗边传:每 tick 拉 {node2}/api/jobs/<job_id>——发现新 status=="done" 的 **key** 即刻
     POST .../kb-upload(逐条不等全批;无需 clean token,job_id 即凭证;node2 端点幂等去重,本地 submitted 去抖)。
     退出 = status=="finished" 且 无文件 kb_status∈{queued,uploading}(照抄 node2 app.py:539)。
     **三道防线**(node2 的 processing/kb_status 都无服务端 reaper,客户端是唯一防线):清洗 stall 看门狗
     (未终态清洗快照 CLEAN_STALL_S 不变→判卡)+ kb stall 看门狗(清洗完后在途 kb 快照 KB_STALL_S 不变→判卡)
     + 整 job 硬超时 HARD_S(清洗预算+kb尾段预算)。卡住条记非 uploaded 不入库,已 uploaded/已 done 继续。
  4. 按 stable_id 聚回每条最终 {cstat,kstat}(严格取自 node2 快照),供台账 + 重试判定。
  5. 写 RES/_autorun_manifest.jsonl 台账(每行带 kb_id,= 去重依据 + 入库留痕)+ 打印汇总。

**必须用 douyin venv python 跑**(它有 requests):
  MATCLEAN_CLEAN_URL=https://tool.alphafin.world ~/.local/share/uv/tools/douyin-mcp-server/bin/python \
    autorun_kb.py [--account <兜底email>] [--kb <兜底库名>] [--res results] [--items results/autorun_selected.json]
  多链接分库:选中集已带 per-link account/kb_name(kb_routing.json 盖章),--account/--kb 仅作未路由项的兜底,可省。
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
POLL_S = int(os.environ.get("AUTORUN_POLL_S", "8"))     # 统一轮询间隔(清洗10/kb6 折中)
CLEAN_STALL_S = 25 * 60                                  # 清洗快照连续无变化 → 判卡死
KB_STALL_S = 12 * 60                                     # kb 在途快照连续无变化 → 判卡死
KB_FAIL_MAX = int(os.environ.get("AUTORUN_KB_FAIL_MAX", "5"))  # kb-upload 连续失败达此 → 熔断停发只继续洗
_TERMINAL = ("done", "failed", "cancelled")             # node2 文件终态(app.py:85)
_KB_TERMINAL = ("uploaded", "failed")                   # kb_status 终态(app.py:190/197)
FAIL_TOL = int(os.environ.get("AUTORUN_FAIL_TOLERANCE", "3"))   # 允许 ≤N 条最终失败(用户拍板 2026-06-17:3)
MAX_ROUNDS = int(os.environ.get("AUTORUN_MAX_ROUNDS", "4"))     # 重试轮次封顶,防顽固失败死循环


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


def _clean_sig(files):
    """清洗看门狗指纹:仅未终态清洗文件的 (status, elapsed)。"""
    return tuple(sorted((k, f.get("status"), f.get("elapsed"))
                        for k, f in files.items() if f.get("status") not in _TERMINAL))


def _kb_sig(files):
    """入库看门狗指纹:仅在途 kb 文件的 (kb_status, kb_pct)。"""
    return tuple(sorted((k, f.get("kb_status"), f.get("kb_pct"))
                        for k, f in files.items() if f.get("kb_status") in ("queued", "uploading")))


def poll_clean_and_upload(job_id, n_expected, account, kb_id):
    """交织轮询:边洗边传。每 tick 拉 node2 job 快照——
      (a) 新 done 且未提交过的 key → 立即 kb-upload(本地 submitted 去抖;POST 失败下 tick 重发;连续失败→熔断);
      (b) 退出 = node2 finished 且 无 kb 在途(照抄 app.py:539,C1);
      (c) 双独立看门狗:清洗 stall(盯未终态清洗)/ kb stall(仅在清洗已完后盯在途 kb)(C2);
      (d) 整 job 硬超时兜底。
    返回最终 files 快照(含各文件 status/kb_status,由 _aggregate_round 取终值,C3)。"""
    base = NODE2 + "/api/jobs/" + job_id
    hard_s = max(2 * 3600, n_expected * 12 * 60) + max(30 * 60, n_expected * 90)   # 清洗预算+kb尾段预算(兜底)
    t0 = time.time()
    submitted = set()                                   # 已成功 POST(queued 返回含)的 key,仅作去抖(C4)
    kb_fail_streak = 0; kb_circuit_open = False
    clean_sig = kb_sig = None; clean_change = kb_change = t0; last_files = {}
    while True:
        try:
            code, body = get_json(base)
        except Exception as e:
            log("轮询 /api/jobs 异常(重试):" + str(e)[:100]); time.sleep(POLL_S)
            if time.time() - t0 > hard_s:
                log("硬超时(连轮询都拿不到),放弃等待"); return last_files
            continue
        if code == 410:
            log("job 已过期(node2 3天清理),停止轮询"); return last_files
        if code != 200 or not isinstance(body, dict):
            log("轮询返回异常 HTTP %s,稍后重试" % code); time.sleep(POLL_S); continue
        files = body.get("files") or {}; last_files = files
        status = body.get("status"); now = time.time()
        # (a) 边洗边传
        new_done = [k for k, f in files.items() if f.get("status") == "done" and k not in submitted]
        just_submitted = False
        if new_done and not kb_circuit_open:
            queued, err = kb_upload(job_id, account, kb_id, new_done)
            if err:
                kb_fail_streak += 1
                log("kb-upload 失败(连续 %d):%s" % (kb_fail_streak, err))
                if kb_fail_streak >= KB_FAIL_MAX:
                    kb_circuit_open = True
                    log("⚠️ kb-upload 连续失败 %d 次,熔断:停发,只继续清洗轮询" % kb_fail_streak)
            else:
                submitted |= set(queued); kb_fail_streak = 0
                if queued:
                    just_submitted = True
                    log("边洗边传:本tick入库提交 %d 条(submitted 累计 %d)" % (len(queued), len(submitted)))
        # 刚提交过 → 本 tick 快照已过期(提交前 GET 的),下 tick 再拉新状态判退出/看门狗,
        # 否则会在"最后一条刚 done 即 finished"那 tick 用旧快照误判"无 kb 在途"而早退漏入库(C1)。
        if just_submitted:
            time.sleep(POLL_S); continue
        # 进度日志 + 双看门狗计时
        csig = _clean_sig(files); ksig = _kb_sig(files)
        clean_pending = any(f.get("status") not in _TERMINAL for f in files.values())
        kb_inflight = any(f.get("kb_status") in ("queued", "uploading") for f in files.values())
        if csig != clean_sig or ksig != kb_sig:
            done_n = sum(1 for f in files.values() if f.get("status") == "done")
            up_n = sum(1 for f in files.values() if f.get("kb_status") == "uploaded")
            log("进度 finished=%s done=%d/%d 已入库=%d/%d" % (status == "finished", done_n, len(files), up_n, len(files)))
        if csig != clean_sig: clean_sig = csig; clean_change = now
        if ksig != kb_sig: kb_sig = ksig; kb_change = now
        # (b) 成功退出(C1)
        if status == "finished" and not kb_inflight:
            log("✅ 清洗全部终态 且 无 kb 在途,收尾"); return files
        # (c) 双看门狗(C2):清洗卡死随时判;kb 卡死仅在清洗已完后判(否则继续洗,别因 kb 卡放弃在洗的)
        if clean_pending and now - clean_change > CLEAN_STALL_S:
            stuck = [k for k, f in files.items() if f.get("status") not in _TERMINAL]
            log("⚠️ 清洗无进展 %d 分钟,判卡死,停等;已 done 继续入库:%s" % (CLEAN_STALL_S // 60, stuck[:8]))
            return files
        if not clean_pending and kb_inflight and now - kb_change > KB_STALL_S:
            stuck = [k for k, f in files.items() if f.get("kb_status") in ("queued", "uploading")]
            log("⚠️ 入库无进展 %d 分钟,判卡死,停等;未终态记非 uploaded:%s" % (KB_STALL_S // 60, stuck[:8]))
            return files
        # (d) 硬超时兜底
        if now - t0 > hard_s:
            log("⚠️ 整 job 硬超时(%d分钟),停等" % (hard_s // 60)); return files
        time.sleep(POLL_S)


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


def _aggregate_round(items, files, job_id):
    """把 node2 最终 files 快照按 stable_id 聚回每条 item 的 {cstat,kstat,key,job_id,err}。
    kstat/cstat 严格取自快照(C3);无 key→orphan;done 但 kb 在途/超时→kstat 非 uploaded(C6,_retryable 不重投)。"""
    out = {}
    key2sid = {k: (files.get(k) or {}).get("stable_id") for k in files}
    for it in items:
        sid = it["_sid"]
        key = next((k for k, s in key2sid.items() if s == sid), None)
        f = files.get(key) or {}
        cstat = f.get("status") if key else "orphan"
        out[sid] = {"cstat": cstat or "orphan", "kstat": f.get("kb_status") if key else None,
                    "key": key, "job_id": job_id, "err": f.get("err", "") if key else ""}
    return out


def _clean_round(items, kb_id, account, outdir, res):
    """一轮 清洗+边洗边传入库:触发清洗 → 交织轮询(每条 done 即入库)→ 按 sid 聚合。
    返回 ({_sid: {cstat,kstat,key,job_id,err}}, job_id)。job_id=None 表示触发失败。"""
    job_id, info = trigger_clean(dlport_required(res), items, outdir)
    if not job_id:
        log("❌ 本轮清洗触发失败:" + str(info))
        return {}, None
    files = poll_clean_and_upload(job_id, len(items), account, kb_id)
    done_n = sum(1 for f in files.values() if f.get("status") == "done")
    up_n = sum(1 for f in files.values() if f.get("kb_status") == "uploaded")
    log("本轮:清洗 done %d/%d,入库 uploaded %d" % (done_n, len(items), up_n))
    return _aggregate_round(items, files, job_id), job_id


def _retryable(r):
    # 可安全重投 = 清洗未成功(含下载失败的假失败)或 kb 明确 failed;
    # kb 在途/超时(已 done 但 kstat 未终态)不重投——避免它其实已上传造成重复入库
    return r.get("cstat") != "done" or r.get("kstat") == "failed"


def run_one_link(g, outdir, res, manifest_path):
    """跑一个口播稿链接(= 一套 账号+知识库 = 一个清洗任务)的完整 清洗+入库:
    跨run去重 → 自动重试轮次(边洗边传)→ 写本链接台账。返回汇总 dict。
    g = {account, kb_name, kb_id, source_link, items:[...]}。串行调用(用户拍板 2026-06-17:不并发)。"""
    account = g["account"]; kb_id = g["kb_id"]; kb_name = g["kb_name"]; slink = g.get("source_link") or ""
    items = g["items"]
    tag = ("链接 %s → 库「%s」" % (slink, kb_name)) if slink else ("兜底库「%s」" % kb_name)
    log("──── %s(账号 %s · %d 条)────" % (tag, account, len(items)))

    # 跨 run 去重:本链接 kb_id 下已 uploaded 的 stable_id 跳过(键不变,天然按库隔离)
    already = load_manifest_uploaded(manifest_path, kb_id)
    fresh = [it for it in items if it["_sid"] not in already]
    skipped_dup = len(items) - len(fresh)
    log("  选中 %d 条;跨run去重跳过 %d 条(已入库);本次清洗 %d 条" % (len(items), skipped_dup, len(fresh)))
    summary = {"tag": tag, "kb_name": kb_name, "source_link": slink, "account": account,
               "n_up": 0, "n_fail": 0, "n_clean_fail": 0, "skipped_dup": skipped_dup,
               "rnd": 0, "last_job": None}
    if not fresh:
        log("  ✅ 无新素材需入库(全部已入库)")
        return summary
    if len(fresh) >= 30:
        log("  ⚠️ 大批量 %d 条:占用共享 node2 GPU 较久,继续(无人值守不阻塞)" % len(fresh))

    agg = {}            # _sid -> {cstat,kstat,key,job_id,err}(后轮覆盖前轮)
    last_job = None
    pending = list(fresh)
    prev_retryable = None
    rnd = 0
    while rnd < MAX_ROUNDS:
        rnd += 1
        log(("  清洗轮次 1" if rnd == 1 else "  🔁 自动重试 第 %d 轮" % rnd) + "/%d:本轮 %d 条" % (MAX_ROUNDS, len(pending)))
        round_res, last_job = _clean_round(pending, kb_id, account, outdir, res)
        if last_job is None:
            log("  ⚠️ 本轮清洗触发失败,停止重试"); break
        agg.update(round_res)
        not_up = [it for it in fresh if agg.get(it["_sid"], {}).get("kstat") != "uploaded"]
        retryable = [it for it in not_up if _retryable(agg.get(it["_sid"], {}))]
        log("  第 %d 轮后:未入库 %d 条(其中可安全重试 %d)" % (rnd, len(not_up), len(retryable)))
        if len(not_up) <= FAIL_TOL:
            log("  ✅ 全部入库" if not not_up else "  剩 %d 条失败 ≤ 容忍阈值 %d,可接受,停止重试" % (len(not_up), FAIL_TOL)); break
        if not retryable:
            log("  ⚠️ 剩 %d 条无法安全重试(kb 在途/超时,重投恐重复入库),停止" % len(not_up)); break
        if prev_retryable is not None and len(retryable) >= prev_retryable:
            log("  ⚠️ 本轮无改善(可重试 %d→%d),判顽固失败,停止重试" % (prev_retryable, len(retryable))); break
        prev_retryable = len(retryable)
        pending = retryable
    else:
        log("  ⚠️ 达重试轮次上限 %d,停止" % MAX_ROUNDS)

    # 写本链接台账(每条 fresh 取最终聚合状态;新增 kb_name/account/source_link 留痕,旧字段不动)
    n_up = n_fail = n_clean_fail = 0
    ts = int(time.time())
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for it in fresh:
            sid = it["_sid"]
            r = agg.get(sid, {})
            key = r.get("key"); cstat = r.get("cstat") or "orphan"; kstat = r.get("kstat")
            if cstat != "done":
                n_clean_fail += 1
            if kstat == "uploaded":
                n_up += 1
            elif kstat and kstat != "uploaded":
                n_fail += 1
            mf.write(json.dumps({
                "ts": ts, "kb_id": kb_id, "kb_name": kb_name, "account": account,
                "source_link": slink, "platform": it.get("platform", ""),
                "script_name": it.get("script_name", ""), "stable_id": sid,
                "key": key, "clean_status": cstat,
                "kb_status": kstat or ("not_done" if cstat != "done" else "n/a"),
                "audit": it.get("audit", "pass"), "title": (it.get("title") or "")[:80],
                "err": r.get("err", ""),
            }, ensure_ascii=False) + "\n")
    summary.update({"n_up": n_up, "n_fail": n_fail, "n_clean_fail": n_clean_fail, "rnd": rnd, "last_job": last_job})
    log("  本链接:入库 %d  失败/超时 %d  清洗未成功 %d  轮次 %d" % (n_up, n_fail, n_clean_fail, rnd))
    return summary


def group_by_link(selected, default_account, default_kb):
    """把选中集按"口播稿链接"分组:一个链接(source_link)= 一套 账号+知识库 = 一个清洗任务。
    带 source_link+kb_name 的项 → 各自成组;不带的 → 落兜底组(用 --account/--kb,向后兼容老单库)。
    返回 OrderedDict {group_key: {account, kb_name, source_link, items}}(保持首现顺序)。"""
    groups = {}
    for it in selected:
        link = (it.get("source_link") or "").strip()
        kbn = (it.get("kb_name") or "").strip()
        acct = (it.get("account") or "").strip()
        if link and kbn:                                    # 已路由项 → 按链接独立成组
            g = groups.setdefault(link, {"account": acct or default_account, "kb_name": kbn,
                                         "source_link": link, "items": []})
        else:                                               # 未路由 → 兜底组(老单库行为)
            g = groups.setdefault("__default__", {"account": default_account, "kb_name": default_kb,
                                                  "source_link": "", "items": []})
        g["items"].append(it)
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=None, help="兜底入库账号 email(未按链接路由的项用它;多链接时账号由选中集 per-link 字段给)")
    ap.add_argument("--kb", default=None, help="兜底知识库名(未按链接路由的项用它;精确匹配,重名拒绝)")
    ap.add_argument("--res", default=os.environ.get("BROLL_RES", "results"), help="BROLL_RES 目录")
    ap.add_argument("--items", default=None, help="选中集 json(默认 RES/autorun_selected.json)")
    ap.add_argument("--dir", default=None, help="清洗过路件落盘目录(须可写;默认 ~/Downloads/af素材/<topic>)")
    ap.add_argument("--dry-run", action="store_true", help="只分组+校验各链接账号库名,不清洗不入库")
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

    log("node2=%s  res=%s  兜底账号=%s  兜底库=%s" % (NODE2, res, args.account, args.kb))

    # 1. 载入选中集
    try:
        selected = json.load(open(items_path, encoding="utf-8"))
        if not isinstance(selected, list):
            selected = selected.get("items") if isinstance(selected, dict) else []
    except Exception as e:
        log("❌ ABORT:载入选中集失败 %s:%s" % (items_path, str(e)[:120])); sys.exit(4)
    for it in selected:
        it["_sid"] = stable_id(it.get("page"), it.get("url"))

    # 2. 按口播稿链接分组(一个链接 = 一套 账号+知识库 = 一个清洗任务)
    groups = group_by_link(selected, args.account, args.kb)
    n_links = sum(1 for k in groups if k != "__default__")
    log("分组:%d 个口播稿链接%s,共 %d 条素材" % (
        n_links, "(+1 个兜底组)" if "__default__" in groups else "", len(selected)))

    # 3. 逐组校验 账号+库名(fail-fast per link)。任一坏链接 → 只跳过该链接、其余照入(用户拍板 2026-06-17);
    #    全部链接都校验失败 → 整批 abort(具名理由)。
    resolved = []; skipped_links = []
    for key, g in groups.items():
        name = g["source_link"] or ("兜底库「%s」" % (g["kb_name"] or "?"))
        if not g["account"] or not g["kb_name"]:
            reason = "缺账号或库名(account=%s kb=%s)" % (g["account"], g["kb_name"])
            log("  ⏭️  跳过 %s:%s(%d 条不入库)" % (name, reason, len(g["items"])))
            skipped_links.append((name, reason, len(g["items"]))); continue
        kb_id, err = resolve_kb(g["account"], g["kb_name"])
        if not kb_id:
            log("  ⏭️  跳过 %s:%s(%d 条不入库)" % (name, err, len(g["items"])))
            skipped_links.append((name, err, len(g["items"]))); continue
        g["kb_id"] = kb_id
        log("  ✓ %s → 账号 %s · 库「%s」kb_id=%s(%d 条)" % (name, g["account"], g["kb_name"], kb_id, len(g["items"])))
        resolved.append(g)

    if not resolved:
        log("❌ ABORT:所有链接的账号/库名都校验失败,无可入库目标"); sys.exit(3)

    if args.dry_run:
        log("--dry-run:到此为止(可入库链接 %d 个,跳过 %d 个)" % (len(resolved), len(skipped_links))); return

    # 4. 串行:逐个口播稿链接跑完整 清洗+入库(用户拍板 2026-06-17:串行不并发)
    summaries = [run_one_link(g, outdir, res, manifest_path) for g in resolved]

    # 5. 总汇总
    log("===== 总汇总(%d 个链接)=====" % len(summaries))
    tot_up = tot_fail = tot_cf = tot_dup = 0
    for s in summaries:
        tot_up += s["n_up"]; tot_fail += s["n_fail"]; tot_cf += s["n_clean_fail"]; tot_dup += s["skipped_dup"]
        log("· %s:入库 %d  失败/超时 %d  清洗未成功 %d  去重跳过 %d%s" % (
            s["tag"], s["n_up"], s["n_fail"], s["n_clean_fail"], s["skipped_dup"],
            ("  进度页 %s/?job=%s" % (NODE2, s["last_job"])) if s["last_job"] else ""))
    log("合计:入库 uploaded=%d  失败/超时=%d  清洗未成功=%d  跨run去重=%d" % (tot_up, tot_fail, tot_cf, tot_dup))
    if skipped_links:
        log("⏭️ 跳过的链接 %d 个(账号/库名问题,这些素材未入库,改对了重跑会补上):" % len(skipped_links))
        for name, reason, n in skipped_links:
            log("   - %s(%d 条):%s" % (name, n, reason))
    log("台账:%s" % manifest_path)


def dlport_required(res):
    p = read_dlport(res)
    if not p:
        log("❌ ABORT:读不到 %s/.dlport —— download_server 没起或没写端口旁车" % res)
        sys.exit(6)
    log("download_server 端口 = %d" % p)
    return p


if __name__ == "__main__":
    main()
