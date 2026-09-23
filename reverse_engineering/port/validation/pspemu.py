"""Run Battlezone PSP game functions from BOOT.BIN under a CPU emulator.

This is the ground truth for the port: instead of trusting a hand transcription, the real
MIPS code of the game (for example HoverTank's drive model at 0x954a0) is executed with
Unicorn, and its outputs are recorded as golden traces for the reference implementation.

What runs natively: every game function reachable from the entry point, including the
soft-float double helpers. What is replaced (and why):

- VFPU instructions (the PSP vector unit; Unicorn has no Allegrex support) are interpreted
  here, following PPSSPP's register layout and semantics (Core/MIPS/MIPSVFPUUtils.cpp,
  InterpreterVFPU.cpp). Only the handful of opcodes the gameplay helpers use are supported;
  anything else raises.
- Rigid body accessors (0xe0f84 get v, 0xe1080 get w, 0xdfa68 set v, 0xdfb44 set w,
  0xe11bc impulse) are replaced by the body model the physics library implements for a
  simple body (0x119060 / 0x13f758): v += J * (1/mass), L += tau, w = invInertia * L.
- RenderWare point/vector transforms (0x1b5d6c / 0x1b5df4) dispatch through a runtime
  function table; they are replaced by RwV3dTransformPoint(s)/Vector(s) semantics.
- The world raycast (0x1df614) is replaced by an analytic ground supplied by the caller.
- Audio/HUD/effect side calls are stubbed out.

Needs: `pip install unicorn` (2.x). The PRX is relocated at base 0 with the extractor.
"""

from __future__ import annotations

import math
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from unicorn import UC_ARCH_MIPS, UC_HOOK_CODE, UC_MODE_32, UC_MODE_LITTLE_ENDIAN, Uc, UcError
from unicorn.mips_const import (
    UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_F0,
    UC_MIPS_REG_F12, UC_MIPS_REG_F13, UC_MIPS_REG_F14, UC_MIPS_REG_PC, UC_MIPS_REG_RA,
    UC_MIPS_REG_SP, UC_MIPS_REG_V0, UC_MIPS_REG_V1, UC_MIPS_REG_ZERO,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

IMAGE_SIZE = 0x400000
HEAP_BASE, HEAP_SIZE = 0x01000000, 0x00100000
STACK_BASE, STACK_SIZE = 0x01800000, 0x00100000
STUB_BASE, STUB_SIZE = 0x02000000, 0x00010000
RET_MAGIC = STUB_BASE  # emulation stops when control returns here
VFPU_HELPERS = (0x222F30, 0x223EC0)  # dot/normalize/transform/sin/sqrt helpers (only VFPU users)


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def sext16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def relocated_image(boot_bin: Path) -> tuple[bytes, list[tuple[int, int, int, int]]]:
    """Return the relocated ELF bytes and its PT_LOAD list (offset, vaddr, filesz, memsz)."""
    from extractors.extract_psp_code import Prx, write_relocated_elf

    prx = Prx(boot_bin.read_bytes())
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "boot.elf"
        write_relocated_elf(prx, out)
        elf = out.read_bytes()
    phoff, = struct.unpack_from("<I", elf, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    loads = []
    for i in range(phnum):
        p_type, off, vaddr, _paddr, filesz, memsz = struct.unpack_from("<6I", elf, phoff + i * phentsize)
        if p_type == 1:
            loads.append((off, vaddr, filesz, memsz))
    return elf, loads


# ---------------------------------------------------------------------------------------------
# VFPU interpreter (subset). Register numbering and layout follow PPSSPP.

VFPU_CONSTANTS = {
    5: 2.0 / math.pi,  # VFPU_2_PI
}


def _voffset(reg: int) -> int:
    return ((reg >> 2) & 7) * 16 + (reg & 3) * 4 + ((reg >> 5) & 3)


class Vfpu:
    def __init__(self) -> None:
        self.v = [0.0] * 128
        self.cc = [False] * 6
        self.dprefix: int | None = None

    @staticmethod
    def vec_size(op: int) -> int:
        return ((op >> 7) & 1) + ((op >> 14) & 2) + 1

    def read_vec(self, reg: int, n: int) -> list[float]:
        if n == 1:
            return [self.v[_voffset(reg)]]
        row = (reg >> 6) & 1 if n == 3 else (reg >> 5) & 2
        mtx, col = (reg << 2) & 0x70, reg & 3
        if (reg >> 5) & 1:
            return [self.v[mtx + col + ((row + i) & 3) * 4] for i in range(n)]
        return [self.v[mtx + col * 4 + ((row + i) & 3)] for i in range(n)]

    def write_vec(self, reg: int, vals: list[float]) -> None:
        n = len(vals)
        pfx = self.dprefix or 0
        out = []
        for i, x in enumerate(vals):
            sat = (pfx >> (2 * i)) & 3
            if sat == 1:
                x = min(max(x, 0.0), 1.0)
            elif sat == 3:
                x = min(max(x, -1.0), 1.0)
            out.append(f32(x))
        masked = [(pfx >> (8 + i)) & 1 for i in range(n)]
        self.dprefix = None
        if n == 1:
            if not masked[0]:
                self.v[_voffset(reg)] = out[0]
            return
        row = (reg >> 6) & 1 if n == 3 else (reg >> 5) & 2
        mtx, col = (reg << 2) & 0x70, reg & 3
        for i in range(n):
            if masked[i]:
                continue
            if (reg >> 5) & 1:
                self.v[mtx + col + ((row + i) & 3) * 4] = out[i]
            else:
                self.v[mtx + col * 4 + ((row + i) & 3)] = out[i]

    def read_matrix(self, reg: int, side: int) -> list[list[float]]:
        transpose = (reg >> 5) & 1
        row = (reg >> 6) & 1 if side == 3 else (reg >> 5) & 2
        mtx, col = ((reg >> 2) & 7) * 16, reg & 3
        m = [[0.0] * side for _ in range(side)]
        for j in range(side):
            for i in range(side):
                if transpose:
                    m[j][i] = self.v[mtx + ((row + i) & 3) * 4 + ((col + j) & 3)]
                else:
                    m[j][i] = self.v[mtx + ((col + j) & 3) * 4 + ((row + i) & 3)]
        return m

    def execute(self, op: int, uc: Uc, gpr: Callable[[int], int]) -> None:
        top = op >> 26
        if top == 0x32 or top == 0x3A:  # lv.s / sv.s
            vt = ((op >> 16) & 0x1F) | ((op & 3) << 5)
            addr = (gpr((op >> 21) & 0x1F) + sext16(op & 0xFFFC)) & 0xFFFFFFFF
            if top == 0x32:
                self.v[_voffset(vt)] = struct.unpack("<f", uc.mem_read(addr, 4))[0]
            else:
                uc.mem_write(addr, struct.pack("<f", self.v[_voffset(vt)]))
            return
        n = self.vec_size(op)
        vd, vs, vt = op & 0x7F, (op >> 8) & 0x7F, (op >> 16) & 0x7F
        if top == 0x19:
            sub = (op >> 23) & 7
            s = self.read_vec(vs, n)
            if sub == 0:  # vmul
                t = self.read_vec(vt, n)
                self.write_vec(vd, [a * b for a, b in zip(s, t)])
            elif sub == 1:  # vdot
                t = self.read_vec(vt, n)
                self.write_vec(vd, [math.fsum(a * b for a, b in zip(s, t))])
            elif sub == 2:  # vscl
                k = self.read_vec(vt, 1)[0]
                self.write_vec(vd, [a * k for a in s])
            else:
                raise NotImplementedError(f"VFPU1 sub {sub} op {op:08x}")
            return
        if top == 0x1B and ((op >> 23) & 7) == 0:  # vcmp
            cond = op & 0xF
            s, t = self.read_vec(vs, n), self.read_vec(vt, n)
            tests = {
                1: lambda a, b: a == b, 2: lambda a, b: a < b, 3: lambda a, b: a <= b,
                5: lambda a, b: a != b, 6: lambda a, b: a >= b, 7: lambda a, b: a > b,
                8: lambda a, _b: a == 0.0, 12: lambda a, _b: a != 0.0,
            }
            if cond not in tests:
                raise NotImplementedError(f"vcmp cond {cond}")
            res = [tests[cond](a, b) for a, b in zip(s, t)]
            self.cc = res + [False] * (4 - n) + [any(res), all(res)]
            return
        if top == 0x34:
            grp = (op >> 21) & 0x1F
            if grp == 0:
                sub = (op >> 16) & 0x1F
                s = self.read_vec(vs, n)
                if sub == 6:
                    self.write_vec(vd, [0.0] * n)
                elif sub == 17:
                    self.write_vec(vd, [1.0 / math.sqrt(a) if a > 0 else math.inf for a in s])
                elif sub == 18:  # vsin: argument in quarter turns
                    self.write_vec(vd, [math.sin(a * math.pi / 2) for a in s])
                elif sub == 22:
                    self.write_vec(vd, [math.sqrt(a) if a >= 0 else math.nan for a in s])
                else:
                    raise NotImplementedError(f"VFPU4 sub {sub} op {op:08x}")
                return
            if grp == 3:  # vcst
                imm = (op >> 16) & 0x1F
                self.write_vec(vd, [VFPU_CONSTANTS[imm]] * n)
                return
            if grp == 0x15:  # vcmovt / vcmovf
                imm = (op >> 16) & 7
                want = ((op >> 19) & 1) == 0
                s = self.read_vec(vs, n)
                if imm < 6:
                    if self.cc[imm] == want:
                        self.write_vec(vd, s)
                    else:
                        self.dprefix = None
                else:
                    cur = self.read_vec(vd, n)
                    self.write_vec(vd, [a if self.cc[i] == want else c for i, (a, c) in enumerate(zip(s, cur))])
                return
        if top == 0x37 and ((op >> 24) & 3) == 2:  # vpfxd
            self.dprefix = op & 0xFFF
            return
        if top == 0x3C and ((op >> 23) & 7) in (1, 2, 3) and (op >> 26) == 0x3C:  # vtfm2/3/4
            ins = (op >> 23) & 3
            side = ins + 1
            m = self.read_matrix(vs, side)
            t = self.read_vec(vt, n)
            t2 = [t[i] if i < min(n, side) else (1.0 if i == ins else 0.0) for i in range(side)]
            self.write_vec(vd, [math.fsum(m[i][k] * t2[k] for k in range(side)) for i in range(side)])
            return
        raise NotImplementedError(f"VFPU op {op:08x}")


def is_vfpu(op: int) -> bool:
    top = op >> 26
    return top in (0x18, 0x19, 0x1B, 0x32, 0x34, 0x36, 0x37, 0x3A, 0x3C, 0x3E, 0x3F)


# ---------------------------------------------------------------------------------------------
# Rigid body stand-in (simple body branch of the physics library)

@dataclass
class Body:
    vel: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    omega: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    mass: float = 100.0
    inertia: float = 300.0

    def impulse(self, j: list[float] | None, tau: list[float] | None) -> None:
        if j is not None:
            inv = f32(1.0 / self.mass)
            self.vel = [f32(v + f32(x * inv)) for v, x in zip(self.vel, j)]
        if tau is not None:
            inv = f32(1.0 / self.inertia)
            ang_mom = [f32(f32(w * self.inertia) + t) for w, t in zip(self.omega, tau)]
            self.omega = [f32(a * inv) for a in ang_mom]


class PspMachine:
    """Relocated BOOT.BIN in Unicorn plus the stubs described in the module docstring."""

    def __init__(self, boot_bin: Path):
        elf, loads = relocated_image(boot_bin)
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_32 + UC_MODE_LITTLE_ENDIAN)
        self.uc.mem_map(0, IMAGE_SIZE)
        for off, vaddr, filesz, _memsz in loads:
            self.uc.mem_write(vaddr, elf[off:off + filesz])
        self.pristine = bytes(self.uc.mem_read(0, IMAGE_SIZE))
        self.uc.mem_map(HEAP_BASE, HEAP_SIZE)
        self.uc.mem_map(STACK_BASE, STACK_SIZE)
        self.uc.mem_map(STUB_BASE, STUB_SIZE)
        self.heap_top = HEAP_BASE
        self.vfpu = Vfpu()
        self.hooks: dict[int, Callable[[], None]] = {}
        self.bodies: dict[int, Body] = {}
        self.next_stub = STUB_BASE + 0x100
        self.log: list[tuple[str, tuple]] = []
        # Hooks are range-limited: Python callbacks on every instruction would be slow.
        self.uc.hook_add(UC_HOOK_CODE, self._on_vfpu, begin=VFPU_HELPERS[0], end=VFPU_HELPERS[1])
        self.uc.hook_add(UC_HOOK_CODE, self._on_stub, begin=STUB_BASE, end=STUB_BASE + STUB_SIZE - 1)

    # memory helpers
    def alloc(self, size: int) -> int:
        addr = self.heap_top
        self.heap_top += (size + 15) & ~15
        self.uc.mem_write(addr, b"\0" * size)
        return addr

    def rf(self, addr: int, n: int = 1) -> list[float]:
        return list(struct.unpack(f"<{n}f", self.uc.mem_read(addr, 4 * n)))

    def wf(self, addr: int, *vals: float) -> None:
        self.uc.mem_write(addr, struct.pack(f"<{len(vals)}f", *vals))

    def ri(self, addr: int) -> int:
        return struct.unpack("<i", self.uc.mem_read(addr, 4))[0]

    def wi(self, addr: int, val: int) -> None:
        self.uc.mem_write(addr, struct.pack("<I", val & 0xFFFFFFFF))

    def wh(self, addr: int, val: int) -> None:
        self.uc.mem_write(addr, struct.pack("<H", val & 0xFFFF))

    def rh(self, addr: int) -> int:
        return struct.unpack("<h", self.uc.mem_read(addr, 2))[0]

    # registers
    def gpr(self, idx: int) -> int:
        return self.uc.reg_read(UC_MIPS_REG_ZERO + idx) & 0xFFFFFFFF

    def freg(self, idx: int) -> float:
        raw = self.uc.reg_read(UC_MIPS_REG_F0 + idx) & 0xFFFFFFFF
        return struct.unpack("<f", struct.pack("<I", raw))[0]

    def set_freg(self, idx: int, val: float) -> None:
        self.uc.reg_write(UC_MIPS_REG_F0 + idx, struct.unpack("<I", struct.pack("<f", val))[0])

    # stubs
    def hook(self, addr: int, fn: Callable[[], None]) -> None:
        """Replace the function at `addr`; fn reads args and sets v0/f0 itself."""
        if addr not in self.hooks and addr < IMAGE_SIZE:
            self.uc.hook_add(UC_HOOK_CODE, self._on_stub, begin=addr, end=addr)
        self.hooks[addr] = fn

    def new_stub(self, fn: Callable[[], None]) -> int:
        addr = self.next_stub
        self.next_stub += 8
        self.hooks[addr] = fn
        return addr

    def ret(self, v0: int | None = None, f0: float | None = None) -> None:
        if v0 is not None:
            self.uc.reg_write(UC_MIPS_REG_V0, v0 & 0xFFFFFFFF)
        if f0 is not None:
            self.set_freg(0, f0)

    def _on_stub(self, uc: Uc, addr: int, _size: int, _user) -> None:
        fn = self.hooks.get(addr)
        if fn is None:
            raise RuntimeError(f"unhooked stub 0x{addr:x}")
        fn()
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def _on_vfpu(self, uc: Uc, addr: int, _size: int, _user) -> None:
        op = struct.unpack("<I", uc.mem_read(addr, 4))[0]
        if is_vfpu(op):
            self.vfpu.execute(op, uc, self.gpr)
            uc.reg_write(UC_MIPS_REG_PC, addr + 4)

    def call(self, addr: int, args: tuple[int, ...] = (), fargs: tuple[float, ...] = ()) -> int:
        regs = (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)
        for reg, val in zip(regs, args):
            self.uc.reg_write(reg, val & 0xFFFFFFFF)
        for i, val in enumerate(fargs):
            self.set_freg(12 + i, val)
        self.uc.reg_write(UC_MIPS_REG_SP, STACK_BASE + STACK_SIZE - 0x100)
        self.uc.reg_write(UC_MIPS_REG_RA, RET_MAGIC)
        try:
            self.uc.emu_start(addr, RET_MAGIC, count=5_000_000)
        except UcError as exc:
            pc = self.uc.reg_read(UC_MIPS_REG_PC)
            raise RuntimeError(f"emulation fault at pc=0x{pc:x}: {exc}") from exc
        return self.uc.reg_read(UC_MIPS_REG_V0)

    # common stubs
    def install_body_stubs(self) -> None:
        def get(attr: str) -> Callable[[], None]:
            def fn() -> None:
                body = self.bodies[self.gpr(4)]
                vec = getattr(body, attr)
                self.wf(self.gpr(5), *vec, 0.0)
                self.ret(v0=self.gpr(5))
            return fn

        def put(attr: str) -> Callable[[], None]:
            def fn() -> None:
                body = self.bodies[self.gpr(4)]
                setattr(body, attr, [f32(x) for x in self.rf(self.gpr(5), 3)])
            return fn

        def impulse() -> None:
            body = self.bodies[self.gpr(4)]
            j = self.rf(self.gpr(5), 3) if self.gpr(5) else None
            tau = self.rf(self.gpr(6), 3) if self.gpr(6) else None
            self.log.append(("impulse", (j, tau)))
            body.impulse(j, tau)

        self.hook(0x0E0F84, get("vel"))
        self.hook(0x0E1080, get("omega"))
        self.hook(0x0DFA68, put("vel"))
        self.hook(0x0DFB44, put("omega"))
        self.hook(0x0E11BC, impulse)

    def install_rw_transform_stubs(self) -> None:
        def transform(point: bool) -> Callable[[], None]:
            def fn() -> None:
                out, src, mtx = self.gpr(4), self.gpr(5), self.gpr(6)
                x, y, z = self.rf(src, 3)
                right, up, at, pos = (self.rf(mtx + 0x10 * k, 3) for k in range(4))
                res = [f32(x * right[i] + y * up[i] + z * at[i] + (pos[i] if point else 0.0)) for i in range(3)]
                self.wf(out, *res)
                self.ret(v0=out)
            return fn

        self.hook(0x1B5D6C, transform(True))   # RwV3dTransformPoint
        self.hook(0x1B5DF4, transform(False))  # RwV3dTransformVector


GroundFn = Callable[[list[float], list[float]], float]
