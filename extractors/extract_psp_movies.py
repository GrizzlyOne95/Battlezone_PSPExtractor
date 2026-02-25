#!/usr/bin/env python3
"""
Extract/convert Battlezone PSP PMF movie assets.

Modes:
- copy:      copy .pmf files to output
- probe:     write ffprobe JSON metadata
- transcode: convert PMF to MP4 using ffmpeg
- all:       run copy + probe + transcode
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout


def _probe(ffprobe_exe: str, src: Path, out_json: Path) -> tuple[bool, str]:
    code, out = _run(
        [
            ffprobe_exe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(src),
        ]
    )
    if code != 0:
        return False, out.strip()
    try:
        data = json.loads(out)
    except Exception:
        return False, "ffprobe returned non-JSON output"
    out_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return True, ""


def _transcode(ffmpeg_exe: str, src: Path, out_mp4: Path, overwrite: bool) -> tuple[bool, str]:
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(out_mp4),
    ]
    code, out = _run(cmd)
    if code != 0:
        return False, out.strip()
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract/probe/transcode PSP PMF movies.")
    parser.add_argument(
        "--movie-root",
        type=Path,
        required=True,
        help="Directory containing .pmf files.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output directory root.",
    )
    parser.add_argument(
        "--mode",
        choices=("copy", "probe", "transcode", "all"),
        default="all",
        help="Operation mode.",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="ffmpeg executable path.",
    )
    parser.add_argument(
        "--ffprobe",
        default="ffprobe",
        help="ffprobe executable path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional file limit (0 = all).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing transcode outputs.",
    )
    args = parser.parse_args()

    if not args.movie_root.exists():
        print(f"Movie path not found: {args.movie_root}", file=sys.stderr)
        return 2

    files = sorted(args.movie_root.glob("*.pmf"))
    if args.limit > 0:
        files = files[: args.limit]

    out_pmf = args.out_root / "pmf"
    out_probe = args.out_root / "probe_json"
    out_mp4 = args.out_root / "mp4"
    out_pmf.mkdir(parents=True, exist_ok=True)
    out_probe.mkdir(parents=True, exist_ok=True)
    out_mp4.mkdir(parents=True, exist_ok=True)

    ok_probe = 0
    ok_copy = 0
    ok_trans = 0
    fail = 0

    for src in files:
        print(f"{src.name}:")

        if args.mode in ("copy", "all"):
            try:
                shutil.copy2(src, out_pmf / src.name)
                ok_copy += 1
                print("  copy: ok")
            except Exception as exc:
                fail += 1
                print(f"  copy: FAIL {exc}")

        if args.mode in ("probe", "all"):
            ok, err = _probe(args.ffprobe, src, out_probe / f"{src.stem}.json")
            if ok:
                ok_probe += 1
                print("  probe: ok")
            else:
                fail += 1
                print(f"  probe: FAIL {err}")

        if args.mode in ("transcode", "all"):
            ok, err = _transcode(args.ffmpeg, src, out_mp4 / f"{src.stem}.mp4", args.overwrite)
            if ok:
                ok_trans += 1
                print("  transcode: ok")
            else:
                fail += 1
                print(f"  transcode: FAIL {err}")

    summary = {
        "files_total": len(files),
        "mode": args.mode,
        "copy_ok": ok_copy,
        "probe_ok": ok_probe,
        "transcode_ok": ok_trans,
        "failed_ops": fail,
        "out_root": str(args.out_root),
    }
    (args.out_root / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"Done. files={len(files)} copy_ok={ok_copy} probe_ok={ok_probe} "
        f"transcode_ok={ok_trans} failed_ops={fail} out={args.out_root}"
    )
    return 0 if (ok_copy + ok_probe + ok_trans) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
