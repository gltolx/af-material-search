#!/usr/bin/env python3
"""R1 识别手段:对 scores_part*.json 中 need_frames=true 的候选,下开头6秒抽3帧,供 Claude 看帧定稿 person_primary。
只对存疑子集抽(不给全池下片,守"轻")。读 candidates.json + scores_part*.json,出 results/covers_frames/<idx>_f{0,1,2}.jpg。
平台不支持区间下载时 best-effort 跳过(仍可只看静态封面)。
用法:python3 extract_early_frames.py   (system python3 即可;只调 yt-dlp/ffmpeg 子进程)
"""
import os, glob, json, subprocess, shutil, tempfile

RES = os.environ.get("BROLL_RES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
FRAMES = os.path.join(RES, "covers_frames")


def _find_bin(name, env, fixed):
    p = os.environ.get(env)
    if p and os.path.exists(p): return p
    p = shutil.which(name)
    if p: return p
    c = [x for x in fixed if os.path.exists(x)]
    return sorted(c, reverse=True)[0] if c else name


def select_need_frames(scores):
    """返回需抽帧的 idx 列表(need_frames 为真)。"""
    return [s["idx"] for s in scores if s.get("need_frames") and "idx" in s]


def build_ytdlp_cmd(page, outbase, ytdlp, ffmpeg):
    """下开头6秒到 outbase.%(ext)s。"""
    return [ytdlp, "--no-warnings", "--no-playlist", "--ffmpeg-location", ffmpeg,
            "--download-sections", "*0-6", "--force-keyframes-at-cuts",
            "-f", "bv*+ba/b", "--merge-output-format", "mp4",
            "-o", outbase + ".%(ext)s", "--retries", "5", "--socket-timeout", "40",
            "--cookies-from-browser", "chrome", page]


def build_ffmpeg_cmds(clip, outbase, ffmpeg):
    """从 clip 抽 0/2/4 秒各一帧 → outbase_f{0,1,2}.jpg。"""
    cmds = []
    for i, t in enumerate((0, 2, 4)):
        cmds.append([ffmpeg, "-y", "-ss", str(t), "-i", clip, "-frames:v", "1",
                     "-q:v", "3", f"{outbase}_f{i}.jpg"])
    return cmds


def _find_clip(outbase):
    d, base = os.path.dirname(outbase), os.path.basename(outbase)
    cand = [f for f in os.listdir(d) if f.startswith(base + ".") and not f.endswith(".part")]
    return os.path.join(d, cand[0]) if cand else None


def extract_one(idx, page, ytdlp, ffmpeg):
    if not page:
        return False, "无 page"
    os.makedirs(FRAMES, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        clipbase = os.path.join(td, "clip")
        try:
            subprocess.run(build_ytdlp_cmd(page, clipbase, ytdlp, ffmpeg),
                           capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return False, "yt-dlp 超时"
        clip = _find_clip(clipbase)
        if not clip or os.path.getsize(clip) < 1024:
            return False, "无开头片段(平台不支持区间/限流)"
        outbase = os.path.join(FRAMES, str(idx))
        got = 0
        for cmd in build_ffmpeg_cmds(clip, outbase, ffmpeg):
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if os.path.exists(cmd[-1]) and os.path.getsize(cmd[-1]) > 500:
                    got += 1
            except subprocess.TimeoutExpired:
                pass
        return (got > 0), (f"抽到 {got} 帧" if got else "ffmpeg 未出帧")


def main():
    ytdlp = _find_bin("yt-dlp", "YTDLP",
                      glob.glob(os.path.expanduser("~/Library/Python/3.*/bin/yt-dlp")) + [os.path.expanduser("~/.local/bin/yt-dlp")])
    ffmpeg = _find_bin("ffmpeg", "FFMPEG", [os.path.expanduser("~/.local/bin/ffmpeg")])
    cands = {c["idx"]: c for c in json.load(open(os.path.join(RES, "candidates.json"), encoding="utf-8"))}
    scores = []
    for f in sorted(glob.glob(os.path.join(RES, "scores_part*.json"))):
        scores += json.load(open(f, encoding="utf-8"))
    todo = select_need_frames(scores)
    print(f"需抽帧候选 {len(todo)} 个 → {FRAMES}")
    ok = 0
    for idx in todo:
        c = cands.get(idx) or {}
        page = c.get("page") or c.get("url")
        good, msg = extract_one(idx, page, ytdlp, ffmpeg)
        ok += 1 if good else 0
        print(f"  [{idx}] {'✓' if good else '✗'} {msg} | {(c.get('title') or '')[:30]}")
    print(f"完成:{ok}/{len(todo)} 抽到帧;看 {FRAMES}/<idx>_f*.jpg 定稿 person_primary 后回填 scores_part")


if __name__ == "__main__":
    main()
