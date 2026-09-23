# Battlezone PSP → Unreal: port audit

This checklist covers every gameplay system in `BOOT.BIN`, from its source-file inventory
(`code_map.json` → `source_files`). For each system it says how well it's verified and where the
spec is. It assumes the assets (models, textures, terrain, audio, movies, CSV tables and the
`.LVL` files) are already extracted.

**Evidence levels:**

- ✅ **emulated**: the game's own code was executed, and the reference reproduces it
  (`validation/`, `golden/`).
- 🟢 **code**: fully read from the decompiled code; the spec has the exact rules.
- 🟡 **partial**: the rules are known, but some details are summarised or unnamed.
- 🔴 **not covered**: not reversed yet.

| # | System | Status | Where | What's left |
|---:|---|---|---|---|
| 1 | Frame timing, dt, fixed physics substep | 🟢 | `PORT_SPEC.md` §1, §4.6 | Confirm 30 fps in PPSSPP (§8.2) |
| 2 | Units, axes, stick sign | 🟢 | §2, `AI_SPEC.md` §8 | BZ98R handedness only |
| 3 | Hover suspension + drive model + nitro | ✅ | §3, `reference/bzpsp_tank.cpp`, `golden/tank_traces.txt` | — (1,809 ticks match) |
| 4 | Tank rigid body (mass, inertia, gravity, impulses) | 🟢 | §3.1, §4.6 | — |
| 5 | Collision solver (bounces, slides, tank-on-tank) | 🟡 | §4.6, `physics_materials.json` | Solver not transcribed; use Unreal physics with the given masses, groups and materials |
| 6 | Collision world | 🟢 (data) | `LEVEL_FORMAT.md` §1 | Use `<terrain>_coll.rws`, not the render mesh |
| 7 | Damage, combos, shield, armor, freeze | ✅ | §4.2, `golden/damage_traces.txt` | — (37 hits match) |
| 8 | Splash, knockback, vampire, burn/freeze on hit | 🟢 | §4.1, §4.3 | — |
| 9 | Weapon firing (cooldown, auto, burst, hold-charge, aim assist, spread, lock-on) | 🟢 | §4.1a | Target cone for lock-on (`0xe650`) summarised |
| 10 | Projectiles: bullet, missile, hitscan, cone, sphere | 🟢 | §4.1, §4.3 | — |
| 11 | Projectiles: mortar and mines (physics bodies) | 🟡 | §4.3 | Detonation trigger in the physics callback |
| 12 | Weapon and projectile data | 🟢 (data) | `port_tables.json` | — |
| 13 | Tweaks (enhancements) | 🟢 | `PSP_GAME_CODE.md` §8 | — |
| 14 | Pickups, dispensers, jump pads | 🟢 | §4.4, `LEVEL_FORMAT.md` | — |
| 15 | Team specials (8 countries) | 🟡 | `PSP_GAME_CODE.md` §7, §4.1a | Per-special effect code (Teleport, Lockdown, EMP details) only summarised |
| 16 | Doors, area triggers, sentinels | 🟢 | `LEVEL_FORMAT.md` §3 | Sentinel p11 unnamed |
| 17 | Lasers, breakables | 🟡 | `LEVEL_FORMAT.md` §3 | Some laser properties unnamed |
| 18 | Match modes: clock, overtime, scoring, 6 modes | 🟢 | §5, `PSP_GAME_CODE.md` §4–5, `reference/bzpsp_match.cpp` | Not emulated yet |
| 19 | Respawn, spawn choice | 🟢 | §5 | — |
| 20 | **Tank AI**: scheduler, perception, targeting, goals, roles | 🟢 | `AI_SPEC.md` §1–4 | Not emulated yet |
| 21 | AI per-mode branches (CTF/HZ/KO) | 🟡 | `AI_SPEC.md` §4.4 | Branches summarised; functions listed for verbatim porting |
| 22 | AI navigation (NavBeacons, A\*) | 🟢 | `AI_SPEC.md` §5, `LEVEL_FORMAT.md` | Beacon data comes from the LVLs |
| 23 | AI weapons, trigger, steering, throttle | 🟢 | `AI_SPEC.md` §6–7 | — |
| 24 | AI difficulty and per-country skill | 🟢 | `AI_SPEC.md` §2 | — |
| 25 | Controls: stick curve, schemes, buttons | 🟢 | `AI_SPEC.md` §8 | — |
| 26 | Camera (chase, first person, sniper, orbit, shake) | 🟢 | §4.5 | — |
| 27 | Level entity format (all 14 types) | 🟢 | `LEVEL_FORMAT.md` | A few properties unnamed |
| 28 | Single-player tournament, tiers, unlock awards | 🟡 | §5.1 | Unlock type → category pairing |
| 29 | Save profile (`BZONE.DAT`) | 🟢 | `PSP_GAME_CODE.md` §11 | Full field map of the 0x9bc profile |
| 30 | Front-end menu flow | 🟢 | `PSP_GAME_CODE.md` §3 | UI layout comes from `menu/*.xml` (data) |
| 31 | HUD (reticle, damage indicators, lock-on, meters) | 🟡 | `PSP_GAME_CODE.md` | Layout is data (`menu/hud_textures.xml` etc.); behaviour not written up |
| 32 | Announcer and speech triggers | 🟡 | `PSP_GAME_CODE.md` §5 (speech table) | Which events play which lines, beyond those noted |
| 33 | Audio (music, SFX banks, 3D sound) | 🔴 | — | Trigger points are visible in code (`audio_psp.cpp`) but not mapped |
| 34 | Multiplayer (ad hoc lockstep) | 🟡 | `PSP_GAME_CODE.md` §10 | Only the architecture; not needed for a single-player port |
| 35 | Cheats | 🟢 | `PSP_GAME_CODE.md` §9 | How they're unlocked |

## What you can build now

The core loop, meaning driving, shooting, taking damage and dying, is specified exactly, and its
two hardest parts (movement and damage) are proven against the game's own code. Match rules, AI,
levels, controls and camera are specified from the code with the formulas and constants you need.
The biggest remaining risk to faithful feel is item 5 (collision response), because the physics
library isn't transcribed.

## Suggested next verifications

1. **More golden traces** (the harness in `validation/pspemu.py` already covers the setup):
   AI target scoring (`0x360c`), steering (`0x27fc`), throttle (`0x5a6c`), weapon firing
   (`0xefc0`) and the match clock (`0x45564`).
2. **PPSSPP runtime checks** (`PORT_SPEC.md` §8.2): frame rate, ride height and projectile speed
   on real hardware timing.
3. **Level cross-check:** run the extracted `.LVL` JSON through `LEVEL_FORMAT.md` for one arena
   and check that spawns, beacons and pickups land where the game shows them.
