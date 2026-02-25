# Third-Party Notices

This project includes or depends on third-party software.

## FFmpeg
- Website: https://ffmpeg.org/
- License: FFmpeg is distributed under LGPL or GPL terms depending on how the binaries are built.

### Distribution note
When distributing packaged builds that include `ffmpeg.exe` and `ffprobe.exe`, you must comply with the license terms of the specific FFmpeg binaries you bundle.

At minimum:
- Include the applicable FFmpeg license text(s) with your distribution.
- Preserve required copyright and notice information.
- If your FFmpeg build is GPL-licensed, comply with GPL obligations for redistribution.
- If your FFmpeg build is LGPL-licensed, comply with LGPL obligations for redistribution.

The build script attempts to copy nearby FFmpeg license files into:
- `dist\BZPSP_Extractor\THIRD_PARTY\ffmpeg\`

If no license files are detected automatically, add them manually before redistributing.

## DragonFF
- Source location in this repo: `vendor/DragonFF`
- License file: `vendor/DragonFF/LICENSE`

