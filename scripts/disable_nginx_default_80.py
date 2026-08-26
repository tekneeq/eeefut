#!/usr/bin/env python3
"""Comment stock ``server { listen 80; }`` blocks out of nginx.conf.

Amazon Linux writes ``listen       80;`` (many spaces). A naive
``\"listen 80\" in text`` check misses that and leaves a conflicting
``server_name _`` on :80.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

MARKER = "eeefut: default :80 server disabled"
LISTEN_80 = re.compile(r"listen\s+(\[::\]:)?80\b")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _listens_on_80(block: str) -> bool:
    for line in block.splitlines():
        if _is_comment(line):
            continue
        if LISTEN_80.search(line):
            return True
    return False


def _is_server_start(line: str) -> bool:
    stripped = line.lstrip()
    if _is_comment(line):
        return False
    return stripped.startswith("server") and (
        stripped == "server" or stripped.startswith("server ") or stripped.startswith("server{") or stripped.startswith("server\t")
    )


def disable_default_80(text: str, *, marker: str = MARKER) -> tuple[str, int]:
    """Return (patched_text, commented_block_count)."""
    if marker in text:
        return text, 0

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    disabled = 0
    while i < len(lines):
        raw = lines[i]
        if _is_server_start(raw):
            block = [raw]
            depth = raw.count("{") - raw.count("}")
            i += 1
            # ``server`` and ``{`` may be on separate lines
            while i < len(lines) and depth <= 0 and "{" not in "".join(block):
                block.append(lines[i])
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            while i < len(lines) and depth > 0:
                block.append(lines[i])
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            body = "".join(block)
            if _listens_on_80(body) and "eeefut_dashboard" not in body:
                out.append(f"# {marker}\n")
                for line in block:
                    nl = "" if line.endswith("\n") else "\n"
                    out.append("# " + line + nl)
                disabled += 1
                continue
            out.extend(block)
            continue
        out.append(raw)
        i += 1
    return "".join(out), disabled


def patch_file(path: Path, *, marker: str = MARKER) -> int:
    original = path.read_text()
    patched, disabled = disable_default_80(original, marker=marker)
    if disabled:
        path.write_text(patched)
    return disabled


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("nginx_conf", type=Path, nargs="?", default=Path("/etc/nginx/nginx.conf"))
    p.add_argument("--marker", default=MARKER)
    args = p.parse_args(argv)
    if not args.nginx_conf.is_file():
        print(f"ERROR: missing {args.nginx_conf}", flush=True)
        return 2
    if args.marker in args.nginx_conf.read_text():
        print(f"already patched ({args.marker})")
        return 0
    disabled = patch_file(args.nginx_conf, marker=args.marker)
    if disabled == 0:
        print("WARNING: nginx.conf mentions :80 but no server block was commented")
        return 1
    print(f"commented {disabled} default :80 server block(s) in {args.nginx_conf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
