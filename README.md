![hqdefault (1)](https://github.com/user-attachments/assets/29fe1a28-9eaa-4ac9-af67-afefacee60da)

# Battlezone PSP Extractor

Standalone GUI front-end for Battlezone PSP extraction workflows.

<img width="1920" height="1032" alt="Battlezone PSP Extractor" src="https://github.com/user-attachments/assets/06d9034a-d956-4ef4-9f1f-7566d31a962a" />

This app wraps the extraction scripts in `extractors`:
- `extract_psp_txd_textures.py`
- `extract_psp_rws_geometry.py`
- `extract_psp_audio.py`
- `extract_psp_lvl_json.py`
- `extract_psp_movies.py`
- `extract_psp_data_tables.py`
- `extract_psp_font_metrics.py`
- `extract_psp_code.py` (executable code map for reverse engineering)

## Features
- Direct ISO support (`.iso`) with automatic `PSP_GAME/USRDIR` extraction to a local cache.
- Dedicated GUI for textures, geometry, audio, level packages, movies, data tables, and font metrics.
- Battlezone-style visual theme and custom font loading.
- Live log output with per-task status.
- Run single tasks or run all tasks sequentially.
- Uses `038_PU_Ammo_big.png` as app/window icon.
- Saved local config (`bzpsp_gui_config.json`).

## Code Map (reverse engineering)
`extract_psp_code.py` maps the game executable, `PSP_GAME/SYSDIR/BOOT.BIN` (an unencrypted,
stripped PSP PRX; the `~PSP` `EBOOT.BIN` is the encrypted copy of the same program):

```powershell
python extractors\extract_psp_code.py --input <ISO or disc folder or BOOT.BIN> --out-root out\code_map --relocated-elf --listing
```

Outputs:
- `code_map.json` / `code_map.md`: imports (NIDs resolved), functions, call graph, string xrefs,
  source-file attribution from leaked assert paths, function-pointer/vtable tables
- `ghidra_symbols.txt`: names for Ghidra's `ImportSymbolsScript.py`
- `BOOT_relocated.elf` (`--relocated-elf`): PSP relocations applied at load base 0, loads in stock
  Ghidra as `MIPS:LE:32:default`
- `listing.asm` (`--listing`, needs `pip install capstone`): annotated disassembly

Findings are documented in `reverse_engineering/PSP_GAME_CODE.md`.

### Port kit (Unreal / Battlezone 98 Redux)
`reverse_engineering/port/` turns the findings into something you can reimplement:
- `PORT_SPEC.md`: exact tank, weapon and match equations, axis conversions, engine notes and a
  PPSSPP validation plan
- `PORT_AUDIT.md`: every gameplay system with its verification status
- `AI_SPEC.md` (tank AI and controls) and `LEVEL_FORMAT.md` (`.LVL` entity records)
- `bzpsp_constants.json`: code constants with their `BOOT.BIN` and PPSSPP addresses
- `port_tables.json`: tank, tweak, weapon and projectile tables as normalized JSON; rebuild it from
  your own extraction:

```powershell
python scripts\build_port_tables.py --tables <data_tables_json or USRDIR\leveldata> --out port_tables.json
```

- `reference/`: engine-neutral C++17 reference implementation with tests (`cmake -S reverse_engineering/port/reference -B build/ref`, then `ctest --test-dir build/ref`)
- `validation/` + `golden/`: runs the game's own tank, damage and collision-impulse functions from `BOOT.BIN` under
  the Unicorn CPU emulator (`pip install unicorn`) and records traces the reference must match:

```powershell
python reverse_engineering\port\validation\tank_traces.py
python reverse_engineering\port\validation\damage_traces.py
python reverse_engineering\port\validation\contact_traces.py
```

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
cd <path-to-repo>\Battlezone_PSPExtractor
python app\bzpsp_gui.py
```

## Release Builds

The public Windows executable has a stable, versionless name:

- `BZPSPExtractor.exe`

Release archives carry the version and platform, for example:

- `Battlezone_PSPExtractor-v0.1.2-windows.zip`

Official Windows releases use the shared Battlezone tool-suite metadata:

```text
FileDescription: Battlezone PSP Extractor
ProductName: Battlezone Modding Tools
CompanyName: GrizzlyOne95
OriginalFilename: BZPSPExtractor.exe
```

`FileVersion` and `ProductVersion` are derived from the canonical repository version in CI/release builds. Local builds can override the version with `BZPSP_VERSION`.

## Build Standalone EXE (Windows)
```powershell
cd <path-to-repo>\Battlezone_PSPExtractor
build_exe.bat
```

Output folder:
- One-file EXE: `dist\BZPSPExtractor.exe`
- Redistributable folder with notices/licenses: `dist\BZPSPExtractor\`

Build behavior:
- Uses `038_PU_Ammo_big.png` as the EXE icon (converted to `.ico` during build).
- Uses PyInstaller `--onefile` (no required `_internal` folder at runtime).
- Adds Windows file/product version metadata.
- Auto-bundles `ffmpeg.exe` and `ffprobe.exe` into the executable.
  - Looks first in repo root, then in system `PATH`.
  - Build fails if either executable is missing.
- Copies project and third-party notices into the redistributable folder.

To stamp a local build with a version instead of `0.0.0`:

```powershell
$env:BZPSP_VERSION = "0.1.2"
build_exe.bat
```

## GitHub Actions
Workflow file: `.github/workflows/build-release.yml`

Current behavior:
- Builds the packaged app on Windows.
- Uses a PyInstaller `--onefile` build.
- Installs and bundles FFmpeg/FFprobe from the Windows runner.
- Uploads a versioned ZIP artifact containing the stable `BZPSPExtractor.exe` executable plus notices/licenses.
- Pushes to `main` and pull requests build a versioned package using the canonical `VERSION` value.
- A `chore(release): vX.Y.Z` commit or matching `v*` tag creates a GitHub Release and attaches the versioned Windows ZIP.

Triggering a release:
```powershell
git tag v0.1.2
git push origin v0.1.2
```

## Licensing
- Project license: [LICENSE](LICENSE) (MIT)
- Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- DragonFF license: `vendor/DragonFF/LICENSE`

FFmpeg note:
- FFmpeg binaries can be LGPL or GPL depending on how they were built.
- If you redistribute packaged builds, you are responsible for complying with the license terms of the FFmpeg binaries you include.
- The build script tries to copy nearby FFmpeg `LICENSE*`/`COPYING*`/`NOTICE*` files into:
  - `dist\BZPSPExtractor\THIRD_PARTY\ffmpeg\`

## Repo Hygiene (Before Push)
Recommended checks before pushing:

```powershell
python -m compileall app extractors scripts
git status
```

`build/`, `dist/`, `*.spec`, local config, and optional local FFmpeg binaries are ignored by `.gitignore`.

## Notes
- Input and output roots are selected in the GUI; no workspace-specific default paths are hardcoded.

## Credits
- DragonFF authors for establishing a good baseline for extracting TXD/RWS.
- "Null" Software for extensive initial reverse engineering.
