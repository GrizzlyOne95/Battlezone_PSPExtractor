# Battlezone PSP — Game / Match Logic Reverse Engineering

Status: **initial static-analysis map**  
Target examined: **BattleZone (USA)** PSP image

This document records reproducible findings from the original PSP disc image while keeping
copyrighted game binaries and disc contents out of the repository. Only hashes, offsets,
small identifiers, and reverse-engineering notes are committed here.

## 1. Disc and executable identity

SHA-256:

| Artifact | SHA-256 |
|---|---|
| BattleZone (USA).iso | `b7e92137992a5af846277d94492a110392832b3ef9604b65729525c940746b54` |
| `PSP_GAME/SYSDIR/BOOT.BIN` | `8db03a35f6c2c376ca79c093bb33b5725acd6de087b87abb706ebd4a56fa6b79` |
| `PSP_GAME/SYSDIR/EBOOT.BIN` | `8719ffd70f6258b144871e4414b0854a4616004e0783a5c6a4cac7bfd85e2c28` |

Important distinction:

- `BOOT.BIN` begins with the ELF magic and is directly usable for static analysis.
- `EBOOT.BIN` begins with the PSP `~PSP` wrapper.
- For game-logic recovery, `BOOT.BIN` is therefore the useful starting point.

### BOOT.BIN ELF summary

`BOOT.BIN` is a stripped, little-endian 32-bit MIPS PSP PRX ELF.

Observed ELF properties:

- ELF type: `0xFFA0` (PSP PRX)
- Machine: MIPS
- Entry point: `0x000dba4c`
- Section count: 63
- `.text`: VA `0x00000000`, file offset `0x80`, size `0x22ffd4`
- `.rodata`: VA `0x00231010`, file offset `0x231090`, size `0x18b80`
- `.data`: VA `0x00249c00`, file offset `0x249c80`, size `0xe733`
- `.bss`: VA `0x00258600`, size `0xd9a55`
- The symbol table is effectively stripped.
- PSP relocation sections are present as processor-specific sections.

For the first load segment, the relationship is particularly convenient:

```text
static_virtual_address = file_offset - 0x80
```

That makes strings and data tables easy to turn into static addresses before relocation
processing is implemented.

## 2. Match types are explicitly enumerated in shipped data

`PSP_GAME/USRDIR/leveldata/BZ_SP_TOURNEY_DEFS.CSV` documents the numeric game-mode enum:

| ID | CSV token | Player-facing mode |
|---:|---|---|
| 0 | DM | Deathzone |
| 1 | TDM | Team Deathzone |
| 2 | CTF | Capture the Flag |
| 3 | FH / FAH | Fox and Hound |
| 4 | HZ | Hot Zone |
| 5 | KO | Knockout |

The same CSV supplies tournament values for:

- player limit
- time limit
- score limit
- game type ID
- localization ID
- level filename

This is strong evidence that match setup is partly data-driven, while the actual scoring,
ownership, win-condition, and per-mode behavior remains in executable code.

Examples from the shipped tournament table include:

- `4,5,5,0,...,NEWMEX_4_DM.LVL`
- `4,5,50,3,...,CINQUE_4_FAH.LVL`
- `4,5,75,4,...,CHINA_4_HZ.LVL`
- `4,15,0,5,...,NEWMEX_8_KO.LVL`
- `4,20,400,4,...,CINQUE_8_HZ.LVL`

## 3. Player-facing rules corroborate the mode semantics

The English localization data contains useful rule descriptions:

### Fox and Hound

- The orb carrier is the Fox.
- Only the Fox accrues points.
- Killing the Fox and taking the orb transfers Fox status.

### Hot Zone

- A player captures a zone by occupying it until it changes to the player's team color.
- Points accrue through holding zones.

### Knockout

- The objective is tied to destroying the opposing launch pad's shield / health.
- The opposing launch pad can recharge, so the mode contains explicit launch-pad health logic.

These strings are useful xref anchors when naming recovered routines.

## 4. Level files expose mode-specific world actors

The existing LVL extractor is already valuable for executable RE because it shows which
objects are placed by data and which behavior must be supplied by code.

### Russia — 4-player variants

Object inventory from the parsed JSON:

| Class | DM | TDM | FAH | HZ |
|---|---:|---:|---:|---:|
| World | 1 | 1 | 1 | 1 |
| NavBeacon | 86 | 86 | 86 | 86 |
| Respawn | 7 | 7 | 7 | 7 |
| Breakable | 6 | 6 | 6 | 6 |
| Dispenser | 5 | 5 | 5 | 5 |
| JumpPad | 2 | 2 | 2 | 2 |
| Ball | 0 | 0 | **1** | 0 |
| HotZonePad | 0 | 0 | 0 | **3** |

Important result:

- DM and TDM use the same world-object inventory here.
- Fox and Hound adds one `Ball`.
- Hot Zone adds three `HotZonePad` actors.

This strongly supports a division of responsibility where the LVL supplies physical mode
actors, while `GameType` / `GameHotZone` implement rules and state transitions.

### Russia — 8-player CTF / KO comparison

The parsed files expose clear mode-specific classes:

- CTF contains **two `FlagStand`** objects.
- KO contains **two `KOChargePad`** objects.

The CTF and KO variants also contain many more navigation / respawn objects than the DM
variant. Do not interpret all of those additional objects as rule objects: those modes use
a different map-layout variant as well.

## 5. Source filenames survive inside the stripped executable

Assertion / debug strings leak the original developer source tree. Confirmed strings include:

| Original source path / name | File offset | Static VA |
|---|---:|---:|
| `c:/Trees/Psp/Game/Source/game.cpp` | `0x2343dc` | `0x23435c` |
| `c:/Trees/Psp/Game/Source/GameHotZone.cpp` | `0x234900` | `0x234880` |
| `c:/Trees/Psp/Game/Source/GameType.cpp` | `0x235138` | `0x2350b8` |
| `c:/Trees/Psp/Game/Source/Online.cpp` | `0x235ff8` | `0x235f78` |
| `c:/Trees/Psp/Game/Source/NetUtil.cpp` | `0x239634` | `0x2395b4` |
| `ProjectileMgr.cpp` | `0x23138d` | `0x23130d` |
| `WeaponMgr.cpp` | `0x231701` | `0x231681` |
| `HoverTank.cpp` | `0x23d6c9` | `0x23d649` |

Additional useful files visible in the binary include `hud.cpp` and several menu / online
implementation files.

This gives us source-level subsystem anchors even though ordinary function symbols are gone.

## 6. First code xrefs worth labeling

Searching MIPS `lui` + immediate address construction against the surviving source-path
strings yields useful code anchors.

### GameHotZone.cpp source-string xrefs

The `GameHotZone.cpp` string at static VA `0x234880` is referenced near:

- `0x0003c014`
- `0x0003c4a0`

These should be treated as **anchors into Hot Zone code**, not final function names yet.

### GameType.cpp source-string xrefs

The `GameType.cpp` string at static VA `0x2350b8` is referenced near:

- `0x00040c6c`
- `0x00040d4c`
- `0x00040e24`
- `0x000410e4`
- `0x00042248`
- `0x00044db0`
- `0x00047048`

These are high-priority locations for reconstruction because assertions commonly sit next
to container bounds checks, object registration, state transitions, and mode setup.

## 7. GameType initialization / creation anchor

A useful executable region starts around `0x00044df4`.

The surrounding routine references the diagnostic text:

- `Creating SubGame Type`
- later, `Game Type Creation completed`

Between those messages the code performs an indirect / virtual call followed by additional
setup calls.

Working interpretation:

> This routine is very likely part of the generic GameType creation/setup path.

Do **not** name it more specifically until the caller, object layout, and vtable are proven.

## 8. Mode resource table in .data

A compact table in `.data` contains mode-related model/resource pointers. Confirmed entries:

| Table VA | String pointer VA | Flag | Resource |
|---:|---:|---:|---|
| `0x24d128` | `0x239d3c` | 1 | `GM_KO_core.rws` |
| `0x24d130` | `0x239d4c` | 1 | `GM_HZ_Pad.rws` |
| `0x24d138` | `0x239d5c` | 1 | `GM_Fox_Flag.rws` |
| `0x24d140` | `0x239d6c` | 1 | `GM_CTF_Flag.rws` |
| `0x24d148` | `0x239d7c` | 1 | `GM_CTF_FlagStand.rws` |

This table is an excellent xref target for finding preload / spawn registration and then
walking outward into each mode's initialization code.

## 9. Other useful executable strings

Confirmed mode/gameplay strings include:

- `Respawn`
- `FlagStand`
- `HotZonePad`
- `GameType::addCollideEquip`
- `GameType::addTarget`
- `GameType::addEquip`
- `update game type`
- `ctf_flagacquire`
- `fox_start`
- `fox_intro`
- `fox_20points`
- `hot_zonecap`
- `hot_zonelost`
- `hot_zoneallblue`
- `hot_zoneallred`

These should be used as xref anchors rather than treated merely as UI/audio content.

## 10. Current architecture hypothesis

### Proven

1. Match IDs and tournament limits are data-driven.
2. Mode-specific actors are serialized into LVL data.
3. Generic game-type and Hot Zone implementation source names survived in `BOOT.BIN`.
4. The executable has explicit mode resource registration data.
5. `BOOT.BIN` is directly statically analyzable MIPS code despite being stripped.

### Strong working model

```text
front end / tournament data
        |
        v
match configuration (mode ID, limits, level)
        |
        v
generic GameType creation
        |
        +--> mode-specific implementation / virtual methods
        |
        +--> LVL actors (Ball / HotZonePad / FlagStand / KOChargePad)
        |
        +--> scoring, ownership, respawn, win-state, HUD/audio events
```

### Not yet proven

- Exact GameType class hierarchy.
- Exact vtable addresses.
- Constructor / destructor boundaries.
- Which object owns authoritative score and timer state.
- Which state is network-authoritative vs locally derived.
- Exact packet serialization for match-state transitions.

## 11. Next reverse-engineering work orders

### Work order 1 — make the PRX friendlier to Ghidra

Implement or adapt PSP PRX relocation handling for the processor-specific relocation
sections. The ELF loads without this, but correct relocation application will greatly
improve xrefs to global data, vtables, and function pointers.

Deliverables:

- documented PRX load base assumptions
- relocation parser / applier
- repeatable Ghidra import notes
- validation against known strings/data pointers

### Work order 2 — reconstruct GameType creation

Start at the `Creating SubGame Type` region around `0x44df4`.

Goals:

- identify caller(s)
- recover input mode ID
- identify the GameType object pointer
- resolve the indirect call target
- identify vtable
- label initialization fields

### Work order 3 — reconstruct Hot Zone first

Hot Zone is the best first complete mode because:

- it has a dedicated leaked source filename, `GameHotZone.cpp`
- LVL placement is simple: three `HotZonePad` objects in the Russia 4-player example
- player-facing rules clearly describe capture and scoring
- many Hot Zone event strings survive in the binary

Recover:

- pad capture state
- team ownership
- scoring tick
- all-zones-owned state
- loss/capture events
- win threshold interaction

### Work order 4 — Fox and Hound

Use the single `Ball` actor and `GM_Fox_Flag.rws` resource as anchors.

Recover:

- orb ownership
- Fox designation
- score tick
- death/drop/transfer flow
- respawn interaction

### Work order 5 — CTF

Use `FlagStand`, `GM_CTF_Flag.rws`, and `GM_CTF_FlagStand.rws`.

Recover:

- home/away flag state
- carried/dropped state
- capture validity
- reset/return timers
- team scoring

### Work order 6 — Knockout

Use `KOChargePad`, `GM_KO_core.rws`, and localization references to launch-pad shield,
health, and recharge.

Recover:

- pad/core health
- shield logic
- recharge behavior
- team win condition

### Work order 7 — network authority

Only after the local mode state machines are named:

- trace Online / NetUtil callers from score and ownership mutation sites
- identify replicated state
- distinguish host authority from client presentation
- document message IDs / serialized structures without conflating them with local HUD code

## 12. Tooling notes

Useful static tools:

- Ghidra with MIPS little-endian
- `llvm-objdump --arch-name=mipsel`
- `readelf` for basic ELF layout (but not PSP-specific relocation decoding)
- the repository's existing LVL/data-table extractors

A helper script, `scripts/inspect_psp_boot.py`, is included to reproduce basic ELF identity,
hashes, static-address conversion, and known string anchors without adding the game binary
to version control.
