# Battlezone PSP: tank AI

This is how computer-controlled tanks think, read from the decompiled `BOOT.BIN`. Addresses are
static VAs with load base 0. Evidence is **code** throughout (read from decompiled code, not yet
emulated) unless marked otherwise. See [`PORT_SPEC.md`](PORT_SPEC.md) for units and axes.

## 1. Architecture

- **One AI controller per computer tank** (`0xe2c` bytes, up to 8), owned by the AI manager at
  `GameType + 0xfc40` (`0x8144c` constructor, `0x81538` setup). In single player every non-human
  tank gets one. In multiplayer only the "AI slots" do (`0x57a48`/`0x57978`).
- **The AI drives a virtual controller**, not the tank. It writes the same controller record a
  human pad fills (`tank + 0x98`, controller type 3): stick X/Y floats and button bits. The tank
  then reads it through the normal input mapping (§8). A port that feeds AI decisions through
  its own input layer gets identical behaviour for free.
- **Scheduler** (`0x81c00`): each controller is split into 8 *tasks*, each with its own think
  interval. They sit in a time-ordered queue (240 entries, `+0x7184…`). Every frame the manager
  runs the tasks that are due, within a small time budget. A task's next run is `now + interval`.
  The clock is the match clock (`GameType + 0x2c`).

| Task (record offset) | Interval | Think function | Job |
|---|---:|---|---|
| `+0x000` | 0.1 s | `0x5558` | Path planner: A* over NavBeacons to the current goal (§5) |
| `+0x0c8` | 0.06 s | `0x3318` | Next waypoint: direction to the next beacon (or the goal) |
| `+0x124` | 1.0 s | `0x24f4` | Weapon choice and ram decision (§6) |
| `+0x1b0` | 0.06 s | `0x27fc` | Steering: turn toward the waypoint or the (led) target (§7) |
| `+0x20c` | 0.25 s | `0x5220` | Trigger: fire and team-special decisions (§6) |
| `+0x268` | 0.06 s | `0x5a6c` | Throttle and strafe toward the waypoint (§7) |
| `+0x2c4` | 0.5 s | `0x5d9c` | Perception: which enemies it can see or remembers (§3) |
| `+0x320` | 0.5 s | `0x4dc0` | Brain: pick the goal (enemy, objective, pickup, roam) (§4) |

## 2. Personality and difficulty

Every controller has three skill bytes, set at spawn (`0x18ec`):

```
skill[k] = table_k[tank.country] + difficulty      (k = 1, 2, 3)
```

Country order: USA, China, Germany, Russia, Italy, Japan, Canada, England.

| Table (`.data`) | USA | CHN | GER | RUS | ITA | JPN | CAN | ENG | Used for |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| skill 1 `0x231050` | 3 | 3 | 3 | 2 | 2 | 3 | 3 | 2 | fire probability `0.5 + 0.07·s1` |
| skill 2 `0x231058` | 3 | 2 | 3 | 3 | 3 | 3 | 2 | 3 | max steer `0.7 + 0.05·s2` |
| skill 3 `0x231060` | 2 | 3 | 2 | 3 | 3 | 2 | 3 | 3 | vision cone half-angle `32° + 2°·s3` |

`difficulty` is `GameType + 0x179c8` (`0x43e4c`), set from the match config. It's 0–4, from the
single-player difficulty screen or the multiplayer AI setting.

## 3. Perception (`0x5d9c`, every 0.5 s)

For every other tank that is not a teammate:

- It is **seen** if it's inside the view cone (`dot(forward_flat, dir) ≥ cos(32° + 2°·s3)`) and
  closer than **350 m**, or **75 m** if that tank is invisible (England special). The AI records
  the time, the target's position and its velocity.
- Otherwise it is **sensed** if it's within **50 m** (any direction), or it hit this tank in the
  last **5 s** (`tank + 0x950` time, `+0x954` attacker).
- Otherwise it's **forgotten** 5 s after it was last seen.

## 4. Choosing a goal (`0x4dc0` brain, every 0.5 s)

### 4.1 Target score (`0x360c`)

For each remembered enemy that is alive:

```
distScore   = clamp((1 − dist/500)·100, 0, 100)
damageScore = 100 − 100·hp/hpMax                    (wounded enemies are preferred)
threat      = 100 if that enemy hit me within 5 s,
              else 100 if it carries the objective (GameType +0xcc) and ... (+0xd0 check),
              else 0
score       = (distScore + damageScore + 2·threat) / 4
```

The best score becomes the current target (`+0x81c`), and `desire = best/100` (0–1).

### 4.2 Pickup desire

- **Health dispensers** (manager list `+0x7d0c`) and the 8 pickup slots (`GameType + 0xf1c0`):
  for the nearest one, if `hp/hpMax < 1`:
  `desire = 0.8·(1 − hp/hpMax) + 0.2·(1 − min(d²/10⁶, 1))`.
- **Other pickups** (list `+0x7d40`): `desire = 0.3·(1 − min(d²/10⁶, 1))`, plus 0.5 when this
  tank is `GameType + 0xd0` (the objective holder).
- The larger of the two is the pickup desire, with its position.

### 4.3 Mode behaviour

Then the mode's behaviour runs (`GameType + 4` = mode). It usually overrides the pickup when the
target score is high enough.

| Mode | Function | Behaviour |
|---|---|---|
| DM | `0x3bf8` | Attack the target if `desire ≥ 0.75`; otherwise roam |
| TDM | `0x3c88` | By role: attack if `desire ≥ 0.8` (role 1), `≥ 0.6` (role 2), `≥ 0.4` (role 3); role 4 escorts its leader |
| CTF | `0x40b8` | By role, see below |
| FAH | `0x3e6c` | If I have the ball: flee and roam; else chase the carrier (if `desire ≥ 0.5`), or go to the ball |
| HZ | `0x45a4` | Go to the nearest pad not held by my team; defenders sit on held pads |
| KO | `0x4a28` | Attack the enemy core (offense), guard or recharge my core (defense) |

If the mode code didn't claim the goal this tick (flag `+0x6c` unset) and the pickup desire is
non-zero, the tank goes for the pickup.

**Roaming** (`0x21e4`, `0x1ff8`): pick a random NavBeacon at least 500 m (Manhattan, x/z)
away. Keep it until within 100 m, then pick again.

**Engaging a target** (`0x1cf4`): if the target is within 125 m and in line of sight
(`game__03a4ec` ray test), drive straight at it. Otherwise path to it through the beacons.

**Escort / follow** (`0x1dfc`): hold a point 50 m from the leader, on the line from the leader
to me.

**Guard a point** (`0x1f40`): within 70 m, go straight to it; farther away, path.

### 4.4 Roles (team modes)

- **Assignment** (`0x14d0`): by the tank's index among its team's AI tanks (humans skipped):
  1st → role 2, 2nd → role 1, 3rd → role 3.
- **TDM with a human on the team:** role 4 (follow the human).
- **Team commands** (`0x81db0` → `0x1838`, command 0–3) change it: 0 → role 1, 1 → role 3,
  2 → back to the assigned role, 3 → role 4 (follow the commander). The speech lines are
  `ai_offense`, `ai_defense`, `ai_roam` and `ai_cover`. The number-to-line pairing is inferred,
  not traced.

| Role | CTF (`0x40b8`) | HZ (`0x45a4`) | KO (`0x4a28`) |
|---|---|---|---|
| 1 | Offense: take the enemy flag and return it; engage enemies under 500 m | Capture the nearest non-held pad | Attack the enemy core while it has health; else fight |
| 2 | Mixed: grab the flag when it's free; otherwise engage | Capture if `desire < 0.75`, else attack | Recharge my core if it's below 99; otherwise as role 1 |
| 3 | Defense: stay by the home flag (guard radius 70 m), chase the carrier | Guard held pads | Guard or recharge my core |
| 4 | Follow the leader | Follow the leader | Follow the leader |

(The CTF/HZ/KO columns summarise the branches in those functions, which also check whether
the flag is home or carried by a teammate, and where the nearest enemy is.)

## 5. Navigation: NavBeacons and A*

- **Beacons** are level data: up to 400 records of `0xc0` bytes at `GameType + 0x187f0`.

| Offset | Field |
|---|---|
| `+0x00` | in use |
| `+0x10` | RwMatrix scaled by the radius |
| `+0x40` | position |
| `+0x54` | radius, int metres, 5–150 |
| `+0x58` | 6 neighbour ids (−1 = none) |
| `+0x70` | 6 per-link speed factors |
| `+0x88` | 6 per-link values |
| `+0xa0` | 6 link lengths (computed at load, `0x43784`) |
| `+0xb8` | linked object (door or jump pad) |

- A jump pad's beacon is linked to the nearest beacon within 1 m of the pad
  (`0x43784`, "Using Alternate Beacon").
- **At a beacon** (`0x2b80`): horizontal distance < radius and |Δy| < 10 m.
- **A\*** (`aiPathFinder.cpp`, `0x69c`/`0xfa8`/`0xd88`): integer costs.
  - **g:** 50 per hop.
  - **Penalties** (`0x87c`, via the linked object):
    - +1050 instead of 50 through a closed door
    - +200 for another gated object
    - +5000 for a team door this tank can't open
    - +10000 for a locked door
  - **h:** straight-line distance.
- The planner (`0x5558`, every 0.1 s) stores the node list. The current node is the first one
  the tank isn't already inside. Nodes visited are time-stamped (`+0x1d0[400]`) so a detour
  (`0x1b9c`) picks the least recently visited neighbour.

## 6. Weapons and firing

**Weapon choice** (`0x24f4`, every 1 s), with `dist` = distance to the target and `speed` = my
speed:

- **Usable side weapon:** its energy cost is covered, and either the target is in its lock range
  or the weapon doesn't need a lock (weapon flags `+0x1a4`, `+0x1b0`).
- **Ram mode:** when `dist < 15`, `speed > 10` and the aim error is below 0.2, the tank rams:
  `+0x828 = 1`, main gun selected.
- **Otherwise:**
  - both side weapons usable → slot 1 or 2 (random when both are ready)
  - one usable → that one
  - none → the main gun (slot 0)

**Trigger** (`0x5220`, every 0.25 s):

- It pulls the trigger with probability `0.5 + 0.07·s1` (always in ram mode), and only with
  line of sight to the target (aimed 5 m above a tank's origin).
- **Aim tolerance:** fire when the aim error (`|dot(right, dirToTarget)|`, `+0x820`) is below
  **0.25** for the main gun, or **0.1** for side weapons.
- **Team special** (charged to 1.0), by special type (`tank + 0x238`):

| Type | Use it when |
|---|---|
| 0, 1, 4, 7 | the aim error is below 0.2 |
| 2 | the target is inside the special's range (`tank + 0x594`) |
| 3, 6 | right away |
| 5 | the target has more than twice my health and I'm not carrying the objective |

- The buttons it presses map to Cross, Square and Circle (weapon slots 0/1/2) and Triangle
  (special), §8.

## 7. Driving

- **Steering** (`0x27fc`, every 0.06 s):
  - **Aim direction:**
    - when a target is within lock range, the lead point (`0x0f40c`)
    - else the target itself
    - else the next waypoint
  - **Steer:** `stickX = clamp(−10·dot(right, dir), ±(0.7 + 0.05·s2))`. The aim error
    `|dot(right, dir)|` is kept for the trigger.
- **Throttle and strafe** (`0x5a6c`, every 0.06 s):
  - `dir` is the flattened direction to the waypoint.
  - **Scale:** the minimum of
    - the link speed factor
    - `d²/400` inside 20 m of a final goal
    - `d²/r²` inside the current beacon's radius
  - The scale is never below 0.1.
  - `stickY = dot(at, dir)·scale` (analog throttle).
  - `lateral = dot(right, dir)·scale`: if it's above 0.2, press L (strafe left); below −0.2,
    press R.

## 8. Controls (shared by humans and AI)

This is read from `0x75840` (pad read), `0x50e20` (tank input), `0x765e8`/`0x76290` (axis), and
the binding tables at `0x24d6b0…0x24d83c`. It's the same path for the human pad and the AI's
virtual pad.

- **Stick:** `x = (raw/255 − 0.5)·2`, `y = −(raw/255 − 0.5)·2`.
- **Response curve:** `out = x·(|x| − 0.15)/(1 − 0.15)`, and 0 inside the 0.15 dead zone. It's
  quadratic near the centre: half deflection gives 0.21. A stick used as a button needs
  |x| > 0.7.
- **Steering sign:** `steer = −stickX`, so **positive steer turns left**. That matches local
  +X being the tank's left (`PORT_SPEC.md` §2). Strafe is positive to the left too.
- **Internal button bits:** 1 up, 2 down, 4 left, 8 right, 0x10 Circle, 0x20 Cross,
  0x40 Triangle, 0x80 Square, 0x100 L, 0x200 R, 0x400 Select, 0x800 Start.

| Scheme (`ctrl + 0x28`) | Steer | Throttle | Strafe |
|---|---|---|---|
| 0 (default) | stick X | stick Y | L / R |
| 1 | d-pad left / right | d-pad up / down | L / R |
| 2 | L / R | stick Y | stick X |

- **Nitro:** hold **L and R together** (`0x50e20`: boost = L && R).
- **Fire:** Cross = main gun, Square = weapon 2, Circle = weapon 3, Triangle = team special.
  The AI presses the same bits.

## 9. What a port needs from the level

- NavBeacons (positions, radii, links, link factors), doors and jump pads linked to beacons.
- Spawn points, pickup slots, dispensers, objective entities (flags, ball, pads, cores).

These come from the `.LVL` files. See `LEVEL_FORMAT.md` for how the loader reads them.

## 10. Not yet covered

- **The exact CTF, HZ and KO branch tables** are summarised above. Every branch is in the listed
  functions if a port needs them verbatim.
- **Weapon flag meanings:** `+0x1a4` and `+0x1b0` on the weapon object.
- **Lead point:** `0x0f40c` computes the lead from projectile speed.
- **Mid-level beacon changes:** doors that open or close during a match change the A\*
  penalties dynamically.
- **None of this is emulated yet.** The next step is golden traces for target scoring, the
  steering and throttle tasks, and A\* (§8 of `PORT_SPEC.md` describes the harness).
