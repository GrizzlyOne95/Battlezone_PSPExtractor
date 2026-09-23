"""Golden traces for vehicle damage: run the game's Vehicle::takeDamage (0x99be8) per hit.

0x99be8 applies, in order: the 3-hit combo multiplier (0x98808), the Germany Armor scale, the
Armor pickup shield, the frozen shatter rule, the regen timer reset and the HP subtraction
with death. Presentation calls (tint, hit flash, kill messages, audio, HUD) are stubbed.

Usage:
    python damage_traces.py --boot ../../BOOT.BIN --out ../golden/damage_traces.txt

Output: one line per hit, "case hit time damage bulletId | pre... | post..." where the state
vector is STATE_FIELDS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pspemu import PspMachine  # noqa: E402

TAKE_DAMAGE = 0x099BE8
GAME_TYPE_PTR = 0x24B770
GT_CLOCK = 0x2C

# Vehicle offsets
V_STATE, V_MODEL, V_HP, V_HPMAX, V_SINCE, V_INVULN = 0x04, 0x0C, 0x20, 0x24, 0x28, 0x2C
V_COMBO_T, V_COMBO_N = 0x30, 0x48     # 2 x 3 floats, 2 x u16
V_SHIELD_ON, V_SHIELD_HP, V_ARMOR_ON, V_ARMOR_SCALE = 0x60, 0x64, 0x74, 0x78
V_FROZEN = 0x150

STATE_FIELDS = "hp hpMax sinceDamage invulnerable shieldActive shieldHp armorActive armorScale frozen dead comboT6 comboN2"


class EmuVehicle:
    def __init__(self, boot: Path):
        m = self.m = PspMachine(boot)
        self.deaths = 0

        def nop() -> None:
            m.ret(v0=0)

        def on_death() -> None:
            self.deaths += 1
            m.ret(v0=0)

        for addr in (0x098EA0, 0x09861C, 0x07D814, 0x07AFD8, 0x099AD4, 0x099B6C, 0x04C750):
            m.hook(addr, nop)

        self.gt = m.alloc(0x400)
        m.wi(GAME_TYPE_PTR, self.gt)
        m.wi(self.gt + 0x7C, m.alloc(0x40))  # "local player" that never matches the shooter

        vt = m.alloc(0x200)
        m.wh(vt + 0xB8, 0)
        m.wi(vt + 0xBC, m.new_stub(on_death))
        self.v = m.alloc(0x400)
        m.wi(self.v, vt)
        m.wi(self.v + V_MODEL, m.alloc(0x100))
        self.proj = m.alloc(0x40)
        m.wi(self.proj + 0x0C, m.alloc(0x40))  # shooter

    def setup(self, hp: float, shield: float = 0.0, armor: float = 0.0, frozen: bool = False,
              invulnerable: bool = False) -> None:
        m, v = self.m, self.v
        m.wf(v + V_HP, hp, hp)
        m.wf(v + V_SINCE, 5.0)
        m.wi(v + V_INVULN, 1 if invulnerable else 0)
        m.wi(v + V_SHIELD_ON, 1 if shield > 0 else 0)
        m.wf(v + V_SHIELD_HP, shield)
        m.wi(v + V_ARMOR_ON, 1 if armor > 0 else 0)
        m.wf(v + V_ARMOR_SCALE, armor if armor > 0 else 1.0)
        m.wi(v + V_FROZEN, 1 if frozen else 0)

    def state(self) -> list[float]:
        m, v = self.m, self.v
        return [
            m.rf(v + V_HP)[0], m.rf(v + V_HPMAX)[0], m.rf(v + V_SINCE)[0], m.ri(v + V_INVULN),
            m.ri(v + V_SHIELD_ON), m.rf(v + V_SHIELD_HP)[0], m.ri(v + V_ARMOR_ON),
            m.rf(v + V_ARMOR_SCALE)[0], m.ri(v + V_FROZEN), 1 if m.ri(v + V_STATE) == 2 else 0,
            *m.rf(v + V_COMBO_T, 6), m.rh(v + V_COMBO_N), m.rh(v + V_COMBO_N + 2),
        ]

    def hit(self, now: float, damage: float, bullet: int) -> None:
        m = self.m
        m.wf(self.gt + GT_CLOCK, now)
        m.wi(self.proj + 4, bullet)
        proj = self.proj if bullet >= 0 else 0
        m.call(TAKE_DAMAGE, (self.v, 0, proj, 0), (damage,))


def cases():
    fusion, swarm, vulcan = 8, 14, 5
    return [
        ("plain_hits", dict(hp=200), [(0.0, 12, vulcan), (0.1, 12, vulcan), (0.2, 50, -1)]),
        ("fusion_combo", dict(hp=325), [(0.0, 25, fusion), (0.5, 25, fusion), (1.0, 25, fusion), (1.2, 25, fusion)]),
        ("fusion_slow", dict(hp=325), [(0.0, 25, fusion), (3.0, 25, fusion), (3.5, 25, fusion), (4.0, 25, fusion)]),
        ("swarm_combo", dict(hp=325), [(0.0, 10, swarm), (1.9, 10, swarm), (3.8, 10, swarm)]),
        ("mixed_ids", dict(hp=325), [(0.0, 25, fusion), (0.2, 10, swarm), (0.4, 25, fusion), (0.6, 10, swarm), (0.8, 25, fusion)]),
        ("shield_absorb", dict(hp=200, shield=150), [(0.0, 100, vulcan), (0.1, 30, vulcan), (0.2, 40, vulcan), (0.3, 40, vulcan)]),
        ("armor_scale", dict(hp=200, armor=0.8), [(0.0, 100, vulcan), (0.1, 100, vulcan), (0.2, 100, vulcan)]),
        ("armor_and_shield", dict(hp=105, shield=150, armor=0.8), [(0.0, 170, vulcan), (0.1, 60, vulcan)]),
        ("frozen_shatter", dict(hp=325, frozen=True), [(0.0, 1, vulcan)]),
        ("frozen_zero_damage", dict(hp=325, frozen=True), [(0.0, 0, vulcan)]),
        ("invulnerable", dict(hp=105, invulnerable=True), [(0.0, 500, vulcan)]),
        ("overkill", dict(hp=105), [(0.0, 400, 18), (0.5, 10, vulcan)]),
    ]


def run(boot: Path, out: Path) -> int:
    lines = [
        "# Battlezone PSP golden damage traces from Vehicle::takeDamage 0x99be8 run under emulation.",
        f"# state fields: {STATE_FIELDS}",
        "# line: case hit time damage bulletId | pre... | post...",
    ]
    for name, setup, hits in cases():
        veh = EmuVehicle(boot)
        veh.setup(**setup)
        for i, (now, dmg, bullet) in enumerate(hits):
            pre = veh.state()
            veh.hit(now, dmg, bullet)
            post = veh.state()
            head = f"{name} {i} {now:g} {dmg:g} {bullet}"
            lines.append(" | ".join((head, " ".join(format(float(x), ".9g") for x in pre),
                                     " ".join(format(float(x), ".9g") for x in post))))
        print(f"{name}: hp {veh.state()[0]:g}, deaths {veh.deaths}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--boot", type=Path, default=here.parents[1] / "BOOT.BIN")
    ap.add_argument("--out", type=Path, default=here.parent / "golden" / "damage_traces.txt")
    args = ap.parse_args()
    return run(args.boot, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
