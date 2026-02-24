#!/usr/bin/env python3
"""
构建期前端资源混淆（轻量版）

- 压缩 panel.html 内联 CSS（去注释 + 折叠空白）
- 将内联 JS 用 Base64 包裹后通过 eval(atob(...)) 执行

目标是提升静态阅读门槛，不改变运行时逻辑。
"""

from __future__ import annotations

import base64
import pathlib
import re
import sys


STYLE_RE = re.compile(r"(<style>\s*)(.*?)(\s*</style>)", re.S | re.I)
SCRIPT_RE = re.compile(r"(<script>\s*)(.*?)(\s*</script>)", re.S | re.I)


def _minify_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,])\s*", r"\1", css)
    css = re.sub(r";}", "}", css)
    return css.strip()


def _obfuscate_js(js: str) -> str:
    encoded = base64.b64encode(js.encode("utf-8")).decode("ascii")
    chunks = [encoded[i : i + 120] for i in range(0, len(encoded), 120)]
    joined = ",".join(f'"{c}"' for c in chunks)
    return (
        "(function(){"
        f"const __p=[{joined}].join('');"
        "const __b=atob(__p);"
        "let __s='';"
        "if(typeof TextDecoder!=='undefined'){"
        "const __u=Uint8Array.from(__b,c=>c.charCodeAt(0));"
        "__s=new TextDecoder('utf-8').decode(__u);"
        "}else{"
        "__s=decodeURIComponent(Array.prototype.map.call(__b,c=>'%'+('00'+c.charCodeAt(0).toString(16)).slice(-2)).join(''));"
        "}"
        "(0,eval)(__s);"
        "})();"
    )


def build_release_panel_html(src_text: str) -> str:
    style_match = STYLE_RE.search(src_text)
    if not style_match:
        raise ValueError("未找到 <style> 块")
    css = style_match.group(2)
    src_text = src_text[: style_match.start()] + style_match.group(1) + _minify_css(css) + style_match.group(3) + src_text[style_match.end() :]

    script_match = SCRIPT_RE.search(src_text)
    if not script_match:
        raise ValueError("未找到 <script> 块")
    js = script_match.group(2)
    obf_js = _obfuscate_js(js)
    src_text = src_text[: script_match.start()] + script_match.group(1) + obf_js + script_match.group(3) + src_text[script_match.end() :]

    return src_text


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: obfuscate_panel.py <input_panel.html> <output_panel.html>", file=sys.stderr)
        return 2

    src = pathlib.Path(sys.argv[1])
    dst = pathlib.Path(sys.argv[2])
    text = src.read_text(encoding="utf-8")
    out = build_release_panel_html(text)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")
    print(f"[obfuscate] wrote: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
