# Battlezone PSP — Game Code Reverse Engineering

Target: **BattleZone (USA)**, `PSP_GAME/SYSDIR/BOOT.BIN`
(SHA-256 `8db03a35f6c2c376ca79c093bb33b5725acd6de087b87abb706ebd4a56fa6b79`).

This document explains how the game's code works: engine layout, the front end, the match
framework, each of the six match types, hover tank physics, weapons, tweaks, cheats,
networking and the save file. It extends [`PSP_GAME_MATCH_LOGIC.md`](PSP_GAME_MATCH_LOGIC.md),
which recovered the game-type factory and constructors.

All addresses are static virtual addresses with the PRX loaded at base 0 (file offset − 0x80 for
the first segment). Emulators such as PPSSPP load the module at `0x08804000`; add that base when
following addresses in a debugger.

Evidence levels used below:

- **code**: read directly from decompiled/disassembled code.
- **data**: values from the shipped CSV tables (`USRDIR/leveldata`), which the code loads at boot.
- **inferred**: consistent with code and data but not traced end to end.

## 1. Reproducing the analysis

```text
python extractors/extract_psp_code.py --input <ISO | disc folder | BOOT.BIN> \
    --out-root out/code_map --relocated-elf --listing
```

- `code_map.json` / `code_map.md`: 8,209 functions, 250/250 imports named, call graph, string
  cross-references, source-file attribution, 179 function-pointer tables / vtables.
- `BOOT_relocated.elf`: PSP relocations applied at base 0 and `e_type` set to `ET_EXEC`. It imports
  into stock Ghidra 11 as `MIPS:LE:32:default` with correct cross-references (no PSP loader
  plugin needed). Applying `ghidra_symbols.txt` with Ghidra's `ImportSymbolsScript.py` and
  running auto-analysis decompiles all 8,281 functions Ghidra finds without failures.
- `listing.asm`: annotated disassembly (needs `pip install capstone`).

Why this works: `BOOT.BIN` is an unencrypted, stripped PRX (ELF type `0xFFA0`) that still carries
its `0x700000A0` relocation sections. Every `lui`/`addiu` address pair and every data pointer can
be resolved exactly from them. Relocations against segment 1 (`.cplinit`, `.linkonce.d`, `.bss`)
must add that segment's address (`0x2583a8`); the `--relocated-elf` output does this.

## 2. Program architecture

| Item | Value | Evidence |
|---|---|---|
| Module name | `Rw37Skel` v1.1 (RenderWare 3.7 skeleton app) | module info |
| Developer tree | `c:/Trees/Psp/Game/Source/*.cpp`, `c:/Trees/gk/src/kernel/*` | assert strings |
| Build | `Ver 12.6 10-03-06`, RenderWare core built `Aug 26 2006` | strings |
| Runtime kernel | Paradigm "GK build 2.0 (Psp) Jun 29 2006" (heaps, threads, asserts) | strings |
| Compiler ABI | SN Systems C++: vtable entries are `{int16 this_adjust, int16 pad, code*}` (8 bytes) | code |
| Third party | RenderWare 3.7, tinyxml, libpng 1.0.12, zlib 1.1.3 (inflate/deflate) | strings |
| Physics | in-house (`PhysicsEngine.cpp`, `physics/CODE/INTERFACE/flags.h`, "HOTS"/proxy files) | strings |

Imported firmware libraries (all 250 NIDs resolved): IoFileMgr, ThreadMan, SysMem, UMD, Ge,
Display, Ctrl, Audio, SasCore (sound effects), Atrac3plus (music/speech), Mpeg/Psmf (movies),
Utility (savedata, message dialog, OSK keyboard), Net/NetAdhoc/Adhocctl/AdhocMatching (ad hoc
multiplayer), Power, Rtc, WlanDrv.

Roughly 100 source files are named in assert strings; the code map attributes about 975
functions to them. Main groups:

- **Game**: `game.cpp`, `GameType.cpp`, `GameHotZone.cpp`, `HoverTank.cpp`, `WeaponMgr.cpp`,
  `ProjectileMgr.cpp`, `PhysicsEngine.cpp`, `aiPathFinder.cpp`, `Breakable.cpp`, `Dispenser.cpp`,
  `Door.cpp`, `Sentinel.cpp`, `hud.cpp`, `camera.cpp`, `vfxMgr.cpp`, `fxRadial.cpp`, `fxSonicCone.cpp`,
  `rail.cpp`, `ShadowManager.cpp`, `LightManager.cpp`
- **Front end**: `BZFrontEnd.cpp`, `BZPauseMenu.cpp`, `Loading.cpp`, 26 `Menu*.cpp` screens, the
  `AxUI` widget library (`AXUIScreen`, `AXUIMenu`, `AXUIButton`, sliders, option lists) with an
  `AxSignalSlot` signal/slot system
- **Platform**: `audio_psp.cpp`, `audio_music.cpp`, `audio_speech.cpp`, `movie*.cpp`,
  `BzIoOpen.cpp`, `NetUtil.cpp`, `Online.cpp`, `DevGui.cpp`

### Boot sequence (`game.cpp`)

Initialization logs, in order: Localization → Kernel → Render → Controller → World → Camera →
Font (`FFFEstudioExtended5/7/9`) → Weapon (loads `BZ_WEAP_DEFS.CSV`) → Front End (`BZFrontEnd`) →
Audio → Clump → `HUD.txd` → PauseMenu → Audio Start → Projectile (`BZ_PROJ_DEFS.CSV`) → Tank
(`BZ_TANK_MOTION.CSV`, `BZ_ENHANCE_DEFS.CSV`) → Shadow → VFX. `psmf.prx` is loaded from
`USRDIR/module` for movie playback.

### Frame loop

The performance-monitor labels give the per-frame task order: Determ Sys → Audio Update →
System Update → Game Update → AI Update → Vfx Update → ClumpMgr Update → HUD Update → UI Update →
Camera Update → Deco Water → Light Mgr → Input → Physics → Tank Vis → ProjectileMgr → Equip →
GameType → Spawn, then rendering (sky dome, world, sorted alpha atomics, VFX, HUD, shadows).
A developer build could dump these timings to `BZ_PERF_MON.CSV`.

## 3. Front end state machine (`BZFrontEnd`)

The front end is a list of AxUI screens keyed by state ID (`screen+8`). `setState` (`0x9a50c`)
stores the new and previous state (`frontend+0x1458` / `+0x145c`), looks the screen up and queues a
transition. State names come from the string table at `0x252628` (code):

| ID | State | ID | State | ID | State |
|---:|---|---:|---|---:|---|
| 0 | SPLASH | 13 | SPTOURNEYSELECTION | 26 | RESPAWN |
| 1 | MAIN | 14 | INFO | 27 | LOBBY |
| 2 | SETTINGS | 15 | GAMETYPE | 28 | OPTIONS |
| 3 | LEVELSEL | 16 | PREFERENCES | 29 | AUDIO |
| 4 | LOADING | 17 | ARENA | 30 | CONTROLLER |
| 5 | GAMEBEGIN | 18 | TANK | 31 | UNLOCK |
| 6 | GAMERUN | 19 | COUNTRY | 32 | UNLOCKABLES |
| 7 | HUD | 20 | TANKSIZE | 33 | PROFILE |
| 8 | PAUSE | 21 | DECAL | 34 | SPTCOUNTRYAIINFO |
| 9 | GAMEOVER | 22 | WEAPONS | 35 | SPTDIFFICULTYSELECTION |
| 10 | MPJOINORHOSTGAME | 23 | SLOT | 36 | MPTEAMCOLOR |
| 11 | SPTOURNEY | 24 | TWEAKS | 37 | SPTMATCHSELECTION |
| 12 | SPTCOUNTRYSELECTION | 25 | TWEAKSLOT | 38 | CHEATS |

Each screen is built in its own `Menu<Name>.cpp`. The screen constructors are in the code map
(for example `MenuWeapons` at `0xccd5c`, `MenuTweaks` at `0xc7e30`, `MenuLobby` at `0xb2d08`).

Flows (inferred from screen names and code):

- **Single-player tournament**: SPTOURNEY → country → difficulty → country/AI info → tourney →
  match selection → INFO → tank setup → LOADING → GAMEBEGIN → GAMERUN → GAMEOVER → UNLOCK.
  Tournament matches come from `BZ_SP_TOURNEY_DEFS.CSV` (`BZFrontEnd::readSPTourneyFile`).
- **Tank setup** (shared): TANK hub → COUNTRY, TANKSIZE, DECAL, WEAPONS / SLOT, TWEAKS / TWEAKSLOT.
  A tank has a front gun, a left gun and a right gun (the debug tank dump prints "front gun / left
  gun / right gun"). Weapons are listed per tank size ("LIGHT/MEDIUM/HEAVY WEAPON SLOTS").
- **Multiplayer**: MPJOINORHOSTGAME → LOBBY → GAMETYPE / ARENA / PREFERENCES → MPTEAMCOLOR → tank
  setup.
- **In a match**: GAMERUN ↔ PAUSE. On the local tank's death the game jumps to RESPAWN (26)
  (`0x9a6d4`). If the player hasn't respawned after **35 s** it is forced back to GAMERUN with the
  current loadout (`0xbb4f8`). Match end sets GAMEOVER (9).

## 4. Match framework (`GameType`)

### Creation

`CreateGameType(mode)` (`0x396d4`) builds one of six subclasses (see the match-logic doc). All
share the base constructor `0x46bd0`, which sets up entity pools (limits below) and installs
vtable `0x2570d4`. `GameType::create` (`0x44b6c`) takes a 0x90-byte match config:

- `cfg[0]`: time limit in **minutes** (converted to seconds)
- `cfg[1]`: score limit
- `cfg[2]`: player count
- two further fields: difficulty and a cheat bitfield (see §9)

Respawn delays are hard-coded here: **35 s for the human player** (a timeout on the Respawn
screen) and **4 s for AI** (code, `GameType+0x334/0x338`).

Entity budget per match (code, destructor `0x46fb8`): 6 players / 6 tanks, 40 spawn points, 16
jump pads, 12 doors, 12 lasers, 12 triggers, 12 dispensers, 10 breakables, 8 pickups, 2 sentinels,
2 drones. Teams have at most 3 members (the team-member lookup `0x4647c` iterates 3 slots), so
team modes are at most 3 v 3.

### Virtual interface

The six mode vtables (DM `0x256f1c`, TDM `0x25707c`, CTF `0x256ec4`, FAH `0x257024`, HZ
`0x256f74`, KO `0x256fcc`) override this interface (slot = entry index, 8 bytes each):

| Slot | Role (from call sites) | Overridden by |
|---:|---|---|
| 1 | load level (reads the entity file, sets the spawn policy) | all |
| 2 | destroy mode objects | all |
| 3 | per-frame mode update (called after players, tanks, physics, projectiles) | CTF, FAH, HZ, KO |
| 4 | read a mode-specific LVL entity (tag 7 FlagStand, 9 KOChargePad, 10 Ball) | CTF, FAH, HZ, KO |
| 5 | build the match (physics, HUD, tanks, AI, audio) | none |
| 6 | allocate equipment of a type | none |
| 7 | objective event (flag scored/returned, zone captured, core destroyed) | CTF, HZ, KO |
| 8 | "is this a KO shield field?" (tank collision query) | KO |
| 9 | choose spawn point | KO |
| 10 | tank killed (victim, killer) | all |

### Match states and clock (`GameType::update`, `0x45564`)

| State | Behaviour |
|---:|---|
| 0 | Intro (single player): wait 5 s, then for a button press |
| 1 | Countdown: 3 s, then all tanks are released and the mode's start line plays |
| 2 | Playing: world update, clock counts up |
| 3 | Game over |

Multiplayer matches start in state 1. When the clock passes the time limit:

- If there is a single leader (free-for-all) or one team leads, the winner is announced and the
  match ends (`0x3ea18`).
- If it's a **tie, the match goes to overtime**: the limit grows by `max(10% of the time limit,
  60 s)` and repeats. Overtime also switches off the KO charge pads.
- The "1 minute remaining" line (`ending_1min`) plays at 61 s left.

Score changes go through `addPlayerScore` (`0x51010`; a player's score is clamped at 0) and
`addTeamScore` (`0x46544`). Teams: **0 = Red, 1 = Blue**.

### Spawning (base slot 9, `0x45e40`)

For every candidate spawn point the game measures the distance to the nearest tank, then picks
the spawn **farthest from all tanks** and drops the tank 5 m above it. Spawn policy (`+0x32c`) 0
uses every spawn point (DM, TDM, FAH, HZ); 1 uses only the player's team spawns (CTF, KO).

### Kill scoring common to all modes

- Suicide: −1 to the player (and −1 to the team in TDM).
- Team kill: −1 to the player (−1 to the team in TDM).
- Enemy kill: +1 to the player.
- Killing an **objective carrier** (flag or Ball holder, tank virtual `+0x84`): **+5** in CTF and
  Fox and Hound.
- Every kill queues the victim in the respawn list (`0x4360c`).

## 5. The six match types

Mode IDs match `BZ_SP_TOURNEY_DEFS.CSV`: 0 DM, 1 TDM, 2 CTF, 3 FAH, 4 HZ, 5 KO.

### Deathzone (DM, 0) — free-for-all

- +1 per kill; first player to the score limit wins.
- Announcer lines at 5 kills and 1 kill from the limit (`dm_5kills`, `dm_1kill`).

### Team Deathzone (TDM, 1) — 2 teams

- An enemy kill is +1 to both the player and the team. Suicides and team kills are −1 to both.
- The first team to the score limit wins.
- Lines at 10 and 3 kills from the limit, voiced for your team or the enemy's.

### Capture the Flag (CTF, 2) — 2 teams, team spawns

Two FlagStands and two Flags (LVL entity tag 7). Distances are squared, so `400` means 20 m.

| Flag state | Rule |
|---|---|
| 0 home | An enemy tank within **20 m** picks it up |
| 1 dropped | A friendly tank within 20 m returns it (+1 to the returner). An enemy within 20 m picks it up. Auto-returns after **20 s** |
| 2 carried | The flag follows the carrier. The carrier captures by reaching its **own** stand (within 20 m) **while its own flag is at home** |

- A capture is +5 to the carrier and +1 to the team, followed by the score-limit check.
- Killing a carrier is +5; any other kill is +1.
- The capture callout (`ctf_lastpoint`) plays when a team is 1 point from winning.

### Fox and Hound (FAH, 3) — free-for-all with one Ball

- One Ball (LVL entity tag 10, model `GM_Fox_Flag.rws`).
- Any tank within **15 m** of a loose Ball picks it up.
- A dropped Ball resets to its spawn after **15 s**.
- The holder (the Fox) scores **+1 every 2 s** of holding.
- Killing the Fox is +5; any other kill is +1.
- `fox_20points` plays at 20 points from the limit, and reaching the limit wins.

### Hot Zone (HZ, 4) — 2 teams, up to 3 pads

- **Capture**: once a second a pad looks for the **nearest** tank within **30 m**. If that tank
  isn't on the owning team, a capture starts. A different team's tank taking over as nearest
  restarts it; the owning team's tank becoming nearest, or no tank in range, cancels it.
  **3 s** of uninterrupted capture flips the pad to that team. Because only the nearest tank
  counts, a defender sitting closer to the pad blocks a capture.
- Capturing a pad is +1 to the capturer's own score.
- **Team scoring**: each team earns +1 team point per interval, and the interval depends on how
  many pads it holds: **1 pad = 3 s, 2 pads = 2 s, 3 pads = 1 s** (none = no points).
- Kills only change players' own scores, not team scores.
- `hot_zoneallcap` plays when one team holds every pad. Warnings play at 20 points from the limit,
  and the first team to the limit wins.

### Knockout (KO, 5) — 2 teams, team spawns, elimination

Each team has a **Core** (health 100, shield 1,600), a **KOChargePad** (LVL entity tag 9) and an
optional shield-field model.

- **Damage**: damage hits the Core's shield first, then its health (`takeDamage`, `0x34594`). The
  announcer calls shield-down, "under attack" (once per attack) and "pad deactivated".
- **Destroying a Core** gives the attacker +5 (−5 for hitting your own team's Core).
- **Charge pad**: every 1 s, while its Core is below max health and the match isn't in overtime,
  each teammate within **20 m** of the pad with at least **10 energy** spends 10 energy to repair
  the Core by **20 health**. The pad links to its team's Core if the Core is within 150 m. A
  destroyed Core that receives health comes back online (`ko_padonline`).
- **No respawn without a Core**: the KO spawn chooser only returns a spawn while your team's Core
  is intact.
- **Win condition**: a team is alive while its Core stands *or* any of its tanks is alive. When
  exactly one team is alive it wins (last team standing).

### Announcer (speech table `0x24a890`, 95 entries × 36 bytes)

Each entry is a name and a variation count. The game indexes the table by group: group 0 is
mode lines (0–51), group 1 notifications and pickups (52–65), group 3 final standings (66–75).
The remaining entries are AI, unlock, rank, trap and hint lines. Examples: `ctf_start`=0,
`fox_start`=11, `hot_start`=17, `ko_start`=27, `dm_start`=40, `tdm_start`=44,
`placechange_1st`=52, `ending_1min`=56, `standing_1st`=66.

### Level table (`.data` `0x24e0fc`, 120 records × 0x90)

Each record holds a mode ID, a map-size class (4 or 8, as in the file names), the LVL filename and
a localization ID. Order: DM, TDM, CTF, HZ, KO, FAH, 20 levels each (10 maps × `_4_`/`_8_`
variants). Maps without a 4-player CTF or KO layout reuse the `_8_` file in that slot.

## 6. Hover tanks (`HoverTank.cpp`)

### Motion table (`BZ_TANK_MOTION.CSV` → `.data 0x24df58`, 20 rows × {small, medium, large})

The loader (`HoverTank::readMotionDefFile`, `0x965ac`) skips `#` lines, splits on commas and stores
each CSV line into a fixed table row. **The row order in memory is not the CSV order** (code):

| CSV line | Field | Table row / address | Small / Medium / Large (data) | Used by |
|---:|---|---|---|---|
| 1 | Turn Rate | 0 `0x24df58` | 21 / 19 / 17 | drive `0x954a0` |
| 2 | Max Fwd Speed | 1 `0x24df64` | 80 / 68 / 55 | spawn, drive |
| 3 | Max Fwd Accel | 2 `0x24df70` | 100 / 90 / 70 | drive |
| 4 | Max Side Speed | 3 `0x24df7c` | 55 / 55 / 50 | drive |
| 5 | Max Side Accel | 4 `0x24df88` | 95 / 90 / 70 | drive |
| 6 | Hit Points | 5 `0x24df94` | 105 / 200 / 325 | spawn |
| 7 | Energy Points | 8 `0x24dfb8` | 300 / 300 / 300 | spawn |
| 8 | Energy Recharge | 9 `0x24dfc4` | 0 / 0 / 0 | spawn |
| 9 | Suspension Height | 12 `0x24dfe8` | 10 / 10 / 10 | hover `0x94da0` |
| 10 | HoverDampCoeff | 13 `0x24dff4` | 0.5 / 0.5 / 0.5 | hover |
| 11 | Suspension Gain | 14 `0x24e000` | 1000 / 1000 / 1000 | hover |
| 12 | Upright Gain | 15 `0x24e00c` | 100 / 100 / 100 | hover |
| 13 | Energy Start % | 10 `0x24dfd0` | 1 / 1 / 1 | spawn |
| 14 | Kill Energy Bonus | 11 `0x24dfdc` | 50 / 100 / 150 | on kill `0x91148` |
| 15 | Health Regen Delay (s) | 6 `0x24dfa0` | 6 / 10 / 15 | spawn |
| 16 | Health Regen Amount (pts/s) | 7 `0x24dfac` | 50 / 75 / 100 | regen `0x97b30` |
| 17–20 | Death splash radius, splash damage, camera shake duration and magnitude | 16–19 `0x24e018…` | 60/70/80, 25/35/50, 1/1/1, 0.6/0.8/1 | death |

The compiled-in defaults (for example turn rate 25/22.5/20) are overwritten by the CSV at boot.

### Hover physics (`0x94da0`, code)

- Four hover points (offsets at `0x24e0ac`, one updated per frame in rotation) each raycast down
  up to the Suspension Height `h`. The collision surface attribute (`bdInt.attr`) is recorded.
- Spring force per point: `gain · ½ · dt · ((h + 1 − d) · (h + 1))²`, where `d` is the hit distance.
- Damping: `−damp · h · 60 · dt · v_vertical`.
- Upright torque: `upright_gain · 100 · dt · (2 − up·worldUp)⁵`, which keeps the tank level
  (stronger the more it tilts).
- The combined force and torque are scaled by 0.4 and handed to the rigid body.

### Driving (`0x954a0`, code)

- **Turning**: the steering rate ramps up the longer the stick is held and is clamped to
  [0.1, 1]. Angular velocity decays at 10/s.
- **Speed**: forward and strafe speed are capped separately by the motion table plus the Top
  Speed tweak (`tank+0x5b4`). Reverse runs at 75%.
- **Gravity**: 9.8 m/s², projected along the slope.
- **Nitro**: meter at `tank+0x828`, 0–1. Boosting multiplies speed by the nitro multiplier
  (`+0x824`, from the tank's nitro weapon plus the Nitro tweak) and drains the meter over the
  nitro duration. It refills at 0.125/s (8 s from empty). The HUD shows the meter for the local
  tank.
- A tank that falls out of the world is killed ("die from out of world").

### Energy and health (code + data)

- **Energy** (`tank+0x4c`, max `+0x50`) is the ammo pool: firing subtracts the weapon's energy cost
  (`0x99424`). Hit points are `tank+0x20` (max `+0x24`). There is
  **no passive energy recharge** (Energy Recharge = 0). Energy comes back from kills (Kill Energy
  Bonus 50/100/150), pickups and dispensers. KO charge pads drain it.
- **Health** regenerates at 50/75/100 points/s after 6/10/15 s without taking damage.
- **Death**: a destroyed tank deals splash damage of 25/35/50 within 60/70/80 m and shakes nearby
  cameras.

## 7. Weapons and projectiles

### Loaders (code)

- `WeaponMgr::readWeaponDefFile` (`0xf7d4`): `BZ_WEAP_DEFS.CSV`, 0xd8-byte records. It reads 33
  numeric columns in CSV order (`WeaponID` at `+0x00` … muzzle-light decay at `+0x80`), then the two
  localization IDs, then copies the nickname and full name as strings.
- `ProjectileMgr::readProjectileDefFile` (`0xa558`): `BZ_PROJ_DEFS.CSV`, 0x70-byte records, 28
  columns in CSV order, at most 35 definitions. Projectile types: 0 bullet, 1 explosive, 2
  missile, 3 nitro, 4 hitscan, 5 sonic cone, 6 sphere burst.

Weapon columns: WeaponID, bulletID, energy cost, maxAmmo, autoChrgRate, tracking (0 basic,
1 semi, 2 full), fireDelay, isAutoFire, holdCharge (damage scale, velocity scale, max time),
sndShootID, mountWeight (0 small, 1 medium, 2 large, 3 team special), sniper (flag, min/max FOV),
aim assist (flag, heading, pitch, max distance), burst (count, offset, spread, delay), lockDelay,
random fire offsets, modelID, muzzle light (radius, RGB, decay), localization IDs, names.

Projectile columns: BulletID, type, lifeSpan, velocity, randVelSlow, damage, impact force,
pfx/impactVFX/move/impact sound IDs, brittleTime, control dampen (magnitude, time),
stealScale (vampire), breakLock (+ range), burn damage/time, camShake, KillMissile radius/distance,
lockDelay, maxTurnRate, explodRadius, isFused, thrust (magnitude, duration).

### Weapon list (data: `BZ_WEAP_DEFS.CSV` joined with `BZ_PROJ_DEFS.CSV`)

Mount: S/M/L = small/medium/large class, TS = team special, – = not player-selectable. For team
specials, "fire delay" is the recharge time.

| ID | Weapon | Mount | Energy | Fire delay (s) | Tracking | Burst | Projectile | Damage | Velocity | Life (s) | Splash r |
|---:|---|---|---:|---:|---|---:|---|---:|---:|---:|---:|
| 3 | Phoenix Rocket | M | 20 | 1.75 | full | – | missile | 170 | 15 | 6 | 20 |
| 4 | HAWC | L | 6 | 0.6 | semi | – | missile | 20 | 13 | 2 | 15 |
| 5 | Vulcan Cannon | S | 1.75 | 0.1 | – | – | bullet | 12 | 30 | 3 | – |
| 6 | Shotgun Rockets | L | 3.3 | 1.25 | – | 6 | missile | 20 | 17 | 2 | 15 |
| 7 | Electron | S | 30 | 1.75 | full | – | missile | 10 | 15 | 6 | 5 |
| 8 | A.E. Fusion Rifle | S | 15 | 0.4 | – | – | hitscan | 25 | 1000 | 3 | – |
| 9 | Homing Missile | L | 15 | 2 | full | 2 | missile | 175 | 7 | 6 | 30 |
| 11 | Mines | M | 30 | 3 | – | – | explosive | 250 | – | 15 | 30 |
| 12 | 360 Boom | S | 15 | 1 | – | – | sphere burst | 20 | – | 0.5 | 70 |
| 13 | Rail Gun | M | 15 | 0.9 | – | – | hitscan | 65 | 1000 | 2 | – |
| 14 | Swarm Missiles | M | 10 | 2.5 | semi | 3 | missile | 10 | 6 | 4 | 20 |
| 15 | Kinetic Gamma Bolt | L | 20 | 2 | – | – | hitscan | 250 | 1000 | 3 | – |
| 16 | Shockwave | M | 7.5 | 0.65 | – | – | sonic cone | 15 | – | 2 | – |
| 17 | NOD Beam | S | 7 | 0.33 | – | – | hitscan | 20 | 150 | 1 | – |
| 18 | Mortar | L | 30 | 2 | – | – | explosive | 400 | 225 | 5 | 35 |
| 19–21 | Nitro (light / medium / heavy) | – | 0 | – | – | – | nitro | – | – | – | – |
| 32–34 | Plasma light / medium / heavy (default gun) | – | 5 / 7 / 10 | 1 | – | – | bullet | 50 / 75 / 100 | 25 | 3 | – |

Other rows: IDs 1–2 are placeholder defaults; ID 10 is an unused copy of Shockwave; 30 is the
Sentinel turret gun; 31 the Drone gun; 35–39 are AI-only copies of Electron, Phoenix, Homing,
Fusion and KGB.

### Team specials (one per country, data + code)

| Country | Special | Recharge (s) |
|---|---|---:|
| USA | Power Shot (hitscan, 600 damage) | 70 |
| Russia | Super Ram | 70 |
| Canada | Liquid Nitrogen Shot (freeze) | 70 |
| Italy | Teleport | 30 |
| England | Invisibility (15 s) | 60 |
| Germany | Armor (15 s, 0.8 damage scale) | 60 |
| Japan | EMP (radius 100) | 45 |
| China | Lockdown | 70 |

The tank's country enum order is USA, China, Germany, Russia, Italy, Japan, Canada, England (code,
tank debug dump at `HoverTank.cpp`).

## 8. Tweaks (`BZ_ENHANCE_DEFS.CSV`)

`HoverTank::readEnhanceDefFile` (`0x92688`) stores `{major, minor}` float pairs at `.bss 0x32efe0`.
A tank has **three tweak slots**. Each slot holds one enhancement at *major* strength and one at
*minor* strength; `0x91608` sums them into a 10-float bonus array and applies them (code):

| Enum | Tweak | Major / Minor (data) | Applied to |
|---:|---|---|---|
| 0 | Top Speed | 5 / 2 | speed cap `+0x5b4` |
| 1 | Nitro Boost | 0.25 / 0.1 | nitro multiplier `+0x830` |
| 2 | Charge Weapon Energy | 5 / 2 | charge-weapon energy (`+0x54`) |
| 3 | Increased Pickup duration (s) | 10 / 5 | pickup timers |
| 4 | HP Recharge Delay (s) | 2 / 1 | subtracted from regen delay `+0x928` |
| 5 | Vampire Transfer | 0.2 / 0.1 | weapon manager (damage → health) |
| 6 | Lock-on Range (m) | 50 / 25 | weapon manager |
| 7 | Damage (%) | 10 / 5 | weapon manager |
| 8 | Splash Damage Radius (m) | 15 / 5 | weapon manager |
| 9 | Team Special Recharge (s) | 10 / 5 | subtracted from special recharge `+0x588` |

## 9. Cheats

The match config's cheat byte (`GameType+0x1c`) is set from the Cheats screen (`MenuCheats.cpp`).
Bit meanings (code):

| Bit | Effect | Where |
|---|---|---|
| `0x01` | Fast team special: recharge 3 s (15 s for Armor and Invisibility) | `0x91980` |
| `0x02` | Both side guns become Mortars (weapon 18) | `0x90d4c` |
| `0x04` | Both side guns become Rail Guns (13) with damage set to 5,000 (one-hit kills) | `0x90d4c` |
| `0x08` | Unlimited energy (firing costs nothing) | `0x91980`, `0x99424` |
| `0x10` | Pickups and dispensers give double effect | `0x2786c`, `0x2c618` |
| `0x20` | No health-regen delay | `0x91980` |
| `0x40` | "Freeze" hull effect on tanks | `0x918b0` |

How the cheats are unlocked isn't traced yet.

## 10. Multiplayer (`NetUtil.cpp`, ad hoc)

- **Lobby**: `sceNetAdhocMatching` for discovery and joins, PDP datagrams for lobby broadcasts,
  PTP connections host ↔ clients. There's deferred-join handling and dropout detection.
- **In game**: ad hoc *game mode* (`sceNetAdhocctlCreateEnterGameMode` / `JoinEnterGameMode`), a
  fixed-size replicated slot per station (the log prints "game slot size"). The simulation is
  **deterministic lockstep**: stations exchange controller inputs per tick
  (`NetGameSetController`/`GetController`), agree on deterministic and non-deterministic random
  seeds before starting, and compare a periodic state hash of positions, RNG and energy
  ("Drift - T P R E"). A missing station produces a "lockstep timeout".
- **In-match message types** (`0x56e94…0x56f94`): 0 PLYRORDER, 1 LOADLEVEL, 2 GAMEDATA,
  3 TANKDATA, 4 RNDSEED, 5 STATEHASH, 6 PAUSE, 7 UNPAUSE, 8 RESPAWN.
- **Link states**: INLOBBY, DISCONNECT, SCAN, GAMEMODE.
- Matches need at least 2 stations ("too few players … disabling").
- `Online.cpp` loads downloadable content from the memory stick: extra arenas (`ARENA.DAT`
  containing ARENA1/ARENA2) and skin sets (`SKINS.DAT`), each validated by CRC.

## 11. Save data (`BZONE.DAT`)

- The save is encrypted with SDK savedata **mode 5** (`SAVEDATA_PARAMS[0] = 0x41`), using this
  16-byte game key:

  ```text
  "MARK BEARDSLEY\0\0"   = 4d 41 52 4b 20 42 45 41 52 44 53 4c 45 59 00 00
  ```

  The key is copied into `SceUtilitySavedataParam+0x5dc` before every `sceUtilitySavedataInitStart`
  (`0x7b338`, `0x7b554`, `0x7b774`, `0x7baac`, `0x7bda4`). Title ID `ULUS10156`, directory `USER%02d`
  (10 profiles).
- **The key is verified.** A 100% save from the European release (`ULES00522USER00`) decrypts with
  it, and the computed MAC equals the `BZONE.DAT` hash stored in that save's `PARAM.SFO`
  (`e15e1cdaa6906c80d7bd2a1c676a59be`). So the EU and US releases share the key.
- **Contents**: the plaintext is a raw 2,492-byte (`0x9bc`) dump of the front end's profile object
  (`frontend+0x10d0`). Known offsets:

  | Offset | Content |
  |---|---|
  | `0x598` | Profile name (for example `PAXRISEN`) |
  | `0x1a4`, `0x46c` | Copies of last-used level records (for example `CINQUE_8_HZ.LVL`, `NEWMEX_4_DM.LVL`) |
  | `~0x5a8–0x8a8`, `0x8d4–0x948` | Unlock/progress flag arrays (all 1 in a 100% save) |
  | end | Three floats of 1.0 (probably the music, effects and speech volumes) |

  The rest of the profile layout still needs mapping.

Any PSP savedata tool that accepts a game key (or an emulator) can decrypt and re-encrypt the file
with this key.

## 12. Key function index

| Address | Function |
|---:|---|
| `0x0dba4c` | `module_start` |
| `0x0396d4` | `CreateGameType(mode)` |
| `0x046bd0` | `GameType::GameType` (base constructor) |
| `0x044b6c` | `GameType::create(config)` |
| `0x045564` | `GameType::update(dt)`: states, clock, overtime |
| `0x040454` | world update (players, tanks, physics, projectiles, mode slot 3, respawn queue) |
| `0x0402e8` | respawn queue |
| `0x045e40` | base spawn chooser (farthest from all tanks) |
| `0x03ea18` | winner / tie check at time-out |
| `0x045224` | end match → GAMEOVER |
| `0x045330` | final-standings announcer |
| `0x051010` / `0x046544` | add player score / add team score |
| `0x03bc74` `0x03e0d8` `0x03b954` `0x03dc04` `0x03c828` `0x03d474` | kill handlers DM/TDM/CTF/FAH/HZ/KO |
| `0x02e598` / `0x02de44` / `0x02dfa4` / `0x02e158` | CTF flag update / capture / pickup / return |
| `0x023a7c` / `0x03d7f8` | FAH ball update / fox scoring |
| `0x0313a4` / `0x03c0ac` / `0x03c604` | HZ pad capture / team scoring / capture event |
| `0x034594` / `0x0349f4` / `0x032290` / `0x03cea8` | KO core damage / core update / charge pad / win check |
| `0x0965ac` / `0x092688` | read tank motion / enhancement tables |
| `0x094da0` / `0x0954a0` | hover suspension / drive model |
| `0x091608` / `0x091980` | apply tweaks / apply cheat bits |
| `0x00f7d4` / `0x00a558` | read weapon / projectile tables |
| `0x09a50c` | front end `setState` |
| `0x07b774` | write savedata (key setup) |

## 13. Open questions

- Exact slot semantics for the unnamed mid-vtable entries of `HoverTank` and the equipment classes.
- AI behaviour (`aiPathFinder.cpp`, cover/defense/offense/roam roles in the speech table).
- Full profile (save) layout, tournament progression and the unlock conditions.
- Pickup and dispenser values (the doubling cheat confirms a multiplier; base amounts are in code
  around `0x2786c`).
- How cheats are unlocked on the Cheats screen.
