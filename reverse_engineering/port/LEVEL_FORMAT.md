# Battlezone PSP: level entity format (`.LVL`)

This is how the game reads a level's entities, taken from its own loader
(`GameType::loadEntityFile` `0x41c14` → `GameType__041288` → `EntityLoader::read` `0x73958`).
It tells a port what every record and property in an extracted `.LVL` means.
`extractors/extract_psp_lvl_json.py` already splits the file into the same records and
properties generically; this document gives them names. Evidence: **code**.

## 1. Files per level

The level table (`.data 0x24e0fc`, 120 records × `0x90`) names one `.LVL` per arena, player
count and mode: 10 arenas (NEWMEX, CANARY, CHINA, RUSSIA, ANTARC, CINQUE, BUENOS, ICELAN,
ARENA1, ARENA2) × 4- or 8-player × 6 modes (DM, TDM, CTF, HZ, KO, FAH). The `World` record in
it names the RenderWare files. The loader (`0x408c8`) opens:

| File | Use |
|---|---|
| `<terrain>.rws` | render world (`rwID_WORLD`) |
| `<terrain>_coll.rws` | **collision world**: tanks, projectiles and raycasts use this, not the render mesh |
| `<terrain>_dyn.rws` | dynamic lights |
| `textures/<terrain>.txd` (or `Online%d.txd` for downloaded arenas) | textures |
| `textures/CinqueWater.txd` | 30 caustic frames when the level has water |

## 2. Container

```
header (16 bytes):  u32 magic 'BZPK' (0x4B505A42), u32, u32, u32 recordCount
recordCount × record
```

Each **record**:

```
u32 size            bytes that follow
u32 id
str name            (≤ 0x28)
str type            (≤ 0x28) — see §3
str                 (skipped)
properties          type-specific, see §3
str                 (skipped)
transform (0x6c bytes): u32 flags, u32, RwMatrix 4×4 floats (right, up, at, pos rows,
                    each x y z w), then 9 more 32-bit values
```

- **`str`:** a u32 length that **includes the 4 length bytes**, followed by the characters.
- **Property:** `u32 len, u32 id, value (len − 8 bytes)`. A scalar property (int or float) is
  12 bytes, so property *k* of a fixed-size type sits at payload offset `8 + 12k`.
- **Property order:** the loader reads properties **by position, not by id**. The tables below
  number them p0, p1, …
- **Placement:** every object is placed with its record's matrix (`setTransform`, vtable
  `+0xd4`). The matrix uses PSP axes (`PORT_SPEC.md` §2).

## 3. Record types

| Type | Tag | Payload | Properties (by position) |
|---|---:|---:|---|
| `JumpPad` | 0 | 0x2c | p0 = direction (vec3), p1 = strength (float; launch velocity = p0·p1), p2 = alternate model (int). Runtime: the nearest tank within 15 m gets its velocity set to `velocity × {0.925, 1.0, 1.05}` by size; scan every 0.1 s, cooldown 0.125 s (`PORT_SPEC.md` §4.4) |
| `Respawn` | 1 | 0xc | p0 = team (int). A spawn point; the matrix is the spawn frame |
| `World` | 2 | 0x3b0 | variable-length property list (§4) |
| `NavBeacon` | 3 | 0xf0 | p0 = beacon id (0–399), p1 = radius (int m, 5–150), p2–p7 = 6 neighbour ids (−1 = none), p8–p13 = 6 per-link speed factors, p14–p19 = 6 per-link values. The loader raises the position by **+3 m**. Alternate beacons (for jump pads) go into a second table (`+0x2b720`) |
| `Door` | 4 | 0x5c | p0 = door type (model table `.data 0x24b718`, stride 0x18), p1–p3 = open offset x/y/z, p4 = scale applied to the offset, p5, p6, p7, p8 = team/lock settings (`+0x160…+0x168`, `+0x14c`, `+0x150`) |
| `Sentinel` | 5 | 0x9c | p0 = weapon id, p1 = health, p2 = team (0 → 1, 1 → 0, else 4), p3 = turn rate, p4 = activation radius (squared at load), p5 = active time before it shuts down, p6 = starts active, p7/p8 = AreaTrigger ids that wake it (−1 = none), p9/p10 = burst on/off times, p11 = unknown, p12 = seconds between shots when it has no triggers |
| `Laser` | 6 | 0x54 | p0 = id, p1 = timer (likely the on/off cycle), p2, p3, p4 = starts on (non-zero) or off, p5, p6 (laser hazard, `0x35814`; p2/p3/p5/p6 are stored at `+0x21c`, `+0x218`, `+0x150`, `+0x154`, meaning not traced) |
| `FlagStand` | 7 | 0xc | p0 = team. Read by the CTF mode (`0x3b4f4`); also creates that team's flag |
| `HotZonePad` | 8 | 0xc | no properties used; pads are numbered in file order (`0x3c438`) |
| `KOChargePad` | 9 | 0x18 | p0 = team, p1 = link value (`0x3d0e0` → `0x321c0`) |
| `Ball` | 10 | 0xc | FAH ball start position (`0x3da4c`) |
| `Dispenser` | 11 | 0x78 | variable-length list: p0 = dispenser type (pickup kind), p1 = respawn time (s), p2 = starts empty (0 = full), p3, p4 |
| `AreaTrigger` | 12 | 0x3c | p0 = trigger id (byte), p1 = radius class (0 → 20 m, 1 → 30 m, 2 → 40 m; stored squared), p2–p4 = flags (`0x23208` stores them at `+0x148…+0x158`; meaning not traced) |
| `Breakable` | 13 | 0x18 | p0 = breakable type (table `.data 0x2336c0`, stride 0x30: model, hit points, sound), p1 = value (health) |
| `RepairPad`, `AmmoPad` | — | — | recognised but **not supported** (the loader prints a warning and skips them) |

Mode-specific records (7–10) are passed to the active mode's vtable slot 4. A mode that
doesn't use them ignores them, so every `.LVL` can carry every mode's objects.

**Per-match limits** (`0x46fb8`): 40 spawn points, 16 jump pads, 12 doors, 12 lasers,
12 triggers, 12 dispensers, 10 breakables, 8 pickups, 2 sentinels, 400 nav beacons.

## 4. `World` properties (partial)

These are the properties the world loader (`0x408c8`) reads, at their offsets in the
`0x3b0` block (value = offset + 8):

| Offset | Meaning |
|---|---|
| `+0x000` | terrain base name (string): gives `<name>.rws`, `_coll.rws`, `_dyn.rws` |
| `+0x048` | extra world model (loaded when the name is ≥ 6 characters) |
| `+0x090` | water model; when present, water and caustics are enabled |
| `+0x0d8` | sky / backdrop model |
| `+0x24c` | RGBA colour (fog or ambient) |
| `+0x258` | value copied to the lighting manager |
| `+0x2cc` | RGBA colour (light) |
| `+0x380` | water flag |
| `+0x38c` | RGB colour |
| `+0x398`, `+0x3a4` | lighting values |

The remaining World properties are presentation (lighting and colours) and aren't needed for
gameplay.

## 5. Open items

- **Unnamed properties:** a few Door, Laser and AreaTrigger properties are named only by where
  they're stored. Their update functions (`Door__029d70`, `0x35110…`, `0x23a7c`) give the rest.
- **Pickup placement:** check it against your extracted LVL JSON. Pickups appear to come from
  Dispensers plus mode code (8 pickup slots at `GameType + 0xf1c0`); there's no `Pickup` record
  type.
