#!/usr/bin/env python3
"""
Extract PSP-native RenderWare TXD textures to PNG.

This script uses DragonFF's GTA library TXD/PSP texture parsers and applies
an unswizzle safety fallback for edge-case textures.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path
from typing import Iterator, Tuple

from PIL import Image


def _safe_unswizzle(data: bytes, width: int, height: int, depth: int) -> bytes:
    """Conservative PSP unswizzle with bounds safety fallback."""
    byte_width = (width * depth) >> 3
    if byte_width <= 0 or height <= 0:
        return data
    if byte_width < 16 or height < 8:
        return data

    row_blocks = byte_width // 16
    if row_blocks <= 0:
        return data

    block_size = 16 * 8
    res = bytearray(byte_width * height)
    max_src = len(data)

    for y in range(height):
        block_y = y // 8
        y_in_block = y % 8
        for x in range(byte_width):
            block_x = x // 16
            x_in_block = x % 16
            block_idx = block_x + (block_y * row_blocks)
            src_off = (block_idx * block_size) + x_in_block + (y_in_block * 16)
            dst_off = (y * byte_width) + x
            if src_off >= max_src:
                return data
            res[dst_off] = data[src_off]

    return bytes(res)


def _iter_chunks(
    blob: bytes, start: int, end: int
) -> Iterator[Tuple[int, int, int, int, int]]:
    """Yield (chunk_type, chunk_size, chunk_ver, payload_start, payload_end)."""
    pos = start
    lim = min(end, len(blob))
    while pos + 12 <= lim:
        chunk_type, chunk_size, chunk_ver = struct.unpack_from("<III", blob, pos)
        payload_start = pos + 12
        payload_end = payload_start + chunk_size
        if payload_end > lim:
            break
        yield chunk_type, chunk_size, chunk_ver, payload_start, payload_end
        pos = payload_end


def _safe_name(text: str) -> str:
    if not text:
        return "unnamed"
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return out.strip("._") or "unnamed"


def _png_name_for_texture(tex_name: str) -> str:
    stem = Path(tex_name).stem
    stem = _safe_name(stem)
    return f"{stem}.png"


def extract_one_txd(
    txd_path: Path,
    out_dir: Path,
    NativePSPTexture,
    flat_out_dir: Path | None = None,
) -> tuple[int, int]:
    """Extract one TXD file. Returns (ok_count, fail_count)."""
    raw = txd_path.read_bytes()
    if len(raw) < 12:
        return 0, 1

    root_type, root_size, _root_ver = struct.unpack_from("<III", raw, 0)
    if root_type != 0x16:  # Texture Dictionary
        return 0, 1

    root_start = 12
    root_end = root_start + root_size

    ok = 0
    fail = 0
    tex_idx = 0

    for chunk_type, _chunk_size, _chunk_ver, payload_start, payload_end in _iter_chunks(
        raw, root_start, root_end
    ):
        if chunk_type != 0x15:  # Texture Native
            continue

        if payload_start + 12 > payload_end:
            fail += 1
            continue

        inner_type, inner_size, _inner_ver = struct.unpack_from("<III", raw, payload_start)
        if inner_type != 0x01:  # Struct
            fail += 1
            continue

        inner_payload = payload_start + 12
        inner_end = inner_payload + inner_size
        if inner_end > payload_end:
            fail += 1
            continue

        blob = raw[inner_payload:inner_end]
        try:
            tex = NativePSPTexture.from_mem(blob)
            rgba = tex.to_rgba(0)
            name = _safe_name(getattr(tex, "name", "") or f"tex_{tex_idx:03d}")
            out_file = out_dir / f"{tex_idx:03d}_{name}.png"
            image = Image.frombytes("RGBA", (tex.width, tex.height), rgba)
            image.save(out_file)

            if flat_out_dir is not None:
                flat_file = flat_out_dir / _png_name_for_texture(name)
                if not flat_file.exists():
                    image.save(flat_file)
            ok += 1
        except Exception as exc:  # pragma: no cover - per-file resilience
            err_file = out_dir / f"{tex_idx:03d}_ERROR.txt"
            err_file.write_text(str(exc), encoding="utf-8")
            fail += 1
        tex_idx += 1

    return ok, fail


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PSP TXD textures to PNG.")
    parser.add_argument(
        "--dragonff-root",
        type=Path,
        required=True,
        help="Path to DragonFF repo root.",
    )
    parser.add_argument(
        "--txd-root",
        type=Path,
        required=True,
        help="Directory containing .txd files.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output directory for extracted PNGs.",
    )
    parser.add_argument(
        "--flat-out-root",
        type=Path,
        default=None,
        help="Optional flat output directory for texture-name PNG aliases used by OBJ/MTL.",
    )
    args = parser.parse_args()

    if not args.dragonff_root.exists():
        print(f"DragonFF path not found: {args.dragonff_root}", file=sys.stderr)
        return 2
    if not args.txd_root.exists():
        print(f"TXD path not found: {args.txd_root}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(args.dragonff_root))
    from gtaLib import native_psp  # type: ignore

    # Monkey patch: handle edge-case mip levels safely.
    native_psp.NativePSPTexture.unswizzle = staticmethod(_safe_unswizzle)
    NativePSPTexture = native_psp.NativePSPTexture

    args.out_root.mkdir(parents=True, exist_ok=True)
    if args.flat_out_root is not None:
        args.flat_out_root.mkdir(parents=True, exist_ok=True)

    total_ok = 0
    total_fail = 0
    files = sorted(args.txd_root.glob("*.txd"))
    for txd_file in files:
        per_out = args.out_root / txd_file.stem
        per_out.mkdir(parents=True, exist_ok=True)
        ok, fail = extract_one_txd(txd_file, per_out, NativePSPTexture, args.flat_out_root)
        total_ok += ok
        total_fail += fail
        print(f"{txd_file.name}: ok={ok} fail={fail}")

    print(
        f"Done. txd_files={len(files)} extracted={total_ok} failed={total_fail} out={args.out_root}"
    )
    return 0 if total_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
