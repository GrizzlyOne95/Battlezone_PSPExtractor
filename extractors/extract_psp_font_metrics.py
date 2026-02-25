#!/usr/bin/env python3
"""
Extract PSP bitmap font metric files (*.met) into JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


GLYPH_RE = re.compile(r"^\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)(?:\s*#\s*(.*))?$")


def _decode_comment_char(comment: str) -> str | None:
    # comments often look like: 'A'
    m = re.search(r"'(.+)'", comment)
    if not m:
        return None
    return m.group(1)


def parse_met(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 3:
        raise ValueError("MET file too short")
    signature = lines[0].strip()
    if signature != "METRICS1":
        raise ValueError(f"Unexpected signature: {signature}")

    atlas_line = lines[1].strip()
    header_line = lines[2].strip()

    glyphs: list[dict[str, Any]] = []
    unparsed: list[dict[str, Any]] = []
    for i, line in enumerate(lines[3:], start=4):
        s = line.rstrip()
        if not s:
            continue
        m = GLYPH_RE.match(s)
        if not m:
            unparsed.append({"line": i, "text": s})
            continue

        code = int(m.group(1))
        x0 = int(m.group(2))
        y0 = int(m.group(3))
        x1 = int(m.group(4))
        y1 = int(m.group(5))
        comment = (m.group(6) or "").strip()
        char_hint = _decode_comment_char(comment) if comment else None
        if char_hint is None and 0 <= code <= 0x10FFFF:
            try:
                char_hint = chr(code)
            except Exception:
                char_hint = None

        glyphs.append(
            {
                "codepoint": code,
                "char": char_hint,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "width": x1 - x0,
                "height": y1 - y0,
                "comment": comment,
            }
        )

    atlas_tokens = atlas_line.split()
    atlas_candidates = []
    for tok in atlas_tokens:
        p = Path(tok)
        if p.suffix:
            atlas_candidates.append(p.with_suffix(".png").name)
            atlas_candidates.append(p.name)
        else:
            atlas_candidates.append(f"{tok}.png")
            atlas_candidates.append(tok)

    atlas_found = None
    for name in atlas_candidates:
        cand = path.parent / name
        if cand.exists():
            atlas_found = cand.name
            break

    return {
        "file": path.name,
        "signature": signature,
        "atlas_line": atlas_line,
        "atlas_candidates": atlas_candidates,
        "atlas_found": atlas_found,
        "header_line": header_line,
        "glyph_count": len(glyphs),
        "glyphs": glyphs,
        "unparsed_lines": unparsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PSP .met font metrics to JSON.")
    parser.add_argument(
        "--font-root",
        type=Path,
        required=True,
        help="Directory containing *.met files.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output directory for JSON files.",
    )
    args = parser.parse_args()

    if not args.font_root.exists():
        print(f"Font path not found: {args.font_root}", file=sys.stderr)
        return 2

    files = sorted(args.font_root.glob("*.met"))
    args.out_root.mkdir(parents=True, exist_ok=True)

    ok = 0
    failed = 0
    summary_files: list[dict[str, Any]] = []
    for f in files:
        try:
            data = parse_met(f)
            (args.out_root / f"{f.stem}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            ok += 1
            summary_files.append(
                {
                    "file": data["file"],
                    "glyph_count": data["glyph_count"],
                    "atlas_found": data["atlas_found"],
                }
            )
            print(f"{f.name}: glyphs={data['glyph_count']} atlas={data['atlas_found']}")
        except Exception as exc:
            failed += 1
            print(f"{f.name}: FAIL {exc}")

    (args.out_root / "_summary.json").write_text(
        json.dumps(
            {
                "files_total": len(files),
                "ok": ok,
                "failed": failed,
                "files": summary_files,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Done. met_files={len(files)} ok={ok} failed={failed} out={args.out_root}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
