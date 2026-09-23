"""Golden traces for the collision impulse: run the physics library's contact impulse (0x13661c).

0x13661c computes the impulse for one contact point in the contact frame (z = normal) from:
the friction coefficient mu (f12), the restitution e (f13), the tangential grip factor c (f14),
the relative contact velocity v (a1), the contact's inverse-mass matrix K (a2, K_zz at +0x28)
and its inverse (a3, applied as RwV3dTransformVector). It writes the impulse to a0.
The caller (0x13af80 in 0x137c40) passes mu = matA.b * matB.b, e = matA.a * matB.a and
c = (matA.c + matB.c) / 2, combined in 0x11b658 (PORT_SPEC.md §4.6).

Usage:
    python contact_traces.py --boot ../../BOOT.BIN --out ../golden/contact_traces.txt

Output: one line per case, "case mu e c | v3 | K9 | Kinv9 | P3".
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pspemu import PspMachine, f32  # noqa: E402

CONTACT_IMPULSE = 0x13661C

# (name, mu, e, c) for the material pairs a tank meets (physics_materials.json)
MATERIAL_PAIRS = [
    ("tank_world", f32(0.3 * 0.84), f32(0.15 * 0.55), 0.5),
    ("tank_tank", f32(0.3 * 0.3), f32(0.15 * 0.15), 0.0),
    ("default_pair", f32(0.84 * 0.84), f32(0.55 * 0.55), 1.0),
]


def inverse3(m: list[list[float]]) -> list[list[float]]:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]


def random_k(rng: random.Random) -> list[list[float]]:
    """A symmetric positive-definite inverse-mass matrix like two bodies' contact K."""
    a = [[rng.uniform(-0.2, 0.2) for _ in range(3)] for _ in range(3)]
    k = [[sum(a[r][t] * a[s][t] for t in range(3)) for s in range(3)] for r in range(3)]
    base = rng.uniform(0.005, 0.03)  # 1/mass for masses 33..200
    for r in range(3):
        k[r][r] += base
    return [[f32(x) for x in row] for row in k]


class EmuContact:
    def __init__(self, boot: Path):
        m = self.m = PspMachine(boot)
        m.install_rw_transform_stubs()
        self.out = m.alloc(0x10)
        self.v = m.alloc(0x10)
        self.k = m.alloc(0x40)
        self.kinv = m.alloc(0x40)

    def run(self, mu: float, e: float, c: float, v: list[float],
            k: list[list[float]], kinv: list[list[float]]) -> list[float]:
        m = self.m
        m.wf(self.v, *v, 0.0)
        for r in range(3):
            m.wf(self.k + 0x10 * r, *k[r], 0.0)
            m.wf(self.kinv + 0x10 * r, *kinv[r], 0.0)
        m.wf(self.kinv + 0x30, 0.0, 0.0, 0.0, 1.0)
        m.wf(self.out, 0.0, 0.0, 0.0, 0.0)
        m.call(CONTACT_IMPULSE, args=(self.out, self.v, self.k, self.kinv), fargs=(mu, e, c))
        return m.rf(self.out, 3)


def cases(rng: random.Random):
    # Named pairs: head-on, glancing and sliding hits.
    for name, mu, e, c in MATERIAL_PAIRS:
        for v in ([0.0, 0.0, -10.0], [8.0, 0.0, -3.0], [30.0, -5.0, -1.0], [2.0, 1.0, -20.0]):
            k = [[0.01, 0.0, 0.0], [0.0, 0.01, 0.0], [0.0, 0.0, 0.01]]
            yield name, mu, e, c, v, k
    for i in range(60):
        mu = f32(rng.choice([0.0, rng.uniform(0.0, 1.2)]))
        e = f32(rng.uniform(0.0, 1.0))
        c = f32(rng.choice([0.0, 0.5, 1.0, rng.uniform(0.0, 1.1)]))
        v = [f32(rng.uniform(-30, 30)), f32(rng.uniform(-30, 30)), f32(-rng.uniform(0.1, 30))]
        yield f"random{i}", mu, e, c, v, random_k(rng)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--boot", type=Path, default=Path(__file__).resolve().parents[2] / "BOOT.BIN")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "golden" / "contact_traces.txt")
    ap.add_argument("--seed", type=int, default=98)
    args = ap.parse_args()

    emu = EmuContact(args.boot)
    rng = random.Random(args.seed)
    lines = ["# contact impulse 0x13661c: case mu e c | v | K (3x3) | Kinv (3x3) | P"]
    for name, mu, e, c, v, k in cases(rng):
        kinv = [[f32(x) for x in row] for row in inverse3(k)]
        p = emu.run(mu, e, c, v, k, kinv)
        flat = lambda xs: " ".join(f"{x:.9g}" for x in xs)  # noqa: E731
        lines.append(f"{name} {mu:.9g} {e:.9g} {c:.9g} | {flat(v)} | "
                     f"{flat(sum(k, []))} | {flat(sum(kinv, []))} | {flat(p)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines) - 1} contacts to {args.out}")


if __name__ == "__main__":
    main()
