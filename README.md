
![hqdefault (1)](https://github.com/user-attachments/assets/29fe1a28-9eaa-4ac9-af67-afefacee60da)

# BZ PSP Extractor

Standalone GUI front-end for Battlezone PSP extraction workflows.

<img width="1920" height="1032" alt="image" src="https://github.com/user-attachments/assets/06d9034a-d956-4ef4-9f1f-7566d31a962a" />


This app wraps the extraction scripts in `extractors`:
- `extract_psp_txd_textures.py`
- `extract_psp_rws_geometry.py`
- `extract_psp_audio.py`
- `extract_psp_lvl_json.py`
- `extract_psp_movies.py`
- `extract_psp_data_tables.py`
- `extract_psp_font_metrics.py`

## Features
- Direct ISO support (`.iso`) with automatic `PSP_GAME/USRDIR` extraction to a local cache.
- Dedicated GUI for textures, geometry, audio, level packages, movies, data tables, and font metrics.
- Battlezone-style visual theme and custom font loading.
- Live log output with per-task status.
- Run single tasks or run all tasks sequentially.
- Uses `038_PU_Ammo_big.png` as app/window icon.
- Saved local config (`bzpsp_gui_config.json`).

## Source Requirements
- Python 3.12+ on Windows
- `pip install -r requirements.txt`
- For building EXE: `pip install -r requirements-build.txt`

Runtime/build dependencies:
- Python packages: `Pillow`, `pycdlib`
- External binaries: `ffmpeg.exe`, `ffprobe.exe`
  - Needed for movie modes `probe`, `transcode`, and `all`
  - Not needed for movie mode `copy`

## Run
```powershell
cd <path-to-repo>\BZPSP_Extractor
python app\bzpsp_gui.py
```

## Build Standalone EXE (Windows)
```powershell
cd <path-to-repo>\BZPSP_Extractor
build_exe.bat
```

Output folder:
- One-file EXE: `dist\BZPSP_Extractor.exe`
- Redistributable folder with notices/licenses: `dist\BZPSP_Extractor\`

Build behavior:
- Uses `038_PU_Ammo_big.png` as the EXE icon (converted to `.ico` during build)
- Uses PyInstaller `--onefile` (no required `_internal` folder at runtime)
- Auto-bundles `ffmpeg.exe` and `ffprobe.exe` into the executable
  - Looks first in repo root, then in system `PATH`
  - Build fails if either executable is missing
- Copies project and third-party notices into the build output

## GitHub Actions (Cross-Platform)
Workflow file: `.github/workflows/build-release.yml`

What it does:
- Builds packaged app artifacts on:
  - Windows
  - macOS
  - Linux
- Uses PyInstaller `--onefile` builds
- Bundles FFmpeg binaries found on each runner
- Uploads zipped build artifacts
- On tag pushes matching `v*`, creates a GitHub Release and attaches all platform zips

Triggering a release:
```powershell
git tag v0.1.0
git push origin v0.1.0
```

## Licensing
- Project license: [LICENSE](LICENSE) (MIT)
- Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- DragonFF license: `vendor/DragonFF/LICENSE`

FFmpeg note:
- FFmpeg binaries can be LGPL or GPL depending on how they were built.
- If you redistribute packaged builds, you are responsible for complying with the license terms of the FFmpeg binaries you include.
- The build script tries to copy nearby FFmpeg `LICENSE*`/`COPYING*`/`NOTICE*` files into:
  - `dist\BZPSP_Extractor\THIRD_PARTY\ffmpeg\`

## Repo Hygiene (Before Push)
Recommended checks before pushing:

```powershell
python -m compileall app extractors
git status
```

`build/`, `dist/`, `*.spec`, local config, and optional local FFmpeg binaries are ignored by `.gitignore`.

## Notes
- Input and output roots are selected in the GUI; no workspace-specific default paths are hardcoded.

## Credits
- DragonFF authors for establishing a good baseline for extracting TXD/RWS
- "Null" Software for extensive initial reverse engineering 
