#!/usr/bin/env python3
"""Inventory a Battlezone PSP BOOT.BIN without modifying it.

This intentionally uses only Python's standard library. It reports:
- SHA-256
- basic ELF32 little-endian header information
- section names / addresses / offsets
- first PT_LOAD mapping
- selected Battlezone PSP source/resource string anchors

It does not decrypt EBOOT.BIN, apply PSP relocations, or distribute game data.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1


ANCHORS = (
    "c:/Trees/Psp/Game/Source/game.cpp",
    "c:/Trees/Psp/Game/Source/GameHotZone.cpp",
    "c:/Trees/Psp/Game/Source/GameType.cpp",
    "c:/Trees/Psp/Game/Source/Online.cpp",
    "c:/Trees/Psp/Game/Source/NetUtil.cpp",
    "ProjectileMgr.cpp",
    "WeaponMgr.cpp",
    "HoverTank.cpp",
    "Creating SubGame Type",
    "Game Type Creation completed",
    "GM_KO_core.rws",
    "GM_HZ_Pad.rws",
    "GM_Fox_Flag.rws",
    "GM_CTF_Flag.rws",
    "GM_CTF_FlagStand.rws",
    "HotZonePad",
    "FlagStand",
    "Respawn",
)


def c_string(blob: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(blob):
        return ""
    end = blob.find(b"\0", offset)
    if end < 0:
        end = len(blob)
    return blob[offset:end].decode("ascii", errors="replace")


def parse_elf32_le(blob: bytes) -> dict:
    if len(blob) < 52 or blob[:4] != ELF_MAGIC:
        raise ValueError("not an ELF file")
    if blob[4] != ELFCLASS32:
        raise ValueError("expected ELF32")
    if blob[5] != ELFDATA2LSB:
        raise ValueError("expected little-endian ELF")

    fields = struct.unpack_from("<16sHHIIIIIHHHHHH", blob, 0)
    (
        ident,
        e_type,
        e_machine,
        e_version,
        e_entry,
        e_phoff,
        e_shoff,
        e_flags,
        e_ehsize,
        e_phentsize,
        e_phnum,
        e_shentsize,
        e_shnum,
        e_shstrndx,
    ) = fields

    result = {
        "type": e_type,
        "machine": e_machine,
        "entry": e_entry,
        "phoff": e_phoff,
        "phentsize": e_phentsize,
        "phnum": e_phnum,
        "shoff": e_shoff,
        "shentsize": e_shentsize,
        "shnum": e_shnum,
        "shstrndx": e_shstrndx,
        "flags": e_flags,
    }

    program_headers = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if off + 32 > len(blob):
            break
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = (
            struct.unpack_from("<IIIIIIII", blob, off)
        )
        program_headers.append(
            {
                "type": p_type,
                "offset": p_offset,
                "vaddr": p_vaddr,
                "filesz": p_filesz,
                "memsz": p_memsz,
                "flags": p_flags,
                "align": p_align,
            }
        )

    raw_sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if off + 40 > len(blob):
            break
        vals = struct.unpack_from("<IIIIIIIIII", blob, off)
        raw_sections.append(
            {
                "name_off": vals[0],
                "type": vals[1],
                "flags": vals[2],
                "addr": vals[3],
                "offset": vals[4],
                "size": vals[5],
                "link": vals[6],
                "info": vals[7],
                "align": vals[8],
                "entsize": vals[9],
            }
        )

    shstr = b""
    if 0 <= e_shstrndx < len(raw_sections):
        sec = raw_sections[e_shstrndx]
        shstr = blob[sec["offset"] : sec["offset"] + sec["size"]]

    for sec in raw_sections:
        name_off = sec["name_off"]
        if name_off < len(shstr):
            end = shstr.find(b"\0", name_off)
            if end < 0:
                end = len(shstr)
            sec["name"] = shstr[name_off:end].decode("ascii", errors="replace")
        else:
            sec["name"] = ""

    result["program_headers"] = program_headers
    result["sections"] = raw_sections
    return result


def file_offset_to_va(offset: int, program_headers: list[dict]) -> int | None:
    for ph in program_headers:
        if ph["type"] != 1:  # PT_LOAD
            continue
        start = ph["offset"]
        end = start + ph["filesz"]
        if start <= offset < end:
            return ph["vaddr"] + (offset - start)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("boot_bin", type=Path, help="Path to PSP_GAME/SYSDIR/BOOT.BIN")
    args = parser.parse_args()

    blob = args.boot_bin.read_bytes()
    print(f"file: {args.boot_bin}")
    print(f"size: {len(blob)} bytes")
    print(f"sha256: {hashlib.sha256(blob).hexdigest()}")

    elf = parse_elf32_le(blob)
    print()
    print("ELF:")
    print(f"  type:    0x{elf['type']:04x}")
    print(f"  machine: {elf['machine']}")
    print(f"  entry:   0x{elf['entry']:08x}")
    print(f"  flags:   0x{elf['flags']:08x}")
    print(f"  phnum:   {elf['phnum']}")
    print(f"  shnum:   {elf['shnum']}")

    print()
    print("PT_LOAD mappings:")
    for ph in elf["program_headers"]:
        if ph["type"] == 1:
            print(
                "  "
                f"file 0x{ph['offset']:08x}-0x{ph['offset'] + ph['filesz']:08x} -> "
                f"VA 0x{ph['vaddr']:08x}-0x{ph['vaddr'] + ph['memsz']:08x}"
            )

    interesting = {".text", ".rodata", ".data", ".bss", ".symtab"}
    print()
    print("Selected sections:")
    for sec in elf["sections"]:
        if sec["name"] in interesting or sec["name"].startswith(".rel"):
            print(
                f"  {sec['name']:<18} "
                f"type=0x{sec['type']:08x} "
                f"VA=0x{sec['addr']:08x} "
                f"off=0x{sec['offset']:08x} "
                f"size=0x{sec['size']:x}"
            )

    print()
    print("Battlezone anchors:")
    for text in ANCHORS:
        needle = text.encode("ascii")
        offset = blob.find(needle)
        if offset < 0:
            print(f"  NOT FOUND  {text}")
            continue
        va = file_offset_to_va(offset, elf["program_headers"])
        va_text = f"0x{va:08x}" if va is not None else "unmapped"
        print(f"  off=0x{offset:08x} VA={va_text}  {text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
