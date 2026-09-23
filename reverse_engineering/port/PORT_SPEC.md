# Battlezone PSP: port specification

This is the engine-neutral spec for reproducing Battlezone PSP (2006) gameplay in another engine:
an Unreal Engine port, or mods and mission DLLs for Battlezone 98 Redux. It turns the findings in
[`../PSP_GAME_CODE.md`](../PSP_GAME_CODE.md) into equations, constants and tables you can
implement directly.

| File | What it is |
|---|---|
| `PORT_SPEC.md` | This document: timing, units, per-tick algorithms, rules, engine notes, validation |
| `PORT_AUDIT.md` | Checklist of every gameplay system with its verification status and open items |
| `AI_SPEC.md` | Tank AI (scheduler, perception, targeting, roles, navigation, firing) and the controls |
| `LEVEL_FORMAT.md` | `.LVL` entity records and every entity type's properties |
| `physics_materials.json` | The 58 contact materials (restitution, friction, grip) from `.data 0x24c7c0` |
| `bzpsp_constants.json` | 72 constants from the code, each with unit, `BOOT.BIN` address, PPSSPP address and evidence |
| `port_tables.json` | Shipped data tables (tank motion, tweaks, 39 weapons, 34 projectiles) normalized to snake_case JSON |
| `reference/` | C++17 reference implementation (tank drive/hover, damage, projectiles, collisions, match rules) with tests |
| `validation/` | Runs the game's own functions from `BOOT.BIN` under a CPU emulator and records golden traces |
| `golden/` | Recorded traces: 1,809 tank ticks, 37 damage hits and 72 contact impulses the reference must reproduce |

Regenerate `port_tables.json` from your own extraction:

```text
python scripts/build_port_tables.py --tables <data_tables_json | USRDIR/leveldata> --out port_tables.json
```

Build and run the reference tests (any C++17 compiler):

```text
cmake -S reverse_engineering/port/reference -B build/ref && cmake --build build/ref
ctest --test-dir build/ref --output-on-failure
```

Evidence levels used below. **Emulated** means the game's own code was executed and the
reference reproduces its output (§8). **Code** means read from the decompiled `BOOT.BIN`.
**Data** means read from the shipped CSV tables or `.data`. **Inferred** means reasoned from code
but not yet confirmed. Every address is a static VA in `BOOT.BIN` with load base 0. To get the
PPSSPP address, add the module base (`0x08804000` on a normal boot; check the module list in the
PPSSPP debugger).

---

## 1. Time

- **The simulation is variable-dt, nominally 30 fps (code).** The frame dt is the average vblank
  count of the last two frames divided by 60, and it starts at 1/30 (`0x39ed8`). At the normal
  30 fps it is exactly 1/30 s. When the game drops to 20 fps, dt is 1/20.
- **Tank updates clamp dt to at most 1/15 s** (`0x8f43c`).
- **Projectiles move a fixed distance per update, not per second** (`0x7950`, code). The
  `velocity` column is meters per update, so at 30 fps a projectile covers `velocity × 30` m/s.
  Slowdown on the PSP therefore slows bullets in real time too. For a port, **run gameplay at a
  fixed 30 Hz** and interpolate for rendering. That is the only way to match projectile speeds,
  and it also matches the deterministic lockstep multiplayer (below).
- Multiplayer is deterministic lockstep. The tick is `vblanks × 16667 µs`, inputs are exchanged
  per tick, and stations compare a periodic state hash ("Drift - T P R E"). A port that wants
  PSP-identical replays needs a fixed tick and the same order of operations.

## 2. Units and axes

- The game uses RenderWare conventions: **right-handed, +Y up, meters, seconds, radians**
  internally. Turn-rate tables are in the units shown below.
- A body's orientation is three rows: `right` (local +X), `up` (+Y) and `at` (+Z, forward). In a
  right-handed frame with +Y up and +Z forward, local +X points to the **left** of a viewer
  standing behind the tank. The field is still called `right` in RenderWare.
- `kWorldUp = (0, 1, 0)` (`.data 0x24b7a8`).

Conversions (position; apply the same mapping to direction vectors):

| Target | Mapping from PSP `(x, y, z)` | Notes |
|---|---|---|
| Unreal (left-handed, Z up, X forward, cm) | `UE = (z, −x, y) × 100` | Determinant −1, so this flips handedness as required. Angular velocity is a pseudo-vector: map it as `(−z, x, −y)`, or convert through rotation matrices. |
| BZ98 Redux (left-handed, Y up, Z forward, m) | `BZ = (−x, y, z)` | Inferred. Confirm by importing one arena and checking it isn't mirrored. The same pseudo-vector rule applies: `ω_BZ = (x, −y, −z)`. |

The safest approach in either engine is to **keep the gameplay state in PSP space** (the reference
code does this) and convert only at the boundary: rendering transforms, raycasts and collision
queries. Then the sign conventions in the equations below never need rewriting.

**Stick signs (verify).** Positive `steer` yaws `at` toward local +X, and positive `strafe`
accelerates along local +X. Whether the PSP maps stick-right to positive values is not yet
confirmed at runtime. Watch input `+0x284` and `+0x28c` in PPSSPP while holding right.

## 3. Hover tank

Tank constants (code; see `bzpsp_constants.json`):

| Constant | Value | Where |
|---|---|---|
| Mass | 100 | `0x8f9c8` |
| Inertia (isotropic) | 300 | `0x8f830` |
| Body gravity (applied by the physics library) | 30 m/s² down | `0x8f830` |
| Extra drive gravity (applied by the drive model) | 9.8 m/s² | `0x954a0` |
| Steep limit | 25° (`sin 25° = 0.42261826`) | `0x954a0` |
| Angular decay | 10 /s | `0x954a0` |
| Reverse speed | 0.75 × max | `0x954a0` |
| Air control | 0.25 × accel | `0x954a0` |
| Nitro refill | 0.125 meter/s | `0x954a0` |
| Hover probes (local) | (±2, 0, ±2) | `.data 0x24e0ac` |
| Hover torque scale | 0.4 | `0x94da0` |
| Out-of-world kill | y < −225 | `0x954a0` |

Motion table (`port_tables.json → tank_motion`, data):

| Field | Small | Medium | Large |
|---|---:|---:|---:|
| turn_rate | 21 | 19 | 17 |
| max_fwd_speed (m/s) | 80 | 68 | 55 |
| max_fwd_accel (m/s²) | 100 | 90 | 70 |
| max_side_speed | 55 | 55 | 50 |
| max_side_accel | 95 | 90 | 70 |
| hit_points | 105 | 200 | 325 |
| energy_points / recharge | 300 / 0 | 300 / 0 | 300 / 0 |
| suspension_height (m) | 10 | 10 | 10 |
| hover_damp_coeff | 0.5 | 0.5 | 0.5 |
| suspension_gain | 1000 | 1000 | 1000 |
| upright_gain | 100 | 100 | 100 |
| health regen delay (s) / amount (hp/s) | 6 / 50 | 10 / 75 | 15 / 100 |
| kill energy bonus | 50 | 100 | 150 |
| death splash radius / damage | 60 / 25 | 70 / 35 | 80 / 50 |

The CSV line order is **not** the memory row order. `build_port_tables.py` keys fields by name,
so you don't need the row order unless you're poking PSP memory; for that, see `PSP_GAME_CODE.md` §6.

### 3.1 Order of operations per tick

On the PSP, `HoverTank` drive (`0x954a0`) runs once per frame for each tank:

1. `dt = min(dt, 1/15)`.
2. Compute `tooSteep = at.y > sin25 || |right.y| > sin25`.
3. **Hover** (`0x94da0`) returns a linear and angular impulse. These are applied immediately
   (`0xe11bc`): `v += J / 100`, `ω += τ / 300`.
4. **Drive** reads the new `v` and `ω`, computes new values and **writes them back**
   (`0x0dfa68` sets linear, `0x0dfb44` sets angular). This is velocity-level control, not forces.
5. Later in the frame, the physics library integrates every body with gravity `(0, −30, 0)` and
   resolves collisions.

`reference/bzpsp_tank.cpp` (`driveStep`) does steps 1–4, and matches the game's own code on
every emulated tick (§8.1). `integrateBody` is a
minimal stand-in for step 5 (gravity plus explicit Euler, no collisions).

### 3.2 Hover suspension (`0x94da0`)

Let `h` be the suspension height (10 m) and `p_i = transformPoint(probe_i)`.

- **One probe is cast per tick, round robin** (`i = probeIndex; probeIndex = (i+1) & 3`). The ray
  goes from `p_i` to `p_i − worldUp·h`. `t_i` is the hit fraction in [0,1], or +∞ on a miss.
  All four cached `t` values feed the torque; a slot that is still 0 copies slot 0.
- Compression `c(t) = max(0, 1 − t) · (h + 1)`.
- **Linear impulse** (current probe only, if `t ≤ 1`):
  - spring: `+ c(t)² · gain · 0.5 · dt · up.y` along **world Y**
  - damping: `− hoverDamp · h · 60 · dt · v_local.y` along the tank's **local up**
- **Torque** from all four probes, where `f_k = c(t_k)² · gain · 0.5 · dt`:
  `τ.z += f_k · (p_k.x − pos.x)` and `τ.x −= f_k · (p_k.z − pos.z)`.
- **Self-righting**: `c = up·worldUp`, `axis = worldUp × up` (normalized only when `c ≤ 0`),
  `k = dt · uprightGain · 100 · (2 − c)⁵`, `τ −= axis · k`. If the linear impulse is exactly zero
  (this probe missed), subtract it a second time and mark the tank **airborne**.
- `τ *= 0.4`. If `tooSteep`, clamp the linear impulse's +Y to 0.
- The ride height is cached from the first tank in a static (`0x2aea58`). All shipped sizes use
  10 m, so this has no visible effect.

### 3.3 Drive model (`0x954a0`)

Inputs: `steer`, `throttle`, `strafe` in [−1, 1] and `boost` (input `+0x284/+0x288/+0x28c/+0x294`).

1. **Slow effects** (lockdown/EMP, `+0x840/+0x844`): multiply max speed, turn rate and strafe by
   `slowFactor`.
2. **Steering ramp.** Holding the stick past ±0.98 in the same direction accumulates
   `hold += dt`; anything else resets it to 0. `ramp = clamp((2·hold)², 0.1, 1)` and
   `turn = clamp(0.3·steer + 0.7·steer·ramp, −1, 1)`. When reversing, `turn *= (1 − throttle)`,
   so it can double.
3. **Angular velocity** (skipped when dead):
   `ω = ω·(1 − 10·dt) + R·(0, dt·turn·turnRate, 0) + R·(−2·dt·p, 0, 0)`, where `R` maps local to
   world and `p` is the throttle, **or 1.5 while nitro is engaged and the tank is grounded**
   (vtable `+0xcc`, `0x93b18`), which lifts the nose during a boost. The steady yaw rate is
   `turnRate / 10` rad/s: **2.1 / 1.9 / 1.7 rad/s** (about 120 / 109 / 97 °/s).
4. **Nitro.** Boosting requires `nitroEngaged && meter > 0 && boost`, or a forced boost such as
   Super Ram. Each tick the meter drains by `dt / nitroDuration`; while it's above 0,
   `fwdAccel *= mult`, `maxSpeed *= mult` and `throttle = 1`. An empty meter stops the nitro
   (`0x900b0`): `nitroEngaged = 0` and `nitroOverspeedTimer = 3 s`.
   While not engaged, the meter refills at 0.125/s (8 s from empty). The multiplier comes from
   the nitro weapon (IDs 19–21) plus the Nitro Boost tweak.
5. **Gravity and downforce** (on top of the solver's 30 m/s²):
   `v += up · (−9.8·dt · |v| · 1.5 / maxSpeed)` and then `v.y −= 9.8·dt`.
6. **Accelerations.** `fwdAccel *= 0.5 + 0.5·|throttle|` and `sideAccel *= 0.5 + 0.5·|strafe|`.
7. **Targets.** `tf = maxSpeed·throttle` (×0.75 when reversing) and `ts = maxSide·strafe`. If
   `hypot(tf, ts) > maxSpeed`, then `ts *= cap/mag` and `tf *= cap / hypot(tf, v_local.x)`, where
   `cap = max(maxSpeed, maxSide)`. The second formula uses the **current** lateral speed. That's
   a quirk of the original; keep it.
8. **Slope limit.** When climbing (pitch `at.y` in the direction of travel), acceleration falls
   linearly to 0 at 25°: `a·(sin25 − pitch)/sin25`. Downhill gets the full value. Strafe uses
   roll (`right.y`) the same way.
9. **Airborne** (and no nitro): both accelerations ×0.25.
10. **Approach.** `dvF = dt·aF` and `dvS = dt·aS`. If the error `|v_local − target| / max` is
    below 0.2, scale by `error·5` so the speed eases in. If already faster than the target,
    negate; when not braking (throttle ≥ 0), also halve it (coasting). For 3 s after a boost
    ends, `dvF -= nitroMult` each tick while last tick's forward speed is above `maxFwdSpeed`.
11. `dv = R·(dvS, 0, dvF)`. If `tooSteep`, clamp `dv.y ≤ 0`. Then `v += dv` and write `v` back.
12. `pos.y < −225` kills the tank (`takeDamage(10000)`).

Top speeds with the shipped table: **80 / 68 / 55 m/s** forward and **60 / 51 / 41.25 m/s** in
reverse. The Top Speed tweak adds +5 (major) or +2 (minor) to `maxFwdSpeed` at spawn. The reference
tests reproduce these.

### 3.4 Health, energy and death

- **Energy is ammo**: 300 for every size, no passive recharge. Firing costs the weapon's
  `energy_cost` (`0x99424`). Kills give 50/100/150. Pickups, dispensers and the Charge Weapon
  Energy tweak refill it.
- **Regen** (`0x97b30`): after `regenDelay − tweak` seconds without damage, heal
  `healthRegenAmount · dt` up to max. Any damage above 0 resets the timer and breaks invisibility.
- **Death**: splash of 25/35/50 within 60/70/80 m (linear falloff, §4.2), plus camera shake.

## 4. Weapons, projectiles and damage

### 4.1 Firing and projectile motion

- **Spawn** (`0x7328`): speed = `velocity + 0.05 · shooterSpeed`. The shooter-speed term is added
  only when positive. The projectile's damage is `def.damage + weapon[+0x248]`: **the Damage
  tweak is a flat bonus** (+10 major, +5 minor), not a percentage. Vampire adds to `steal_scale`
  (`weapon +0x24c`), and the Splash tweak adds meters to the radius (`weapon +0x244`).
  `rand_vel_slow` is meant to subtract `velocity × random(0..rand_vel_slow)`, but the code
  truncates it to an integer first (`trunc.w.s` at `0x74ac`). Every shipped value is below 1, so
  the slowdown is always 0. The random number is still drawn, which matters only for
  lockstep determinism.
- **Motion** (`0x7950`): `pos += velPerUpdate` every update. The projectile expires when
  `age ≥ life_span` (seconds); explosives detonate on expiry.
- **Missiles** (`0xb998`): no steering until `lock_delay` has passed. After that, the missile turns
  toward `targetPos + targetVel·dt` by at most `max_turn_rate · dt` degrees per update, then its
  velocity is re-aligned with its new `at`. Steering happens before the move in the same update.
  The missile drops its target when the target reports dead. Lock-on range is at
  `weapon +0x1bc` (plus the Lock-on tweak, +50/+25 m).
- **Pool sizes by type** (`ProjectileMgr`): 0 bullet 150, 1 explosive 40, 2 missile 75,
  3 nitro (none), 4 hitscan 20, 5 sonic cone 10, 6 sphere burst 10. A port can size its pools to
  match. What happens when a pool is full (drop the shot or recycle the oldest) isn't confirmed.
- **Knockback** (`0x94758`, code): the hit tank gets a linear impulse of `force × 10000` along the
  normalized hit direction (`+X` if the direction is zero), applied like the hover impulse
  (`v += J/100`). So `Δv = force × 100` m/s, for example 10 m/s for a Vulcan round, and the drive
  model then pulls the tank back toward its target speed. That `force` is the projectile's
  `impact_force` column is inferred from the call sites.

### 4.1a Weapon firing (code: `0xefc0` update, `0xdb80` press, `0xdc44` release, `0xd4a0` shoot)

Each tank has a main gun and two side-weapon slots. The fields come from `BZ_WEAP_DEFS`
(`port_tables.json → weapons`).

- **Press (`0xdb80`):**
  - Hold-charge weapons start charging.
  - Otherwise, a non-auto weapon fires once if its cooldown is 0; a burst weapon starts a
    burst instead.
- **Held (`0xefc0`, every frame):** the cooldown counts down by dt. An auto-fire weapon
  (`is_auto_fire`) fires again as soon as the cooldown reaches 0.
- **Release (`0xdc44`), hold-charge weapons:** `charge = min(held, hold_charge_max_time) /
  hold_charge_max_time`. If it was held longer than 1 s, the energy cost for this shot is
  `energy_cost × held × 1.3`. Then it fires. The charge fraction scales the projectile through
  `hold_charge_damage_scale` / `hold_charge_vel_scale`.
- **Bursts** (`burst_cnt > 0`):
  - **All at once** (`burst_delay == 0`): all shots fire this frame, each offset by
    `burst_spread` degrees of heading (or `burst_offset` metres sideways).
  - **Spaced:** otherwise one shot every `burst_delay` seconds until `burst_cnt`.
- **Shooting one round (`0xd4a0`):**
  1. If `energy < cost`, fail and show "not enough energy". Otherwise subtract the cost
     from the tank's energy (the energy pool is the ammo).
  2. Take the muzzle frame and level out its roll.
  3. **Aim assist** (non-tracking weapons with `is_aim_assist`):
     - pitch: turn toward the target when the pitch error is ≤ `assist_pit`
     - heading: turn toward it when the error is ≤ `max(assist_hdg, 7°)`
  4. Apply the burst offset. Then add random spread: a uniform integer in
     ±`rand_fire_hdg_offset`° about up and ±`rand_fire_pitch_offset`° about right.
  5. Spawn the projectile (`bullet_id`) from that frame (§4.1). Reset the cooldown to
     `fire_delay`.
  6. Semi-tracking weapons hand their current lock to the projectile.
- **Lock-on** (`0xe650` picks the best target in the weapon's cone and `lock_on_range`):
  - **Full tracking:** the same target must stay selected for `lock_delay` seconds.
  - **Semi tracking:** locks on immediately.
  - **Basic tracking:** only uses a target for aim assist.
- **Team special** (`0x10834`): the charge meter rises at the special's rate while it isn't in
  use. It's ready at the maximum, and drains while active. The Team Special tweak and the
  fast-special cheat change the rate or duration (§4.4).

### 4.2 Damage (`0x99be8`, `0x98808`)

Order of evaluation for each hit:

1. Ignore the hit if the target is invulnerable (`+0x2c`). `0x99be8` does **not** check for a
   dead target, and hitting one runs the death handler again, so skip dead targets in the caller.
2. **Combo**: Swarm (bullet 14) ×13 and A.E. Fusion (bullet 8) ×15 on the **third** hit, when each
   gap between consecutive hits is ≤ 2 s. If a gap is too long, the oldest hit is dropped and
   counting continues.
3. **Germany Armor special**: `damage *= armorScale` (0.8).
4. **Armor pickup shield** (150 hp): absorbs damage first. Any overflow passes through and turns
   the shield off (its leftover hp value is left in place).
5. **Frozen** (`+0x150`, set by Canada's Liquid Nitrogen, `0x9987c`): any positive damage first
   sets HP to 0, so the hit shatters the tank. The frozen flag itself stays set.
6. If damage > 0, reset the regen timer (`+0x28`). Subtract from HP. At ≤ 0 the HP is clamped to 0
   and the tank dies (state 2, vtable `+0xbc`).

**Splash** (`0x64e8`): `damage · (1 − d/R)` for `d < R`, and 0 outside. It's linear.

### 4.3 Weapons table

`port_tables.json → weapons / projectiles` holds every shipped row. The player-relevant summary
(with the tracking, burst and team-special notes) is in `PSP_GAME_CODE.md` §7. Useful derived
numbers at 30 Hz:

| Weapon | Projectile speed (m/s) | Range ≈ speed × life (m) |
|---|---:|---:|
| Vulcan Cannon (bullet, 30/update, 3 s) | 900 | 2,700 |
| Plasma (default, 25/update, 3 s) | 750 | 2,250 |
| Phoenix Rocket (missile, 15/update, 6 s) | 450 | 2,700 |
| Homing Missile (7/update, 6 s) | 210 | 1,260 |
| Swarm (6/update, 4 s) | 180 | 720 |

Every projectile class (the vtables at `0x255b24`…`0x255c8c`) and its per-update behaviour:

| Type | Class update | Behaviour |
|---|---|---|
| 0 bullet | `0x7950` | moves `velocity` m per update and hit-tests the segment (§4.1) |
| 2 missile | `0xb998` | steers, then moves like a bullet (§4.1) |
| 4 hitscan | `0x9ed0` | **one** ray from the muzzle, **`velocity` metres long** (1000 m for Rail, Fusion and KGB; 150 m for NOD; 500 m for the ray specials). Hits the collision world or the first tank; the projectile ends after this single update |
| 1 explosive | `0x98e8` | a **physics body**: a 2 m sphere launched at `at × speed` (`0x93a0`). Its own gravity is **−240 m/s² for the Mortar (id 18)** and −20 m/s² otherwise (Mines, id 11). Mortar and id 10 add the shooter's speed (id 10 twice). It bounces through the physics engine and explodes with linear splash. It arms after 0.5 s (id 10), 1 s (Mines) or 0.15 s (Mortar); the Mortar explodes on hitting the ground, and armed shells explode on touching a tank (§4.6) |
| 5 sonic cone | `0xbe9c` | **once**: every tank in front of the muzzle within `assist_max_dist` of the *weapon* row and within ±`assist_hdg` degrees horizontally takes the damage (the cone reuses the weapon's aim-assist columns) |
| 6 sphere burst | `0xc2cc` | **once**: every tank within `explod_radius` + the Splash tweak takes the damage (no falloff) |

**On hit** (`0x62f4`), in order:

1. Apply the damage (§4.2).
2. Knockback along the projectile's velocity.
3. Vampire: the shooter heals `damage × steal_scale`.
4. Burn, control-dampen and freeze effects, each if the projectile defines it.

Splash explosions (`0x64e8`) do the same for every tank and breakable within the radius, using
linear falloff.

### 4.4 Pickups, jump pads and specials (code, `0x2786c`)

| Pickup type | Effect |
|---|---|
| 0 Armor | 150-hp shield for `25 + pickup tweak` s |
| 1 Double Damage | 20 s (hard-coded; the dispenser passes `10 + tweak`, but the function stores 20) |
| 2 Drone | spawns a helper drone (its gun is weapon 31) |
| 3 / 4 Energy | +50% / +100% of max energy |
| 5 / 6 Health | +50% / +100% of max HP |
| 7 Invisibility | `15 + tweak` s |
| 8 Regen | +10 hp/s for `15 + tweak` s |

The pickup-doubling cheat (bit `0x10`) doubles these effects.

**Jump pads** (`0x31ad0`): every 0.1 s, the nearest tank within 15 m of the pad has its velocity
**set** to `padVector × sizeScale`, where `sizeScale` is {0.925, 1.0, 1.05} for small, medium and
large. Then the pad waits 0.125 s.

Team specials and their recharge times are in `PSP_GAME_CODE.md` §7. The Team Special tweak
subtracts 10/5 s from the recharge, and the fast-special cheat sets it to 3 s (15 s for Armor and
Invisibility).

### 4.5 Camera (code: `0x70918` update, `0x702f0` chase placement)

**Views:** the offset table at `.data 0x24cff0` is indexed [view][tank size]. Each entry is an
offset in the tank's frame (x, y, z) plus a pitch in degrees.

| View | Small | Medium | Large |
|---|---|---|---|
| 0 near chase | (0, 5, −16), 2° | (0, 5, −18), 4° | (0, 6.5, −20), 2° |
| 1 far chase | (0, 6, −36), 13° | (0, 7, −38), 19° | (0, 7, −40), 19° |
| 2 first person | (0, 1.5, 0) | (0, 1.5, 0) | (0, 1.5, 0) |
| 3 sniper / zoom | (0, 1.0, 0) | (0, 1.25, 0) | (0, 1.5, 0) |

**Chase placement** (`0x702f0`, views 0/1):

1. Build a level frame from the tank's heading, ignoring its pitch and roll.
2. **Terrain look-ahead:** cast two rays down the collision world, from 10 m above to 40 m
   below, one at the tank and one 20 m ahead. The height difference gives a slope pitch.
3. Limit that pitch to ±10° (`0x24d0c8`/2); downhill it's ×0.75. Smooth it toward the target
   at rate 3 (`0x24d0c4`).
4. Pitch the frame by `viewPitch − slopePitch`. Offset it by `offset × 1.8` (`0x24d0e0`), with
   Y additionally ×0.8.
5. Smooth the result with a lag of 0.33 (`0x24d0c0`, `0x6e744`).
6. **Look-at point:** raise it 5 m (`0x24d0dc`) above the tank. The lift shrinks linearly when
   the first ray's hit is closer than 0.3.
7. If the camera jumps more than `1.25 × 1.8 ×` the offset length in one frame, the game logs it
   and snaps.

**Other views:**

| View | Behaviour |
|---|---|
| 2 (levelled) | the tank frame with half its pitch and roll removed (`0x24d0d8` = 0.5) |
| 3 | attached to a weapon's scope frame (sniper weapons, `tank + 0x170`) |
| 4 | fixed |
| 5 | orbits at 15°/s about world up (intro and death) |

**Shake** (`0x7020c`): a hit or explosion starts a shake of the given magnitude and duration.
Each frame the camera moves by `rand(−2..2)·mag` in local x and y. A bigger shake replaces a
smaller one. Death shakes use the motion table's death shake columns.

### 4.6 Physics world and collisions (code: `PhysicsEngine.cpp` `0x4f9b0`, `0x4fd98`, `0x4ff4c`; solver `0x11b658`)

`reference/bzpsp_collision.cpp` implements this section. Its impulse is checked against the
game's own code (`golden/contact_traces.txt`, §8.1).

#### Timing and bodies

- **Fixed 60 Hz physics.** Each frame's dt (clamped to 1/15 s) is added to an accumulator, and
  the solver steps **1/60 s** while the accumulator is positive (`0x4fd98`). At 30 fps that's
  2 physics steps per game update. The drive model (§3) still runs once per frame. In Unreal,
  use a 1/60 substep for the tank bodies, or integrate twice per 30 Hz tick.
- **Bodies** (`0x4ff4c`):
  - **Tank:** mass 100, inertia 300, gravity (0, −30, 0), collision group 1 (`0x8f830`).
  - **Everything else** (doors, breakables, dispensers, sentinels, mode objects, explosive
    shells): group 6, mass 2 (breakables 0.2), default gravity −9.8 unless overridden (shells:
    −20, Mortar −240). Doors appear to be made immovable (body flag `0x20`, `0xe1244`).
- **Collision groups** (`0x4f9b0`, table at `world +0x218`): 0 = don't test, 2 = full contact.
  The library also supports 1 (report to the pre-collide callback only, no response), but no
  pair uses it. Group 0 is the static world.

| | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|---|
| **0** world | – | ✓ | ✓ | ✓ | – | ✓ | ✓ |
| **1** tank | | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **2** | | | – | – | – | – | – |
| **3** | | | | – | – | – | – |
| **4** | | | | | – | ✓ (5,4) | – |
| **5** | | | | | | ✓ | – |
| **6** objects | | | | | | | ✓ |

  Only groups 1 and 6 are used, so in practice **everything collides with everything**: tanks
  with the world, with each other and with every object.

#### Collision shapes

A body's shape is a list of primitives, 0x60 bytes each: `+0x00` size (sphere radius, or box
extents x, y, z), `+0x10` local matrix, `+0x50` flags (low byte = type: 0 box, 2 sphere;
bits 8–15 = material id; bit 31 = disabled). When a body has more than one primitive, the first
is a bounding sphere, and it's skipped by the contact tests.

- **Tank** (`0x8f464`, the same for all sizes): five colliding spheres of **radius 2.5 m**, all
  **material 11**, in tank-local space (x left, y up, z forward):

  | Sphere | Centre (m) |
  |---|---|
  | front left, front right | (±3, 0.5, 5) |
  | rear left, rear right | (±3, 0.5, −5) |
  | top | (0, 1.2, 0) |

  The bounding sphere is at the origin with radius √34 + 2.5 ≈ 8.33 m. The four corners come from
  `.data 0x24e064`; their y is set to 0.5 the first time a tank is built.
- **Door** (`0x298f4`): one box, sizes (24, 13.25, 1.5) from the door table (`.data 0x24b718`
  `+4..+0xc`), material 0. Whether these are full or half extents isn't traced; compare them with
  the door model.
- **Other objects** appear to take their primitives from the model's collision proxy
  (`0x77134`, types 0/1/2).

#### World contacts (the live path)

The library has two ways to collide against the level. `.data 0x24c790` selects one. It is 1 in
the data and nothing writes it, so the game always uses the RenderWare path below. The "THD v3.0"
HOTS tree and Proxy loaders (`0x50128`, `0x502f4`) have no callers and are dead code.

For each colliding sphere of a body (`0x121280`):

1. Query the level's **`<terrain>_coll.rws`** collision world with an intersection sphere
   (`RpCollisionWorldForAllIntersections`, `0x1df614`).
2. For each triangle (`0x120ff0`), find the closest point: the projection onto the plane if it
   falls inside the triangle, otherwise the nearest point on its three edges. Keep the closest
   triangle.
3. The penetration depth is `radius − distance`.

The **deepest sphere wins: one world contact per body per step.** The contact normal is the
chosen **triangle's face normal**, and the contact point is its closest point.

On this path the world side's material is always **0**, whatever the triangle's material. The
per-triangle material only reaches the game through the hover raycast (`bdInt.attr`, §3.2).

#### Body–body contacts

- **Broadphase:** bounding boxes must overlap, and the group pair must be non-zero (`0xdf0b4`).
- **Narrowphase** (`0xe594c`): every pair of non-disabled primitives is tested with the
  sphere/box routines (`0xe48e8`). The deepest pair gives the **one contact** for the two bodies,
  with the two primitives' materials.

#### Contact filtering and game callbacks

A contact is dropped when it has no points, its depth is ≤ 1e-5, or **either material is 19**
(`0x11b844`). Otherwise:

1. **Pre-collide** (game `0x3ee94`, registered at `world +0x204`) runs with `(A, B)`, dispatched on
   **A's** type. It runs only when one of the two bodies has the callback flag. It returns
   **10** to resolve the contact, and any other value (the game uses **9**) ignores it: no
   impulse, no push-out. Game rules are below.
2. **Response** (below).
3. **Post-collide** (game `0x3f01c`, `world +0x208`) runs after the response.

#### Response (`0x11b658` → `0x1461cc` → `0x137c40` → `0x13661c`)

1. **Coefficients.** The material table (`physics_materials.json`) holds, per material,
   **a = restitution**, **b = friction** and **c = grip**. They combine per contact as:
   - `μ = b_A · b_B`
   - `e = a_A · a_B`
   - `c = (c_A + c_B) / 2`

   An invalid id uses (a, b, c) = (0.5, 0.77, 1.0). If a pre-collide callback changes a body's
   material, the coefficients are recomputed.
2. **Impulse** (`0x13661c`, **emulated**). The contact frame has z along the normal. `v` is the
   relative velocity of the contact points (approaching means `v.z < 0`), and `K` is the
   contact's 3×3 inverse-mass matrix (impulse to velocity change):

   ```text
   jn    = −v.z / K_zz                  normal impulse that just stops the approach
   stick = −K⁻¹ v                       impulse that stops the contact point completely
   d     = stick − (0, 0, jn)           its tangential remainder
   P     = (1 + e)·jn·ẑ + c·d
   if (μ·P.z)² < P.x² + P.y²:           outside the friction cone
       s = |d.xy| − μ·d.z
       P = |s| > 1e-5 ? (1 + e)·jn·ẑ + (μ(1 + e)·jn / s)·d : 0
   ```

   `c` scales how much of the sliding is stopped: 0 gives a frictionless contact, 1 stops it up
   to the Coulomb limit `μ`. `P` is applied at the contact point to both bodies, equal and
   opposite, linear and angular. `K` is the usual two-body contact matrix; the library builds it
   in `0x137c40`.
3. **Resting contact.** The library compares the speed change with `4·√(0.02·|g|)`, where `g` is
   the world's gravity vector (world `+0`). Below it the contact counts as resting (the library
   may put the bodies to sleep); above it, both bodies are woken. The exact sleep rules aren't
   traced.
4. **Push-out** (`0x145b2c`). The push is `depth · normal`, **clamped to 1 m**. The bodies share it
   by mass: A moves by `push · m_B/(m_A + m_B)` and B by `−push · m_A/(m_A + m_B)`.
   - A body that isn't an awake dynamic body (the world, a door, a sleeping body) counts as
     mass 1e16, so it doesn't move.
   - When both bodies are awake and one is a **tank**, the tank counts as immovable (A is checked
     first). A tank shoves other objects out of itself, and in a tank–tank contact only body B is
     pushed.

What a tank gets from this:

| Contact | μ | e | c | Effect |
|---|---:|---:|---:|---|
| Tank vs world, door (materials 11 + 0) | 0.252 | 0.0825 | 0.5 | nearly dead bounce; half the sliding is stopped, capped at μ = 0.25 |
| Tank vs tank (11 + 11) | 0.09 | 0.0225 | 0 | **frictionless** normal push, almost no bounce |

#### Game rules on contact (code)

Pre-collide on a **tank** (`0x94014`) against:

- **Another tank:** returns 10 (normal contact).
- **A breakable:** `ratio = |forward speed| / max forward speed`. The breakable takes damage when
  `ratio > 0.5`, or it has under 5 hp, or the tank is **AI-driven** (controller `+0x7c`, set only
  for human players). The damage is `min(ratio, 1.5) × 100`, **+1000 for AI tanks**, so AI always
  ploughs through. Once destroyed, the breakable returns 9 and the tank passes through. It then
  ignores ram damage for 0.75 s (`0x25024`).
- **A projectile:** see the explosive-shell rules below.
- **A mode object** (`0x30c68`, the `fieldglow` objects created at `0x30744`): returns 9 (pass
  through) when the object's owner value (`+0xd0`) matches the tank's (tank vtable `+0x54`,
  presumably the team), and 10 otherwise. Either way the object plays its glow for 0.5 s.

Post-collide on a **tank** (`0x93d20`):

- **Hit flags:** it sets "hit the world" (`+0x978`) or "hit a tank" (`+0x97c`).
- **Ramming another tank** (checked in this order):
  - **Super Ram active** (`+0x944`, the forced nitro): the other tank takes **10000 damage** (a
    kill) and an impact of `tank +0x594` along the rammer's forward.
  - **Otherwise,** when the rammer's slot-0 object (`+0x1c4`) is active and its cooldown
    (`+0x30`) is over, and the other tank is in front: the other tank takes `slot+0x294 × s`
    damage, a hull flash, and an impact of `slot+0x29c × s` along the rammer's forward. Here
    `s = min(0.75 × forward speed / max forward speed, 1.5)`. The cooldown then restarts from
    `slot+0x2c`.
    - "In front" (`0x1071c`): with `dir` the unit vector from the rammer to the other tank,
      `|dot(right, dir)| < 0.9` and `dot(at, dir) ≥ 0`.
    - "Active" is slot vtable `+0x34` = `0x106b8`: `+0x24` set and `+0x28c` clear. This object's
      activation shows the `FXSuperram_Ram` effect.
- **Doors:** a door in state 3 (**closing**) deals **10000 damage** to a tank touching it, from
  either side's callback (`0x2aaac`, `0x2aa1c`).
- **Impact sound** (`0x91fe4`): when the vector the solver passes (its length, probably the
  contact impulse) divided by 100 exceeds 2, and more than 0.5 s has passed since the last one,
  one of 3 impact sounds plays. Its volume is `0.1 + 0.2·min(x/40, 1)`.

**Explosive shells** (Mortar id 18, Mines id 11, id 10; `0x9ca8` on the shell, `0x9dc0` on the
tank):

- **Arm time** (`0x99a0`): id 10 0.5 s, Mines 1.0 s, Mortar 0.15 s, counted on the shell's age
  (`+0x14`).
- **Against the world:** the **Mortar explodes on impact** (even before it arms). Other shells
  bounce with the normal response.
- **Against an object before arming:** ignored (9). The shell passes through everything,
  including its owner.
- **Against an object once armed:** it explodes when the object is a **tank** (any tank,
  including the owner), or on any object for the Mortar. The contact is ignored either way.
- **Mines:** each weapon keeps its last **3** live mines (`weapon +0x230`). Laying a fourth
  detonates the oldest (`0xddd0`).

#### Porting

- **Collision geometry.** Use `<terrain>_coll.rws` as the level's complex collision, and give
  each tank the five 2.5 m spheres above (250 cm in Unreal).
- **Faithful response.** Run your own contact step per 1/60 substep instead of the engine's
  solver:
  1. Take the deepest sphere-versus-world contact per tank, with the triangle's normal.
  2. Take the deepest sphere pair per tank–object or tank–tank pair.
  3. Call the pre-collide rules.
  4. Apply `contactImpulse` at the contact point.
  5. Apply `separate`.
  6. Call the post-collide rules.

  This reproduces the game's contact model exactly. The one approximated part is the library's
  sleep handling.
- **Engine physics.** With Chaos or PhysX, set a physical material per body with the combined
  values from the table above: combine mode *Multiply* for friction and restitution, tank
  friction 0.3 and restitution 0.15, world 0.84 and 0.55. The grip factor `c` has no engine
  equivalent; it is what makes tank–tank contacts frictionless. Expect bounces and slides to be
  close but not identical.

## 5. Match rules

`reference/bzpsp_match.cpp` implements the shared clock and scoring. The per-mode details are in
`PSP_GAME_CODE.md` §4–5 and `bzpsp_constants.json`.

- **States**: intro (single player: at least 5 s, then a button press), then a 3 s countdown,
  then playing, then game over. The one-minute warning plays at 61 s left.
- **Time-out**: if there's a unique leader (or unequal team scores), that side wins. On a tie,
  overtime extends the limit by `max(10% of the original limit, 60 s)`, and this repeats.
- **Kills**: +1 to the killer. Suicide or a team kill is −1 (player score is clamped at 0; TDM also
  adjusts the team score). Killing the CTF flag carrier or the FAH ball holder gives +5. The score
  limit is checked per player (DM, FAH) or per team (TDM).
- **Respawn**: a human is forced back after 35 s on the respawn screen, and AI respawns after 4 s.
  The spawn point chosen is the one farthest from all tanks, lifted 5 m.
- **CTF**: 20 m touch radius; a dropped flag returns after 20 s; a capture gives +5 to the player
  and +1 to the team; a return gives +1.
- **Fox and Hound**: 15 m pickup radius; a dropped ball resets after 15 s; the holder scores every
  2 s.
- **Hot Zone**: 30 m pads checked every 1 s; capture takes 3 s. With 1/2/3 pads held, the team
  scores a point every 3/2/1 s.
- **Knockout**: each core has 1,600 shield, then 100 health. Destroying one gives 5 points. A
  charge pad (20 m, linked within 150 m) ticks every 1 s; each teammate on it pays 10 energy for
  +20 core health, and that also revives a destroyed core.

### 5.1 Single-player tournament (code: `BZFrontEnd::readSPTourneyFile` `0xc2164`, `0xc2a04`, `0x9aa24`, `0x9ae7c`)

- **Matches:** `BZ_SP_TOURNEY_DEFS.CSV` has up to 28 rows (20 used). The loader reads the
  columns in order:

| Column | Type | Used as |
|---|---|---|
| c0, c1, c2 | int | match settings, copied into the match config (`+0x114`, `+0x10c`, `+0x110`); use the CSV header names |
| c3 | int | game mode |
| c4 | int | opponent country code 27–34, which selects the country's AI info page |
| c5 | string | the level |
| c6 | — | ignored |

- **Tiers** (`.data 0x240764` start index, `0x24076c` count): tier 1 is matches 0–4,
  tier 2 is 5–11, tier 3 is 12–18, and the final is match 19.
- **Awards after each match** (the player's score against the other scores, `0x9b1e4`):
  - **Placed** (score ≥ the runner-up's score): sets the progression flag from table B
    (`.data 0x252aa8`, all type 5), which opens later matches.
  - **Won** (score equals the top score): also sets the prize from table A (`.data 0x2529d0`).
    Winning the final, whose table A entry is type 5 index 20, plays the victory speech and the
    medal ending.

| Match | Prize (type, index) | Match | Prize (type, index) |
|---:|---|---:|---|
| 0 | (3, 3) | 10 | (1, 11) |
| 1 | (2, 4) | 11 | (2, 2) |
| 2 | (0, 0) | 12 | (1, 16) |
| 3 | (1, 2) | 13 | (1, 10) |
| 4 | (3, 4) | 14 | (1, 15) |
| 5 | (0, 2) | 15 | (2, 3) |
| 6 | (1, 8) | 16 | (1, 14) |
| 7 | (2, 7) | 17 | (3, 5) |
| 8 | (1, 17) | 18 | (2, 6) |
| 9 | (1, 7) | 19 | (5, 20) final |

- **Unlock flags:** stored in the save profile at `+0x5a8 + type·0x74 + index·4` (6 types ×
  29 slots). The speech lines `unlock_arena`, `unlock_movie`, `unlock_tier`, `unlock_tank` and
  `unlock_weapon` name the categories. The type-to-category pairing isn't traced yet; type 1
  indices look like weapon ids, and type 5 is progression. Per-match completion flags are at
  profile `+0x7ec` and `+0x860` (`PSP_GAME_CODE.md` §11 has the save format).

## 6. Unreal Engine notes

- **Tick**: run gameplay at a fixed 30 Hz from an accumulator (for example a subsystem that steps
  every tank, projectile and mode object in PSP order) and interpolate transforms for rendering.
  Don't use Unreal's variable `DeltaTime` for projectiles, which move per update (§1).
- **Tank movement**: prefer a **custom movement component** over Chaos rigid bodies. The PSP drive
  model writes velocities directly; only the hover impulses are "physical", and they are
  pre-divided by mass/inertia. Per tick:
  1. Call `driveStep` in PSP space. It uses your raycast callback, which converts the segment to
     UE space and runs `LineTraceSingleByChannel`.
  2. Apply gravity `v.y −= 30·dt` (turn off Unreal gravity on the actor).
  3. Move with `SafeMoveUpdatedComponent` / `SlideAlongSurface` and integrate rotation by `ω·dt`.
     The reference's `integrateBody` is steps 2–3 without collision.
- **If you must use Chaos**: turn off Unreal gravity, apply `(0, −30, 0)` yourself as an
  acceleration, and write velocities with `SetPhysicsLinearVelocity` /
  `SetPhysicsAngularVelocityInRadians` from the drive result every substep. Set mass 100 kg and
  inertia 300 on all axes (Unreal uses kg·cm², so multiply by 10⁴), turn off
  linear/angular damping (the 10/s angular decay is in the drive model), and don't expect
  identical collision response (§9).
- **Units**: multiply lengths by 100 and use the axis mapping in §2. Angles in the tables are in
  the units shown there; `max_turn_rate` for missiles is in degrees per second.
- **Data**: import `port_tables.json` into `UDataTable`s (one row struct per table) and
  `bzpsp_constants.json` into a `UDataAsset`. The reference `.cpp` files compile unchanged in an
  Unreal module if you wrap `bzpsp::Vec3` ↔ `FVector` at the boundary.

## 7. Battlezone 98 Redux notes

BZ98R's hover physics is its own model (`HoverCraftClass`), so ODF tuning can only approximate the
PSP feel. For exact behaviour, drive the tank from a mission DLL using the reference code: compute
velocity with `driveStep` and set it with the DLL's velocity and position setters every tick. Check
the exact semantics of each BZ98 parameter against the BZ1 source you have before relying on the
mapping below.

Approximate ODF mapping per size (small / medium / large):

| PSP field | BZ98R parameter (approximate) | Value |
|---|---|---|
| max_fwd_speed | `[HoverCraftClass] velocForward` | 80 / 68 / 55 |
| 0.75 × max_fwd_speed | `velocReverse` | 60 / 51 / 41.25 |
| max_side_speed | `velocStrafe` | 55 / 55 / 50 |
| max_fwd_accel | `accelThrust` | 100 / 90 / 70 |
| turn_rate / 10 (steady yaw, rad/s) | `omegaTurn` | 2.1 / 1.9 / 1.7 |
| angular decay 10/s | `alphaDamp` | 10 |
| equilibrium ride height (measure in PPSSPP) | `setAltitude` | TBD |
| hit_points | `[GameObjectClass] maxHealth` | 105 / 200 / 325 |
| energy_points | `maxAmmo` (with `addAmmo = 0`) | 300 |
| projectile `velocity × 30` | `[OrdnanceClass] shotSpeed` | e.g. Vulcan 900 |
| `life_span` | `lifeSpan` | per row |
| `damage`; `explod_radius` | ordnance damage; explosion `damageRadius` | per row |

- Distances are the same meters, so arenas import 1:1 with the axis flip in §2. Remember that BZ98
  explosion damage falloff may not be linear; the PSP's is (§4.2).
- Health regen, the 3-hit combos, pickups, jump pads and the six match modes all belong in the
  mission DLL. `reference/bzpsp_combat.cpp` and `bzpsp_match.cpp` are written to drop into one.

## 8. Validation

### 8.1 Golden traces from the game's own code (done)

`validation/pspemu.py` loads `BOOT.BIN`, applies its relocations and runs game functions in
Unicorn (`pip install unicorn`). Integer and FPU code runs natively. The PSP's VFPU vector
instructions are interpreted following PPSSPP's register layout. A few calls are replaced:

- **Rigid-body accessors:** replaced with the simple-body model the physics library implements
  (`0x119060`): `v += J·(1/m)`, `L += τ`, `ω = I⁻¹L`.
- **RenderWare point and vector transforms:** replaced.
- **World raycast:** replaced with an analytic ground (flat or a 20° slope).
- **Presentation calls** (audio, HUD, tint, messages): stubbed.

| Script | Game function | Scenarios | Result |
|---|---|---|---|
| `tank_traces.py` | HoverTank drive `0x954a0` (with hover `0x94da0`) | idle, full throttle, reverse, held turn, strafe, nitro, slowed, 40 m drop, 30° tilt recovery, 20° slope climb; all sizes | 1,809 ticks; `driveStep` matches every tick within 1e-5 relative |
| `damage_traces.py` | Vehicle::takeDamage `0x99be8` (with combo `0x98808`) | plain hits, both combos in and out of the 2 s window, mixed IDs, shield, armor, armor + shield, frozen, invulnerable, overkill | 37 hits; `takeDamage` matches every field |
| `contact_traces.py` | Contact impulse `0x13661c` (friction, restitution, grip, friction cone) | tank–world and tank–tank material pairs on head-on, glancing and sliding hits, plus 60 random coefficient, velocity and inverse-mass cases | 72 contacts; `contactImpulse` matches within 1e-6 |

`ctest` replays each recorded step from its `pre` state (`reference/test_golden.cpp`). Any change
to the reference that departs from the game fails the build. The traces exposed three things the
hand transcription had missed: the 1.5 boost pitch, the 3 s overspeed timer and the damage-order
details in §4.2. They are fixed in the reference.

To extend it, add a scenario to one of the scripts, rerun it to regenerate `golden/`, and rerun
`ctest`.

### 8.2 Runtime checks in PPSSPP (still to do)

These cover what emulating single functions can't: frame timing, the physics solver and input
mapping. Use the PPSSPP debugger (memory view and breakpoints) with a module base of `0x08804000`
(`ppsspp_address` in `bzpsp_constants.json` already includes it).

1. **Frame rate and dt**: read `game +0x108c` (game pointer at `0x08a51d3c`) during a busy match.
   Confirm 1/30, and note any 1/20 frames.
2. **Top speed and reverse**: break at the drive model (`0x088994a0`, `$a0` = tank). Log the
   tank's forward speed (`+0x5b0`) while holding full throttle on flat ground. Expect 80/68/55,
   and 60/51/41.25 in reverse.
3. **Yaw rate**: hold full steer for more than 1 s and log the frame's `at` vector. Expect
   2.1/1.9/1.7 rad/s. This also settles the stick sign (§2).
4. **Ride height**: log `pos.y − ground` at rest. Use it for BZ98R `setAltitude` and to check the
   integrator (the reference's minimal integrator settles at 7.43 m; the PSP solver may differ).
5. **Projectile speed**: log a Vulcan shot's position on two consecutive frames. Expect 30 m per
   update.
6. **Damage**: hit a medium tank (200 hp) with three Fusion shots under 2 s apart. Expect 25, 25,
   then 375 (HP at `vehicle +0x20`).
7. **Match rules**: in a 10-minute DM, tie at time-out. Expect 60 s of overtime (the limit at
   `GameType +0x30/+0x34`).
8. **Collisions**: break at the contact impulse (`0x0893a61c`) and drive into a wall, then into
   another tank. Expect `$f12`/`$f13`/`$f14` (μ, e, c) = 0.252/0.0825/0.5 against the wall and
   0.09/0.0225/0 against the tank (§4.6). Log the tank's `pos` on consecutive frames to see
   the push-out, which is at most 1 m per step.

## 9. Known uncertainties

- **Collision response** is specified in §4.6: the shapes, the contact selection, the material
  coefficients, the impulse (emulated) and the push-out. Not traced: the library's sleep
  handling, the exact build of the two-body `K` matrix (`0x137c40`), and how multiple bodies'
  contacts are ordered within a step. Expect near-identical single contacts, and small drift in
  piles of simultaneous contacts.
- **The 30 fps assumption.** The code is variable-dt, but projectiles and several timers are
  per-update, so behavior at other frame rates differs. Validate step 1 in §8.2.
- **Scripted steering** (input `+0x10c`, a direction at input `+0x170…`) isn't modeled. It
  zeroes throttle and strafe and stops nitro; player and AI driving don't use it.
- **Stick sign convention** and **BZ98R handedness** (§2) need one runtime check each.
- The shipped tables here are from the US disc. The EU disc's tables may differ; run
  `build_port_tables.py` on each.
