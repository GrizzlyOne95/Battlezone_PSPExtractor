"""Golden traces for the hover tank: run the game's own HoverTank drive (0x954a0) per tick.

Each tick records the complete tank state before and after one call of the drive model
(which also runs the hover model 0x94da0 and applies its impulse). A port reproduces the PSP
exactly if, given each `pre` state and the tick's input, it produces the `post` state. The
body is integrated between ticks with the same minimal integrator as the C++ reference
(gravity 30 m/s^2, explicit Euler); that part is not the PSP physics library, which is fine
because every tick is checked from its recorded `pre` state.

Usage:
    python tank_traces.py --boot ../../BOOT.BIN --out ../golden/tank_traces.txt

Output format (plain text so the C++ test needs no JSON parser): one line per tick,
"scenario tick dt steer throttle strafe boost ground" followed by the pre and post state
vectors (see STATE_FIELDS).
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pspemu import Body, PspMachine, f32  # noqa: E402

HOVERTANK_VTABLE = 0x257974
DRIVE = 0x0954A0
MOTION_TABLE = 0x24DF58      # 20 rows x {small, medium, large}
HOVER_HEIGHT_CACHED = 0x2AEA54  # static flag + value used by the hover model
GAME_TYPE_PTR = 0x24B770

# Shipped BZ_TANK_MOTION.CSV values by memory row (see PORT_SPEC.md / PSP_GAME_CODE.md §6).
MOTION_ROWS = {
    0: (21, 19, 17), 1: (80, 68, 55), 2: (100, 90, 70), 3: (55, 55, 50), 4: (95, 90, 70),
    5: (105, 200, 325), 6: (6, 10, 15), 7: (50, 75, 100), 8: (300, 300, 300), 9: (0, 0, 0),
    10: (1, 1, 1), 11: (50, 100, 150), 12: (10, 10, 10), 13: (0.5, 0.5, 0.5),
    14: (1000, 1000, 1000), 15: (100, 100, 100), 16: (60, 70, 80), 17: (25, 35, 50),
    18: (1, 1, 1), 19: (0.6, 0.8, 1.0),
}

# Tank object offsets (HoverTank, see bzpsp_ref.h)
T_STATE, T_SIZE, T_MODEL = 0x04, 0x08, 0x0C
T_FRAME = 0xC0            # right(+0x00) up(+0x10) at(+0x20) pos(+0x30), 16-byte rows
T_INPUT = 0x118
T_AIRBORNE, T_TOOSTEEP, T_LASTFWD, T_MAXFWD = 0x5A8, 0x5AC, 0x5B0, 0x5B4
T_STEERDIR, T_STEERHOLD, T_BODY = 0x5B8, 0x5BC, 0x5C4
T_NITRO_MULT, T_NITRO_METER, T_NITRO_ON, T_NITRO_DUR = 0x824, 0x828, 0x82C, 0x834
T_OVERSPEED, T_SLOWED, T_SLOWFACTOR = 0x838, 0x840, 0x844
T_PROBES, T_PROBE_IDX = 0x850, 0x890   # 4 x {end xyz, t} stride 16
T_FORCED, T_AUDIO = 0x944, 0x970

STATE_FIELDS = (
    "right3 up3 at3 pos3 vel3 omega3 probeT4 probeIndex airborne tooSteep steerHold steerDir "
    "maxFwdSpeed nitroMeter nitroMult nitroDuration nitroEngaged nitroForced "
    "nitroOverspeedTimer lastFwdSpeed slowed slowFactor dead"
)


@dataclass
class Ground:
    """Plane n.p = d (n normalized). kind 0 = flat y=0; kind 1 = tilted about z."""
    kind: int = 0
    slope_deg: float = 0.0

    def normal(self) -> tuple[float, float, float]:
        a = math.radians(self.slope_deg)
        return (-math.sin(a), math.cos(a), 0.0)

    def ray(self, a: list[float], b: list[float]) -> float:
        n = self.normal()
        da = sum(n[i] * a[i] for i in range(3))
        db = sum(n[i] * b[i] for i in range(3))
        if da < 0 or db > 0 or da == db:
            return -1.0
        return da / (da - db)

    def code(self) -> str:
        return f"{self.kind}:{self.slope_deg:g}"


class EmuTank:
    def __init__(self, boot: Path, size: int, ground: Ground):
        m = self.m = PspMachine(boot)
        m.install_body_stubs()
        m.install_rw_transform_stubs()
        self.ground = ground
        self.hits: list[float] = []
        self.damage: list[float] = []

        for row, vals in MOTION_ROWS.items():
            m.wf(MOTION_TABLE + row * 12, *[float(v) for v in vals])
        m.wi(HOVER_HEIGHT_CACHED, 0)

        game_type = m.alloc(0x400)
        m.wi(GAME_TYPE_PTR, game_type)

        def raycast() -> None:
            seg, result = m.gpr(5), m.gpr(7)
            start, end = m.rf(seg, 3), m.rf(seg + 0xC, 3)
            t = ground.ray(start, end)
            if 0 <= t < m.rf(result)[0]:
                m.wf(result, f32(t))

        def nop() -> None:
            m.ret(v0=0)

        def take_damage() -> None:
            self.damage.append(m.freg(12))
            m.ret(v0=0)

        m.hook(0x1DF614, raycast)
        m.hook(0x090018, nop)   # nitro audio loop
        m.hook(0x04C748, nop)   # HUD nitro meter
        m.hook(0x0943B8, take_damage)

        # Model object whose vtable +0x2c is poked when nitro stops (effect toggle).
        model = m.alloc(0x100)
        model_vt = m.alloc(0x40)
        m.wi(model + 0xB4, model_vt)
        m.wh(model_vt + 0x28, 0)
        m.wi(model_vt + 0x2C, m.new_stub(nop))

        self.body_addr = m.alloc(0x400)
        m.bodies[self.body_addr] = Body()
        self.input = m.alloc(0x400)
        t = self.tank = m.alloc(0x1000)
        m.wi(t, HOVERTANK_VTABLE)
        m.wi(t + T_SIZE, size)
        m.wi(t + T_MODEL, model)
        m.wi(t + T_INPUT, self.input)
        m.wi(t + T_BODY, self.body_addr)
        m.wi(t + T_AUDIO, -1)
        self.set_frame([1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 6, 0])
        m.wf(t + T_MAXFWD, MOTION_ROWS[1][size])
        m.wf(t + T_NITRO_METER, 1.0)
        m.wf(t + T_NITRO_MULT, 1.0)
        m.wf(t + T_NITRO_DUR, 1.0)
        m.wf(t + T_SLOWFACTOR, 1.0)

    @property
    def body(self) -> Body:
        return self.m.bodies[self.body_addr]

    def set_frame(self, right, up, at, pos) -> None:
        for k, row in enumerate((right, up, at, pos)):
            self.m.wf(self.tank + T_FRAME + 0x10 * k, *[f32(x) for x in row], 0.0)

    def frame(self) -> list[list[float]]:
        return [self.m.rf(self.tank + T_FRAME + 0x10 * k, 3) for k in range(4)]

    def state(self) -> list[float]:
        m, t = self.m, self.tank
        right, up, at, pos = self.frame()
        probes = [m.rf(t + T_PROBES + 16 * k + 12)[0] for k in range(4)]
        return [
            *right, *up, *at, *pos, *self.body.vel, *self.body.omega, *probes,
            m.rh(t + T_PROBE_IDX), m.ri(t + T_AIRBORNE), m.ri(t + T_TOOSTEEP),
            m.rf(t + T_STEERHOLD)[0], m.rh(t + T_STEERDIR), m.rf(t + T_MAXFWD)[0],
            m.rf(t + T_NITRO_METER)[0], m.rf(t + T_NITRO_MULT)[0], m.rf(t + T_NITRO_DUR)[0],
            m.ri(t + T_NITRO_ON), m.ri(t + T_FORCED), m.rf(t + T_OVERSPEED)[0],
            m.rf(t + T_LASTFWD)[0], m.ri(t + T_SLOWED), m.rf(t + T_SLOWFACTOR)[0],
            1 if m.ri(t + T_STATE) == 2 else 0,
        ]

    def step(self, dt: float, steer: float, throttle: float, strafe: float, boost: bool) -> None:
        m = self.m
        m.wf(self.input + 0x284, steer, throttle, strafe)
        m.wi(self.input + 0x294, 1 if boost else 0)
        m.call(DRIVE, (self.tank,), (dt,))

    def integrate(self, dt: float) -> None:
        """Same as bzpsp::integrateBody (not the PSP solver)."""
        b = self.body
        b.vel = [b.vel[0], f32(b.vel[1] - 30.0 * dt), b.vel[2]]
        right, up, at, pos = self.frame()
        pos = [pos[i] + b.vel[i] * dt for i in range(3)]
        w = [x * dt for x in b.omega]
        ang = math.sqrt(sum(x * x for x in w))
        if ang > 0:
            a = [x / ang for x in w]
            c, s = math.cos(ang), math.sin(ang)

            def rot(v):
                cr = [a[1] * v[2] - a[2] * v[1], a[2] * v[0] - a[0] * v[2], a[0] * v[1] - a[1] * v[0]]
                d = sum(a[i] * v[i] for i in range(3))
                return [v[i] * c + cr[i] * s + a[i] * d * (1 - c) for i in range(3)]

            right, up, at = rot(right), rot(up), rot(at)
            n = math.sqrt(sum(x * x for x in at))
            at = [x / n for x in at]
            right = [up[1] * at[2] - up[2] * at[1], up[2] * at[0] - up[0] * at[2], up[0] * at[1] - up[1] * at[0]]
            n = math.sqrt(sum(x * x for x in right))
            right = [x / n for x in right]
            up = [at[1] * right[2] - at[2] * right[1], at[2] * right[0] - at[0] * right[2], at[0] * right[1] - at[1] * right[0]]
        self.set_frame(right, up, at, pos)


# Scenarios: (name, size, ground, setup, [(seconds, steer, throttle, strafe, boost)])
def scenarios():
    def nitro(tank: EmuTank) -> None:
        tank.m.wi(tank.tank + T_NITRO_ON, 1)
        tank.m.wf(tank.tank + T_NITRO_MULT, 1.5)
        tank.m.wf(tank.tank + T_NITRO_DUR, 3.0)

    def slowed(tank: EmuTank) -> None:
        tank.m.wi(tank.tank + T_SLOWED, 1)
        tank.m.wf(tank.tank + T_SLOWFACTOR, 0.5)

    def fall(tank: EmuTank) -> None:
        tank.set_frame([1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 40, 0])

    def tilted(tank: EmuTank) -> None:
        a = math.radians(30)
        tank.set_frame([1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)], [0, 8, 0])

    none = lambda _t: None  # noqa: E731
    flat, slope = Ground(0, 0.0), Ground(1, 20.0)
    return [
        ("idle_medium", 1, flat, none, [(4.0, 0, 0, 0, False)]),
        ("accel_small", 0, flat, none, [(10.0, 0, 1, 0, False), (3.0, 0, 0, 0, False)]),
        ("accel_large_reverse", 2, flat, none, [(6.0, 0, -1, 0, False)]),
        ("turn_hold_medium", 1, flat, none, [(0.3, 0.5, 1, 0, False), (3.0, 1, 1, 0, False), (2.0, -1, 0.5, 0, False)]),
        ("strafe_diag_small", 0, flat, none, [(4.0, 0, 1, 1, False), (2.0, 0, 0, -1, False)]),
        ("nitro_medium", 1, flat, nitro, [(1.0, 0, 1, 0, False), (4.0, 0, 1, 0, True), (4.0, 0, 1, 0, False)]),
        ("slowed_large", 2, flat, slowed, [(4.0, 0.6, 1, 0.5, False)]),
        ("drop_from_40m", 1, flat, fall, [(4.0, 0, 0.5, 0, False)]),
        ("tilted_recover", 1, flat, tilted, [(3.0, 0, 0, 0, False)]),
        ("climb_slope_20deg", 1, slope, none, [(6.0, 0, 1, 0, False)]),
    ]


def run(boot: Path, out: Path, dt: float) -> int:
    lines = [
        "# Battlezone PSP golden tank traces from HoverTank::drive 0x954a0 run under emulation.",
        f"# state fields: {STATE_FIELDS}",
        "# line: scenario tick dt steer throttle strafe boost ground | pre... | post...",
    ]
    ticks = 0
    for name, size, ground, setup, segments in scenarios():
        tank = EmuTank(boot, size, ground)
        setup(tank)
        tick = 0
        for seconds, steer, throttle, strafe, boost in segments:
            for _ in range(int(round(seconds / dt))):
                pre = tank.state()
                tank.step(dt, steer, throttle, strafe, boost)
                post = tank.state()
                head = f"{name} {size} {tick} {dt!r} {steer} {throttle} {strafe} {int(boost)} {ground.code()}"
                lines.append(" | ".join((head, " ".join(format(float(x), ".9g") for x in pre), " ".join(format(float(x), ".9g") for x in post))))
                tank.integrate(dt)
                tick += 1
                ticks += 1
        print(f"{name}: {tick} ticks, final speed {math.sqrt(sum(v * v for v in tank.body.vel)):.3f} m/s, "
              f"pos {[round(x, 2) for x in tank.frame()[3]]}, damage calls {tank.damage}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {ticks} ticks to {out}")
    return 0


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--boot", type=Path, default=here.parents[1] / "BOOT.BIN")
    ap.add_argument("--out", type=Path, default=here.parent / "golden" / "tank_traces.txt")
    ap.add_argument("--dt", type=float, default=1.0 / 30.0)
    args = ap.parse_args()
    return run(args.boot, args.out, args.dt)


if __name__ == "__main__":
    raise SystemExit(main())
