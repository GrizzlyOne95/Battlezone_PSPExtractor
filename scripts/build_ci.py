#!/usr/bin/env python3
"""
Cross-platform CI build helper for BZPSP_Extractor.

Builds a PyInstaller package and produces a zip in ./release.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


APP_NAME = "BZPSP_Extractor"
HIDDEN_IMPORTS = [
    "extractors.extract_psp_txd_textures",
    "extractors.extract_psp_rws_geometry",
    "extractors.extract_psp_audio",
    "extractors.extract_psp_lvl_json",
    "extractors.extract_psp_movies",
    "extractors.extract_psp_data_tables",
    "extractors.extract_psp_font_metrics",
]
FILE_DATAS = [
    "038_PU_Ammo_big.png",
    "background.jpg",
    "THIRD_PARTY_NOTICES.md",
]


def _pair_arg(src: Path, dst: str) -> str:
    sep = ";" if os.name == "nt" else ":"
    return f"{src}{sep}{dst}"


def _find_binary(name: str) -> Path:
    candidates = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        candidates.insert(0, f"{name}.exe")

    for cand in candidates:
        hit = shutil.which(cand)
        if hit:
            return Path(hit).resolve()
    raise RuntimeError(f"Required binary not found in PATH: {name}")


def _build_pyinstaller(repo_root: Path, ffmpeg: Path, ffprobe: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_NAME,
    ]

    if os.name == "nt":
        from PIL import Image

        icon_png = repo_root / "038_PU_Ammo_big.png"
        if not icon_png.exists():
            raise RuntimeError(f"Missing icon source file: {icon_png}")
        icon_ico = repo_root / "build" / "bzpsp_icon.ico"
        icon_ico.parent.mkdir(parents=True, exist_ok=True)
        Image.open(icon_png).save(
            icon_ico,
            format="ICO",
            sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
        )
        cmd.extend(["--icon", str(icon_ico)])

    for module in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", module])

    cmd.extend(["--add-binary", _pair_arg(ffmpeg, ".")])
    cmd.extend(["--add-binary", _pair_arg(ffprobe, ".")])

    for rel in FILE_DATAS:
        src = repo_root / rel
        if not src.exists():
            raise RuntimeError(f"Missing required file: {src}")
        cmd.extend(["--add-data", _pair_arg(src, ".")])

    vendor_dir = repo_root / "vendor"
    if not vendor_dir.exists():
        raise RuntimeError(f"Missing vendor directory: {vendor_dir}")
    cmd.extend(["--add-data", _pair_arg(vendor_dir, "vendor")])

    cmd.append(str(repo_root / "app" / "bzpsp_gui.py"))
    subprocess.run(cmd, cwd=repo_root, check=True)


def _collect_ffmpeg_licenses(ffmpeg: Path, ffprobe: Path, out_dir: Path) -> None:
    license_dir = out_dir / "THIRD_PARTY" / "ffmpeg"
    license_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    found = 0
    for base in {ffmpeg.parent, ffprobe.parent, ffmpeg.parent.parent, ffprobe.parent.parent}:
        for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
            for src in base.glob(pattern):
                if not src.is_file():
                    continue
                key = str(src.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                shutil.copy2(src, license_dir / src.name)
                found += 1

    if found == 0:
        (license_dir / "README.txt").write_text(
            "No FFmpeg license files were auto-detected. Add the correct license files here before redistribution.\n",
            encoding="utf-8",
        )


def _find_dist_items(repo_root: Path) -> list[Path]:
    dist = repo_root / "dist"
    candidates = [
        dist / APP_NAME,
        dist / f"{APP_NAME}.exe",
        dist / f"{APP_NAME}.app",
    ]
    items = [p for p in candidates if p.exists()]
    if items:
        return items

    raise RuntimeError(f"No build output found under: {dist}")


def _stage_and_zip(repo_root: Path, platform_tag: str, ffmpeg: Path, ffprobe: Path) -> Path:
    release_root = repo_root / "release"
    release_root.mkdir(parents=True, exist_ok=True)

    stage_dir = release_root / f"{APP_NAME}-{platform_tag}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    for item in _find_dist_items(repo_root):
        dst = stage_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)

    for doc in ("LICENSE", "README.md", "THIRD_PARTY_NOTICES.md"):
        src = repo_root / doc
        if src.exists():
            shutil.copy2(src, stage_dir / src.name)

    _collect_ffmpeg_licenses(ffmpeg, ffprobe, stage_dir)

    zip_path = release_root / f"{stage_dir.name}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in stage_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(release_root))

    return zip_path


def _normalize_platform(raw: str) -> str:
    value = raw.strip().lower()
    if value in ("windows", "windows-latest", "win32"):
        return "windows"
    if value in ("mac", "macos", "macos-latest", "darwin"):
        return "macos"
    if value in ("linux", "ubuntu", "ubuntu-latest"):
        return "linux"
    return value.replace(" ", "_")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build release package for CI.")
    parser.add_argument(
        "--platform",
        default=os.environ.get("RUNNER_OS", os.name),
        help="Platform label for archive naming.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    ffmpeg = _find_binary("ffmpeg")
    ffprobe = _find_binary("ffprobe")

    _build_pyinstaller(repo_root, ffmpeg, ffprobe)
    zip_path = _stage_and_zip(repo_root, _normalize_platform(args.platform), ffmpeg, ffprobe)
    print(f"Created archive: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

