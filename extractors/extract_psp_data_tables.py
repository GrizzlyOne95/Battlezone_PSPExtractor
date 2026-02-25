#!/usr/bin/env python3
"""
Extract gameplay/localization/UI data into structured JSON.

Covers:
- USRDIR/leveldata/*.CSV
- USRDIR/text/*.TXT (localization tokens)
- USRDIR/menu/*.xml
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


LOCAL_RE = re.compile(r"^\{([^}]+)\}<(.+)>$")


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def parse_csv_file(path: Path) -> dict[str, Any]:
    lines = _read_lines(path)
    comments: list[dict[str, Any]] = []
    raw_rows: list[list[str]] = []
    structured_rows: list[dict[str, Any]] = []

    header: list[str] | None = None
    section = ""

    for ln, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            continue

        if s.startswith("#"):
            comment = s[1:].strip()
            comments.append({"line": ln, "text": comment})
            if comment and "," in comment and header is None:
                header = [x.strip() for x in next(csv.reader([comment]))]
            else:
                section = comment
            continue

        row = [x.strip() for x in next(csv.reader([line]))]
        raw_rows.append(row)
        rec: dict[str, Any] = {"line": ln, "values": row}
        if section:
            rec["section"] = section
        if header:
            mapped: dict[str, str] = {}
            for i, cell in enumerate(row):
                key = header[i] if i < len(header) else f"col_{i}"
                mapped[key] = cell
            rec["mapped"] = mapped
        structured_rows.append(rec)

    return {
        "file": path.name,
        "header": header,
        "comment_count": len(comments),
        "row_count": len(raw_rows),
        "comments": comments,
        "rows": structured_rows,
    }


def parse_localization_txt(path: Path) -> dict[str, Any]:
    lines = _read_lines(path)
    entries: dict[str, str] = {}
    unparsed: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        m = LOCAL_RE.match(s)
        if not m:
            unparsed.append(s)
            continue
        key, value = m.group(1), m.group(2)
        entries[key] = value
    return {
        "file": path.name,
        "entries_count": len(entries),
        "entries": entries,
        "unparsed": unparsed,
    }


def parse_menu_xml(path: Path) -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()

    path_items = [elem.text.strip() for elem in root.findall("./Path/Item") if elem.text]
    tex_items = []
    for elem in root.findall("./TextureList/Item"):
        text = (elem.text or "").strip()
        attrs = dict(elem.attrib)
        tex_items.append({"texture": text, "attrs": attrs})

    unique_textures = sorted({item["texture"] for item in tex_items if item["texture"]})
    return {
        "file": path.name,
        "root_tag": root.tag,
        "path_items": path_items,
        "texture_item_count": len(tex_items),
        "unique_texture_count": len(unique_textures),
        "unique_textures": unique_textures,
        "items": tex_items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PSP data tables to JSON.")
    parser.add_argument(
        "--leveldata-root",
        type=Path,
        required=True,
        help="Directory containing CSV gameplay tables.",
    )
    parser.add_argument(
        "--text-root",
        type=Path,
        required=True,
        help="Directory containing localization TXT files.",
    )
    parser.add_argument(
        "--menu-root",
        type=Path,
        required=True,
        help="Directory containing menu XML files.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output directory root.",
    )
    args = parser.parse_args()

    for required in (args.leveldata_root, args.text_root, args.menu_root):
        if not required.exists():
            print(f"Path not found: {required}", file=sys.stderr)
            return 2

    out_csv = args.out_root / "leveldata_csv"
    out_txt = args.out_root / "localization_txt"
    out_xml = args.out_root / "menu_xml"
    out_csv.mkdir(parents=True, exist_ok=True)
    out_txt.mkdir(parents=True, exist_ok=True)
    out_xml.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(args.leveldata_root.glob("*.CSV"))
    txt_files = sorted(args.text_root.glob("*.TXT"))
    xml_files = sorted(args.menu_root.glob("*.xml"))

    csv_ok = 0
    txt_ok = 0
    xml_ok = 0

    for f in csv_files:
        data = parse_csv_file(f)
        (out_csv / f"{f.stem}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        csv_ok += 1
        print(f"[csv] {f.name}: rows={data['row_count']} comments={data['comment_count']}")

    for f in txt_files:
        data = parse_localization_txt(f)
        (out_txt / f"{f.stem}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        txt_ok += 1
        print(f"[txt] {f.name}: entries={data['entries_count']} unparsed={len(data['unparsed'])}")

    for f in xml_files:
        try:
            data = parse_menu_xml(f)
            (out_xml / f"{f.stem}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            xml_ok += 1
            print(f"[xml] {f.name}: textures={data['unique_texture_count']}")
        except Exception as exc:
            print(f"[xml] {f.name}: FAIL {exc}")

    summary = {
        "csv_files": len(csv_files),
        "csv_ok": csv_ok,
        "txt_files": len(txt_files),
        "txt_ok": txt_ok,
        "xml_files": len(xml_files),
        "xml_ok": xml_ok,
    }
    (args.out_root / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(
        f"Done. csv_ok={csv_ok}/{len(csv_files)} txt_ok={txt_ok}/{len(txt_files)} "
        f"xml_ok={xml_ok}/{len(xml_files)} out={args.out_root}"
    )
    return 0 if (csv_ok + txt_ok + xml_ok) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
