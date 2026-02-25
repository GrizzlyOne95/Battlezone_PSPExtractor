#!/usr/bin/env python3
"""
Extract Battlezone PSP .LVL (BZPK) mission/map packages to JSON.

The parser models the BZPK stream as:
- 16-byte file header
- sequence of entries, each: <u32 size><u32 id><payload bytes>
- optional 0x00000000 padding words between sibling entries
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAGIC = b"BZPK"


@dataclass
class BzpkEntry:
    offset: int
    size: int
    entry_id: int
    payload_start: int
    payload_end: int
    children: list["BzpkEntry"] | None = None


def _id_hex(entry_id: int) -> str:
    return f"0x{entry_id:08X}"


def _id_signed(entry_id: int) -> int:
    return entry_id if entry_id < 0x80000000 else entry_id - 0x100000000


def _skip_zero_words(blob: bytes, pos: int, end: int) -> int:
    while pos + 4 <= end and struct.unpack_from("<I", blob, pos)[0] == 0:
        pos += 4
    return pos


def _parse_entries(blob: bytes, start: int, end: int) -> tuple[list[BzpkEntry], int]:
    entries: list[BzpkEntry] = []
    pos = start
    while pos + 8 <= end:
        pos = _skip_zero_words(blob, pos, end)
        if pos + 8 > end:
            break
        size, entry_id = struct.unpack_from("<II", blob, pos)
        if size < 8 or (pos + size) > end:
            raise ValueError(f"Invalid entry at 0x{pos:X}: size={size}")
        payload_start = pos + 8
        payload_end = pos + size
        entries.append(
            BzpkEntry(
                offset=pos,
                size=size,
                entry_id=entry_id,
                payload_start=payload_start,
                payload_end=payload_end,
                children=None,
            )
        )
        pos = payload_end
    pos = _skip_zero_words(blob, pos, end)
    return entries, pos


def _try_parse_children(blob: bytes, entry: BzpkEntry) -> list[BzpkEntry] | None:
    try:
        children, final_pos = _parse_entries(blob, entry.payload_start, entry.payload_end)
    except Exception:
        return None
    if not children:
        return None
    if final_pos != entry.payload_end:
        return None
    return children


def _strip_bf_padding(data: bytes) -> bytes:
    # Common padding observed in LVL strings: 0xBF 0xBF ...
    end = len(data)
    while end > 0 and data[end - 1] in (0x00, 0xBF):
        end -= 1
    return data[:end]


def _decode_scalar_payload(data: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"raw_hex": data.hex()}

    trimmed = _strip_bf_padding(data)
    if trimmed:
        is_ascii = all(32 <= b < 127 for b in trimmed)
        if is_ascii:
            text = trimmed.decode("ascii", errors="replace")
            out["kind"] = "string"
            out["value"] = text
            return out

    if len(data) == 4:
        u = struct.unpack_from("<I", data, 0)[0]
        s = struct.unpack_from("<i", data, 0)[0]
        f = struct.unpack_from("<f", data, 0)[0]
        out["kind"] = "u32_f32"
        out["u32"] = u
        out["i32"] = s
        if math.isfinite(f):
            out["f32"] = round(f, 6)
        else:
            out["f32"] = str(f)
        return out

    if len(data) in (8, 12, 16) and (len(data) % 4 == 0):
        vals = list(struct.unpack("<" + ("f" * (len(data) // 4)), data))
        out["kind"] = f"f32x{len(vals)}"
        out["f32"] = [round(v, 6) if math.isfinite(v) else str(v) for v in vals]
        return out

    if len(data) % 4 == 0 and len(data) <= 64:
        uvals = list(struct.unpack("<" + ("I" * (len(data) // 4)), data))
        out["kind"] = f"u32x{len(uvals)}"
        out["u32"] = uvals
        return out

    out["kind"] = "blob"
    out["size"] = len(data)
    return out


def _entry_to_json(blob: bytes, entry: BzpkEntry) -> dict[str, Any]:
    node: dict[str, Any] = {
        "offset": entry.offset,
        "size": entry.size,
        "id_u32": entry.entry_id,
        "id_i32": _id_signed(entry.entry_id),
        "id_hex": _id_hex(entry.entry_id),
    }
    if entry.children is not None:
        node["children"] = [_entry_to_json(blob, ch) for ch in entry.children]
    else:
        data = blob[entry.payload_start : entry.payload_end]
        node["value"] = _decode_scalar_payload(data)
    return node


def _walk_collect(node: dict[str, Any], strings: list[str], rws_refs: list[str]) -> None:
    value = node.get("value")
    if isinstance(value, dict) and value.get("kind") == "string":
        s = value.get("value")
        if isinstance(s, str):
            strings.append(s)
            if s.lower().endswith(".rws"):
                rws_refs.append(s)
    children = node.get("children")
    if isinstance(children, list):
        for ch in children:
            if isinstance(ch, dict):
                _walk_collect(ch, strings, rws_refs)


def _infer_object_info(node: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {}
    children = node.get("children")
    if not isinstance(children, list):
        return info

    class_name = None
    obj_name = None
    for ch in children:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("id_u32")
        value = ch.get("value")
        if not isinstance(value, dict) or value.get("kind") != "string":
            continue
        text = value.get("value")
        if not isinstance(text, str):
            continue
        if cid == 0x20000000 and class_name is None:
            class_name = text
        elif cid == 0x80000000 and obj_name is None:
            obj_name = text

    if class_name:
        info["class"] = class_name
    if obj_name:
        info["name"] = obj_name
    return info


def extract_one_lvl(lvl_path: Path, out_dir: Path) -> tuple[bool, dict[str, Any]]:
    blob = lvl_path.read_bytes()
    if len(blob) < 16:
        return False, {"error": "File too small"}
    if blob[:4] != MAGIC:
        return False, {"error": "Missing BZPK header"}

    declared_size = struct.unpack_from("<I", blob, 4)[0]
    header_unk = struct.unpack_from("<I", blob, 8)[0]
    object_count = struct.unpack_from("<I", blob, 12)[0]

    try:
        top_entries, final_pos = _parse_entries(blob, 0x10, len(blob))
    except Exception as exc:
        return False, {"error": str(exc)}

    for ent in top_entries:
        ent.children = _try_parse_children(blob, ent)

    nodes = [_entry_to_json(blob, ent) for ent in top_entries]
    objects: list[dict[str, Any]] = []
    all_strings: list[str] = []
    all_rws: list[str] = []

    for i, node in enumerate(nodes):
        infer = _infer_object_info(node)
        refs: list[str] = []
        strs: list[str] = []
        _walk_collect(node, strs, refs)
        all_strings.extend(strs)
        all_rws.extend(refs)
        objects.append(
            {
                "index": i,
                "offset": node["offset"],
                "size": node["size"],
                "class": infer.get("class"),
                "name": infer.get("name"),
                "rws_refs": sorted(set(refs)),
                "node": node,
            }
        )

    summary = {
        "file": lvl_path.name,
        "declared_size": declared_size,
        "actual_size": len(blob),
        "header_unknown": header_unk,
        "header_object_count": object_count,
        "parsed_object_count": len(top_entries),
        "parse_final_offset": final_pos,
        "unique_rws_refs": sorted(set(all_rws)),
        "string_count": len(all_strings),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{lvl_path.stem}.json"
    out_file.write_text(
        json.dumps(
            {
                "summary": summary,
                "objects": objects,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return True, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract .LVL BZPK files to JSON.")
    parser.add_argument(
        "--lvl-root",
        type=Path,
        required=True,
        help="Directory containing .LVL files.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output directory for .json files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional file limit (0 = all).",
    )
    args = parser.parse_args()

    if not args.lvl_root.exists():
        print(f"LVL path not found: {args.lvl_root}", file=sys.stderr)
        return 2

    files = sorted(args.lvl_root.glob("*.LVL"))
    if args.limit > 0:
        files = files[: args.limit]

    args.out_root.mkdir(parents=True, exist_ok=True)
    ok_count = 0
    fail_count = 0
    summaries: list[dict[str, Any]] = []

    for lvl in files:
        ok, info = extract_one_lvl(lvl, args.out_root)
        if ok:
            ok_count += 1
            summaries.append(info)
            print(
                f"{lvl.name}: objects={info['parsed_object_count']} "
                f"rws_refs={len(info['unique_rws_refs'])}"
            )
        else:
            fail_count += 1
            err = info.get("error", "unknown error")
            print(f"{lvl.name}: FAIL {err}")

    summary_path = args.out_root / "_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "files_total": len(files),
                "ok": ok_count,
                "failed": fail_count,
                "files": summaries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"Done. lvl_files={len(files)} ok={ok_count} failed={fail_count} out={args.out_root}"
    )
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
