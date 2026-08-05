#!/usr/bin/env python3
# 一次性解析奥克斯20人设口播稿 -> scripts.json
import json, re, os, sys

SRC = "/Users/linxiao/Desktop/奥克斯20人设_中国空调消费痛点口播稿_20260618.md"
RES = os.environ.get("BROLL_RES", "results")
out = os.path.join(RES, "scripts.json")

txt = open(SRC, encoding="utf-8").read()
lines = txt.splitlines()

scripts = []
cur = None
buf = []

def flush():
    global cur, buf
    if cur is None:
        return
    body = "\n".join(buf).strip()
    cur["text"] = body
    cur["words"] = len(re.sub(r"\s", "", body))
    scripts.append(cur)
    buf = []

hdr = re.compile(r"^##\s+(\d+)\.\s+(.*)$")
for ln in lines:
    m = hdr.match(ln)
    if m:
        flush()
        n = int(m.group(1))
        rest = m.group(2).strip()
        # 格式: 人设号 / 人名｜标题   （也可能只有 人名｜标题）
        persona = ""
        title = rest
        if "｜" in rest:
            left, title = rest.split("｜", 1)
            persona = left.strip()
            title = title.strip()
        cur = {"script_id": f"s{n}", "persona": persona, "name": title, "text": "", "words": 0}
    else:
        if cur is not None:
            # 跳过“数据来源”这种尾部块（它不是 ## N 开头，自然不会进新段）
            buf.append(ln)

flush()

# 去掉“数据来源”尾巴：最后一段如果 script_id 异常不会发生，这里仅清洗 text 里可能混入的来源块
# （数据来源是单独 ## 段但无编号，hdr 不匹配 -> 会被并进最后一条 text，需剔除）
for s in scripts:
    if "## 数据来源" in s["text"]:
        s["text"] = s["text"].split("## 数据来源", 1)[0].strip()
        s["words"] = len(re.sub(r"\s", "", s["text"]))

os.makedirs(RES, exist_ok=True)
json.dump(scripts, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"wrote {len(scripts)} scripts -> {out}")
for s in scripts:
    print(f'  {s["script_id"]:>4}  persona={s["persona"]!r:<22} words={s["words"]:>4}  {s["name"]}')
