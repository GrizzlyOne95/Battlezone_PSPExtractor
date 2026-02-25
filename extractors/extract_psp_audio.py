#!/usr/bin/env python3
"""
Extract Battlezone PSP audio assets from USRDIR/audio.

Features:
- Copy all .at3 files (preserving relative paths).
- Parse custom .bnk banks and extract embedded files (typically .vag).
- Optionally decode extracted VAG (PSX ADPCM) to WAV using pure Python.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import struct
import sys
import wave
from dataclasses import dataclass
from pathlib import Path


BNK_ENTRY_NAME_SIZE = 0x40
BNK_ENTRY_SIZE = 0x48
VAG_HEADER_MIN = 0x40
PSX_ADPCM_COEFS = (
    (0, 0),
    (60, 0),
    (115, -52),
    (98, -55),
    (122, -60),
)


@dataclass(frozen=True)
class BnkEntry:
    index: int
    name: str
    size: int
    offset: int


def _safe_name(text: str) -> str:
    if not text:
        return "unnamed"
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    out = out.strip("._")
    return out or "unnamed"


def _parse_bnk_entries(blob: bytes) -> list[BnkEntry]:
    if len(blob) < 4:
        return []
    count = struct.unpack_from("<I", blob, 0)[0]
    table_end = 4 + (count * BNK_ENTRY_SIZE)
    if table_end > len(blob):
        return []

    entries: list[BnkEntry] = []
    for i in range(count):
        off = 4 + (i * BNK_ENTRY_SIZE)
        raw_name = blob[off : off + BNK_ENTRY_NAME_SIZE]
        raw_name = raw_name.split(b"\x00", 1)[0]
        name = raw_name.decode("ascii", errors="ignore").strip()
        size = struct.unpack_from("<I", blob, off + 0x40)[0]
        data_off = struct.unpack_from("<I", blob, off + 0x44)[0]
        entries.append(
            BnkEntry(
                index=i,
                name=name or f"entry_{i:04d}.bin",
                size=size,
                offset=data_off,
            )
        )
    return entries


def _decode_psx_adpcm_block(
    block: bytes, hist1: int, hist2: int
) -> tuple[list[int], int, int]:
    if len(block) != 16:
        return [], hist1, hist2

    pred_shift = block[0]
    predictor = (pred_shift >> 4) & 0x0F
    shift = pred_shift & 0x0F
    coef1, coef2 = PSX_ADPCM_COEFS[predictor] if predictor < len(PSX_ADPCM_COEFS) else (0, 0)

    out: list[int] = []
    for b in block[2:]:
        for nibble in (b & 0x0F, (b >> 4) & 0x0F):
            if nibble >= 8:
                nibble -= 16
            sample = (nibble << 12) >> shift
            sample += ((hist1 * coef1) + (hist2 * coef2) + 32) >> 6
            if sample > 32767:
                sample = 32767
            elif sample < -32768:
                sample = -32768
            out.append(sample)
            hist2 = hist1
            hist1 = sample
    return out, hist1, hist2


def _decode_vag_to_pcm(vag_blob: bytes) -> tuple[int, bytes]:
    if len(vag_blob) < VAG_HEADER_MIN or vag_blob[:4] != b"VAGp":
        raise ValueError("Not a VAGp stream")

    data_size = struct.unpack_from(">I", vag_blob, 0x0C)[0]
    sample_rate = struct.unpack_from(">I", vag_blob, 0x10)[0]
    if sample_rate <= 0:
        sample_rate = 22050

    data_start = 0x40
    max_size = max(0, min(data_size, len(vag_blob) - data_start))
    adpcm = vag_blob[data_start : data_start + max_size]

    pcm = bytearray()
    hist1 = 0
    hist2 = 0
    for i in range(0, len(adpcm) - (len(adpcm) % 16), 16):
        block = adpcm[i : i + 16]
        flags = block[1]
        samples, hist1, hist2 = _decode_psx_adpcm_block(block, hist1, hist2)
        for s in samples:
            pcm += struct.pack("<h", s)
        # End flag often set with bit 0 or bit 2 depending on encoder.
        if flags & 0x01 or flags & 0x04:
            break

    return sample_rate, bytes(pcm)


def _write_wav(path: Path, sample_rate: int, pcm_s16le: bytes, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_s16le)


def _copy_at3_files(audio_root: Path, out_root: Path) -> tuple[int, int]:
    src_files = sorted(audio_root.rglob("*.at3"))
    copied = 0
    failed = 0
    out_at3 = out_root / "at3"
    for src in src_files:
        rel = src.relative_to(audio_root)
        dst = out_at3 / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            copied += 1
        except Exception:
            failed += 1
    return copied, failed


def _extract_bnk_file(
    bnk_path: Path,
    audio_root: Path,
    out_root: Path,
    decode_vag: bool,
) -> tuple[int, int, int]:
    blob = bnk_path.read_bytes()
    entries = _parse_bnk_entries(blob)

    rel_bnk = bnk_path.relative_to(audio_root)
    bank_name = _safe_name(rel_bnk.stem)
    bank_out = out_root / "bnk" / rel_bnk.parent / bank_name
    bank_out.mkdir(parents=True, exist_ok=True)

    csv_path = bank_out / "_index.csv"
    rows: list[list[str]] = [
        ["index", "name", "size", "offset", "magic", "out_file", "wav_file", "status"]
    ]

    extracted = 0
    wav_written = 0
    failed = 0

    used_names: set[str] = set()
    for ent in entries:
        end = ent.offset + ent.size
        if ent.offset < 0 or ent.size <= 0 or end > len(blob):
            rows.append(
                [
                    str(ent.index),
                    ent.name,
                    str(ent.size),
                    str(ent.offset),
                    "",
                    "",
                    "",
                    "invalid_range",
                ]
            )
            failed += 1
            continue

        payload = blob[ent.offset:end]
        safe = _safe_name(Path(ent.name).name)
        if "." not in safe:
            safe += ".bin"
        stem = Path(safe).stem
        suffix = Path(safe).suffix
        out_name = safe
        n = 2
        while out_name.lower() in used_names:
            out_name = f"{stem}_{n}{suffix}"
            n += 1
        used_names.add(out_name.lower())
        out_file = bank_out / out_name
        out_file.write_bytes(payload)
        extracted += 1

        magic = payload[:4].decode("latin1", errors="replace") if len(payload) >= 4 else ""
        wav_file_rel = ""
        status = "ok"

        if decode_vag and payload[:4] == b"VAGp":
            try:
                sample_rate, pcm = _decode_vag_to_pcm(payload)
                wav_path = out_file.with_suffix(".wav")
                _write_wav(wav_path, sample_rate, pcm, channels=1)
                wav_file_rel = wav_path.name
                wav_written += 1
            except Exception:
                status = "decode_failed"
                failed += 1

        rows.append(
            [
                str(ent.index),
                ent.name,
                str(ent.size),
                str(ent.offset),
                magic,
                out_file.name,
                wav_file_rel,
                status,
            ]
        )

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    return extracted, wav_written, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract Battlezone PSP audio (.at3 + .bnk embedded files)."
    )
    parser.add_argument(
        "--audio-root",
        type=Path,
        required=True,
        help="Path to USRDIR/audio folder.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output root for extracted audio.",
    )
    parser.add_argument(
        "--mode",
        choices=("all", "at3", "bnk"),
        default="all",
        help="Extraction mode.",
    )
    parser.add_argument(
        "--no-decode-vag",
        action="store_true",
        help="Do not decode extracted VAG files to WAV.",
    )
    args = parser.parse_args()

    if not args.audio_root.exists():
        print(f"Audio path not found: {args.audio_root}", file=sys.stderr)
        return 2

    args.out_root.mkdir(parents=True, exist_ok=True)

    total_copied = 0
    total_extracted = 0
    total_wav = 0
    total_failed = 0

    if args.mode in ("all", "at3"):
        copied, failed = _copy_at3_files(args.audio_root, args.out_root)
        total_copied += copied
        total_failed += failed
        print(f"[at3] copied={copied} failed={failed}")

    if args.mode in ("all", "bnk"):
        bnk_files = sorted(args.audio_root.rglob("*.bnk"))
        decode_vag = not args.no_decode_vag
        for bnk in bnk_files:
            extracted, wav_written, failed = _extract_bnk_file(
                bnk_path=bnk,
                audio_root=args.audio_root,
                out_root=args.out_root,
                decode_vag=decode_vag,
            )
            total_extracted += extracted
            total_wav += wav_written
            total_failed += failed
            print(
                f"[bnk] {bnk.name}: extracted={extracted} wav={wav_written} failed={failed}"
            )

    print(
        "Done. "
        f"at3_copied={total_copied} "
        f"bnk_extracted={total_extracted} "
        f"wav_written={total_wav} "
        f"failed={total_failed} "
        f"out={args.out_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
