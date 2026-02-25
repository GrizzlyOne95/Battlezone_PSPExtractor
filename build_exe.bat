@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ICON_PNG=%cd%\038_PU_Ammo_big.png"
set "ICON_ICO=%cd%\build\bzpsp_icon.ico"
set "FFMPEG_EXE="
set "FFPROBE_EXE="
set "DIST_EXE=%cd%\dist\BZPSP_Extractor.exe"
set "DIST_PACKAGE_DIR=%cd%\dist\BZPSP_Extractor"

if not exist "%ICON_PNG%" (
  echo ERROR: Icon source missing: %ICON_PNG%
  exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

if not exist build mkdir build
python -c "from PIL import Image; Image.open(r'%ICON_PNG%').save(r'%ICON_ICO%', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
if errorlevel 1 (
  echo ERROR: Failed to generate ICO from %ICON_PNG%
  exit /b 1
)

if exist "%cd%\ffmpeg.exe" set "FFMPEG_EXE=%cd%\ffmpeg.exe"
if exist "%cd%\ffprobe.exe" set "FFPROBE_EXE=%cd%\ffprobe.exe"
if not defined FFMPEG_EXE for /f "delims=" %%I in ('where.exe ffmpeg.exe 2^>nul') do if not defined FFMPEG_EXE set "FFMPEG_EXE=%%I"
if not defined FFPROBE_EXE for /f "delims=" %%I in ('where.exe ffprobe.exe 2^>nul') do if not defined FFPROBE_EXE set "FFPROBE_EXE=%%I"

if not defined FFMPEG_EXE (
  echo ERROR: ffmpeg.exe not found. Place ffmpeg.exe in this repo root or install it in PATH.
  exit /b 1
)
if not defined FFPROBE_EXE (
  echo ERROR: ffprobe.exe not found. Place ffprobe.exe in this repo root or install it in PATH.
  exit /b 1
)

for %%I in ("%FFMPEG_EXE%") do set "FFMPEG_BIN_DIR=%%~dpI"
for %%I in ("%FFPROBE_EXE%") do set "FFPROBE_BIN_DIR=%%~dpI"
for %%I in ("%FFMPEG_BIN_DIR%..") do set "FFMPEG_PARENT_DIR=%%~fI\"
for %%I in ("%FFPROBE_BIN_DIR%..") do set "FFPROBE_PARENT_DIR=%%~fI\"

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name BZPSP_Extractor ^
  --icon "%ICON_ICO%" ^
  --hidden-import extractors.extract_psp_txd_textures ^
  --hidden-import extractors.extract_psp_rws_geometry ^
  --hidden-import extractors.extract_psp_audio ^
  --hidden-import extractors.extract_psp_lvl_json ^
  --hidden-import extractors.extract_psp_movies ^
  --hidden-import extractors.extract_psp_data_tables ^
  --hidden-import extractors.extract_psp_font_metrics ^
  --add-binary "%FFMPEG_EXE%;." ^
  --add-binary "%FFPROBE_EXE%;." ^
  --add-data "038_PU_Ammo_big.png;." ^
  --add-data "background.jpg;." ^
  --add-data "THIRD_PARTY_NOTICES.md;." ^
  --add-data "vendor;vendor" ^
  app\bzpsp_gui.py
if errorlevel 1 exit /b 1

if not exist "%DIST_EXE%" (
  echo ERROR: Expected build output not found: %DIST_EXE%
  exit /b 1
)

if exist "%DIST_PACKAGE_DIR%" rmdir /s /q "%DIST_PACKAGE_DIR%"
mkdir "%DIST_PACKAGE_DIR%"
copy /y "%DIST_EXE%" "%DIST_PACKAGE_DIR%\BZPSP_Extractor.exe" >nul
copy /y "THIRD_PARTY_NOTICES.md" "%DIST_PACKAGE_DIR%\THIRD_PARTY_NOTICES.md" >nul
if exist "LICENSE" copy /y "LICENSE" "%DIST_PACKAGE_DIR%\LICENSE" >nul

set "FFMPEG_TP_DIR=%DIST_PACKAGE_DIR%\THIRD_PARTY\ffmpeg"
if not exist "%FFMPEG_TP_DIR%" mkdir "%FFMPEG_TP_DIR%"

set "FFMPEG_LICENSE_FOUND=0"
for %%F in (
  "%FFMPEG_BIN_DIR%LICENSE*"
  "%FFMPEG_BIN_DIR%COPYING*"
  "%FFMPEG_BIN_DIR%NOTICE*"
  "%FFMPEG_PARENT_DIR%LICENSE*"
  "%FFMPEG_PARENT_DIR%COPYING*"
  "%FFMPEG_PARENT_DIR%NOTICE*"
  "%FFPROBE_BIN_DIR%LICENSE*"
  "%FFPROBE_BIN_DIR%COPYING*"
  "%FFPROBE_BIN_DIR%NOTICE*"
  "%FFPROBE_PARENT_DIR%LICENSE*"
  "%FFPROBE_PARENT_DIR%COPYING*"
  "%FFPROBE_PARENT_DIR%NOTICE*"
) do (
  if exist "%%~fF" (
    copy /y "%%~fF" "%FFMPEG_TP_DIR%\" >nul
    set "FFMPEG_LICENSE_FOUND=1"
  )
)

if "!FFMPEG_LICENSE_FOUND!"=="0" (
  echo WARNING: No FFmpeg license files were found automatically.
  echo          Add the correct FFmpeg license text files to:
  echo          %FFMPEG_TP_DIR%
)

echo.
echo Build complete.
echo One-file EXE: dist\BZPSP_Extractor.exe
echo Redistributable folder: dist\BZPSP_Extractor\
echo ffmpeg bundled from: %FFMPEG_EXE%
echo ffprobe bundled from: %FFPROBE_EXE%
endlocal
