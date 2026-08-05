"""Enable official Pexels download URLs in a generated result page."""

import argparse
import html
import json
import os
import re
import stat
import tempfile
import urllib.parse
from pathlib import Path
from typing import Dict, Optional, Tuple


TAG_RE = re.compile(r"<(a|input)\b[^>]*>", re.IGNORECASE)
ATTR_RE_TEMPLATE = r"\b{attribute}\s*=\s*(['\"])(.*?)\1"
PEXELS_NOTICE = "Pexels 支持批量下载；清洗/入库已禁用"
OLD_PEXELS_NOTICES = (
    "Pexels 素材暂不支持下载、清洗或入库",
    "🔒 Pexels 补充页仅验收；下载请点卡片进入原站，清洗/入库已禁用",
)


def _attribute(tag: str, name: str) -> Optional[str]:
    match = re.search(ATTR_RE_TEMPLATE.format(attribute=re.escape(name)), tag, re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(2)) if match else None


def _with_data_url(tag: str, direct_url: str) -> str:
    pattern = re.compile(ATTR_RE_TEMPLATE.format(attribute="data-url"), re.IGNORECASE | re.DOTALL)
    escaped_url = html.escape(direct_url, quote=True)

    def replace(match: re.Match[str]) -> str:
        return f"data-url={match.group(1)}{escaped_url}{match.group(1)}"

    updated, count = pattern.subn(replace, tag, count=1)
    if count:
        return updated
    return tag[:-1] + f' data-url="{escaped_url}">'


def _is_official_pexels_mp4(direct_url: str) -> bool:
    parsed = urllib.parse.urlparse((direct_url or "").strip())
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "videos.pexels.com"
        and parsed.path.lower().endswith(".mp4")
    )


def _replace_pexels_urls(source: str, direct_by_page: Dict[str, str]) -> Tuple[str, int]:
    updated_cards = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal updated_cards
        tag = match.group(0)
        if _attribute(tag, "data-plat") != "Pexels":
            return tag
        page = _attribute(tag, "data-page")
        direct_url = direct_by_page.get(page or "")
        if not direct_url or not _is_official_pexels_mp4(direct_url):
            return tag
        if match.group(1).lower() == "a":
            updated_cards += 1
        return _with_data_url(tag, direct_url)

    return TAG_RE.sub(replace, source), updated_cards


def _enable_download_button(source: str) -> str:
    button_re = re.compile(r"<[^>]*\bid\s*=\s*(['\"])dlSel\1[^>]*>", re.IGNORECASE | re.DOTALL)

    def replace(match: re.Match[str]) -> str:
        tag = re.sub(r"\s+aria-hidden\s*=\s*(['\"]).*?\1", "", match.group(0), flags=re.IGNORECASE | re.DOTALL)

        def clean_style(style_match: re.Match[str]) -> str:
            style = re.sub(r"(^|;)\s*display\s*:\s*none\s*(?=;|$)", r"\1", style_match.group(2), flags=re.IGNORECASE)
            style = re.sub(r";{2,}", ";", style).strip(" ;")
            return f" style={style_match.group(1)}{style}{style_match.group(1)}" if style else ""

        return re.sub(r"\s+style\s*=\s*(['\"])(.*?)\1", clean_style, tag, flags=re.IGNORECASE | re.DOTALL)

    return button_re.sub(replace, source)


def _update_pexels_notice(source: str) -> str:
    notice_re = re.compile(
        r"(<(?P<tag>[A-Za-z][\w:-]*)\b[^>]*\bid\s*=\s*(['\"])pexelsNotice\3[^>]*>).*?(</(?P=tag)\s*>)",
        re.IGNORECASE | re.DOTALL,
    )
    updated, count = notice_re.subn(r"\1" + PEXELS_NOTICE + r"\4", source, count=1)
    if count:
        return updated
    for old_notice in OLD_PEXELS_NOTICES:
        if old_notice in source:
            return source.replace(old_notice, PEXELS_NOTICE, 1)
    return source


def transform_html(source: str, direct_by_page: Dict[str, str]) -> Tuple[str, int]:
    """替换 Pexels 卡片的 data-url、恢复下载按钮、删除下载保护，保留清洗保护。"""
    output, updated_cards = _replace_pexels_urls(source, direct_by_page)
    output = _enable_download_button(output)
    output = re.sub(
        r"<script\b[^>]*\bid\s*=\s*(['\"])disablePexelsDownloadGuard\1[^>]*>.*?</script\s*>",
        "",
        output,
        flags=re.IGNORECASE | re.DOTALL,
    )
    output = _update_pexels_notice(output)
    return output, updated_cards


def enable_result_dir(result_dir: Path) -> int:
    """从 harvest_pexels.json 建映射，原子写回 filtered.html，返回匹配卡片数。"""
    result_dir = Path(result_dir)
    harvest = json.loads((result_dir / "harvest_pexels.json").read_text(encoding="utf-8"))
    direct_by_page = {
        item["page"]: item["direct_url"]
        for item in harvest
        if item.get("page") and _is_official_pexels_mp4(item.get("direct_url", ""))
    }
    page_path = result_dir / "filtered.html"
    original_mode = stat.S_IMODE(page_path.stat().st_mode)
    output, updated_cards = transform_html(page_path.read_text(encoding="utf-8"), direct_by_page)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=result_dir, delete=False) as temp_file:
            temp_file.write(output)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        os.chmod(temp_path, original_mode)
        os.replace(temp_path, page_path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    return updated_cards


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="将 Pexels 官方下载直链注入结果页")
    parser.add_argument("result_dir", type=Path, help="包含 harvest_pexels.json 与 filtered.html 的目录")
    args = parser.parse_args(argv)
    print(enable_result_dir(args.result_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
