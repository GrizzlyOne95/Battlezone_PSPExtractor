#!/usr/bin/env python3
"""
Extract PSP RenderWare .rws geometry to Wavefront OBJ.

Pass 1: models (.rws files under USRDIR/models) by decoding Clump chunks (type 16).
Pass 2: terrain worlds (.rws files under USRDIR/terrains) by decoding World chunks
        (type 11) and walking plane/atomic sectors (types 10/9).
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# RenderWare chunk ids
CHUNK_STRUCT = 1
CHUNK_EXTENSION = 3
CHUNK_MATERIAL_LIST = 8
CHUNK_ATOMIC_SECTOR = 9
CHUNK_PLANE_SECTOR = 10
CHUNK_WORLD = 11
CHUNK_CLUMP = 16

# Containers encountered in these .rws files that can wrap other chunks.
RECURSE_CHUNK_TYPES = {
    3,   # Extension
    9,   # Atomic Sector
    10,  # Plane Sector
    11,  # World
    16,  # Clump
    36,
    41,
    42,
    43,
}


@dataclass(frozen=True)
class ChunkInfo:
    chunk_type: int
    chunk_size: int
    chunk_version: int
    header_start: int
    payload_start: int
    payload_end: int


@dataclass
class MaterialSpec:
    name: str
    color: tuple[float, float, float]
    texture_name: str | None


@dataclass
class ObjObject:
    name: str
    vertices: list[tuple[float, float, float]]
    normals: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    faces: list[tuple[int, int, int, str]]


def _safe_name(text: str) -> str:
    if not text:
        return "unnamed"
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return out.strip("._") or "unnamed"


def _iter_chunks(blob: bytes, start: int, end: int) -> Iterator[ChunkInfo]:
    pos = start
    lim = min(end, len(blob))
    while pos + 12 <= lim:
        chunk_type, chunk_size, chunk_ver = struct.unpack_from("<III", blob, pos)
        payload_start = pos + 12
        payload_end = payload_start + chunk_size
        if payload_end > lim:
            break
        yield ChunkInfo(
            chunk_type=chunk_type,
            chunk_size=chunk_size,
            chunk_version=chunk_ver,
            header_start=pos,
            payload_start=payload_start,
            payload_end=payload_end,
        )
        pos = payload_end


def _find_chunks(
    blob: bytes,
    start: int,
    end: int,
    target_type: int,
    recurse_types: set[int],
    max_depth: int = 32,
) -> list[ChunkInfo]:
    found: list[ChunkInfo] = []

    def walk(s: int, e: int, depth: int) -> None:
        if depth > max_depth:
            return
        for ch in _iter_chunks(blob, s, e):
            if ch.chunk_type == target_type:
                found.append(ch)
            if ch.chunk_type in recurse_types:
                walk(ch.payload_start, ch.payload_end, depth + 1)

    walk(start, end, 0)
    return found


def _mat_identity() -> tuple[tuple[float, float, float, float], ...]:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _mat_mul(
    a: tuple[tuple[float, float, float, float], ...],
    b: tuple[tuple[float, float, float, float], ...],
) -> tuple[tuple[float, float, float, float], ...]:
    out = [[0.0] * 4 for _ in range(4)]
    for r in range(4):
        for c in range(4):
            out[r][c] = (
                a[r][0] * b[0][c]
                + a[r][1] * b[1][c]
                + a[r][2] * b[2][c]
                + a[r][3] * b[3][c]
            )
    return tuple(tuple(row) for row in out)


def _frame_to_matrix(frame) -> tuple[tuple[float, float, float, float], ...]:
    right = frame.rotation_matrix.right
    up = frame.rotation_matrix.up
    at = frame.rotation_matrix.at
    pos = frame.position
    return (
        (right.x, up.x, at.x, pos.x),
        (right.y, up.y, at.y, pos.y),
        (right.z, up.z, at.z, pos.z),
        (0.0, 0.0, 0.0, 1.0),
    )


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt((v[0] * v[0]) + (v[1] * v[1]) + (v[2] * v[2]))
    if length <= 1e-9:
        return v
    inv = 1.0 / length
    return (v[0] * inv, v[1] * inv, v[2] * inv)


def _transform_point(
    m: tuple[tuple[float, float, float, float], ...], p
) -> tuple[float, float, float]:
    x = (m[0][0] * p.x) + (m[0][1] * p.y) + (m[0][2] * p.z) + m[0][3]
    y = (m[1][0] * p.x) + (m[1][1] * p.y) + (m[1][2] * p.z) + m[1][3]
    z = (m[2][0] * p.x) + (m[2][1] * p.y) + (m[2][2] * p.z) + m[2][3]
    return (x, y, z)


def _transform_normal(
    m: tuple[tuple[float, float, float, float], ...], n
) -> tuple[float, float, float]:
    x = (m[0][0] * n.x) + (m[0][1] * n.y) + (m[0][2] * n.z)
    y = (m[1][0] * n.x) + (m[1][1] * n.y) + (m[1][2] * n.z)
    z = (m[2][0] * n.x) + (m[2][1] * n.y) + (m[2][2] * n.z)
    return _normalize((x, y, z))


def _compute_frame_world_mats(frame_list: list) -> dict[int, tuple[tuple[float, float, float, float], ...]]:
    world: dict[int, tuple[tuple[float, float, float, float], ...]] = {}

    def build(idx: int) -> tuple[tuple[float, float, float, float], ...]:
        if idx in world:
            return world[idx]

        frame = frame_list[idx]
        local = _frame_to_matrix(frame)
        if 0 <= frame.parent < len(frame_list):
            mat = _mat_mul(build(frame.parent), local)
        else:
            mat = local
        world[idx] = mat
        return mat

    for i in range(len(frame_list)):
        build(i)
    return world


def _texture_name_from_material(material) -> str | None:
    if getattr(material, "textures", None):
        tex = material.textures[0]
        if tex and getattr(tex, "name", ""):
            return _safe_name(tex.name)
    return None


def _register_material(
    out: dict[str, MaterialSpec], name: str, material
) -> str:
    base = _safe_name(name)
    final = base
    n = 1
    while final in out:
        n += 1
        final = f"{base}_{n}"

    color = getattr(material, "color", None)
    if color is None:
        kd = (0.8, 0.8, 0.8)
    else:
        kd = (color.r / 255.0, color.g / 255.0, color.b / 255.0)

    out[final] = MaterialSpec(
        name=final,
        color=kd,
        texture_name=_texture_name_from_material(material),
    )
    return final


def _material_names_for_geometry(
    geometry,
    prefix: str,
    out_materials: dict[str, MaterialSpec],
) -> list[str]:
    names: list[str] = []
    if not getattr(geometry, "materials", None):
        if "__default__" not in out_materials:
            out_materials["__default__"] = MaterialSpec(
                name="__default__", color=(0.8, 0.8, 0.8), texture_name=None
            )
        return ["__default__"]

    for i, mat in enumerate(geometry.materials):
        tex = _texture_name_from_material(mat) or "solid"
        names.append(_register_material(out_materials, f"{prefix}_m{i:03d}_{tex}", mat))
    return names


def _geometry_to_obj_object(
    geometry,
    name: str,
    transform: tuple[tuple[float, float, float, float], ...],
    material_names: list[str],
) -> ObjObject | None:
    verts_src = getattr(geometry, "vertices", [])
    tris_src = getattr(geometry, "triangles", [])
    if not verts_src or not tris_src:
        return None

    vertices = [_transform_point(transform, v) for v in verts_src]

    normals: list[tuple[float, float, float]] = []
    normals_src = getattr(geometry, "normals", [])
    if len(normals_src) == len(verts_src):
        normals = [_transform_normal(transform, n) for n in normals_src]

    uvs: list[tuple[float, float]] = []
    uv_layers = getattr(geometry, "uv_layers", [])
    if uv_layers and len(uv_layers[0]) == len(verts_src):
        # Keep UV orientation as-is; source textures are already in RW convention.
        uvs = [(uv.u, uv.v) for uv in uv_layers[0]]

    faces: list[tuple[int, int, int, str]] = []
    vcount = len(vertices)
    for tri in tris_src:
        a = int(tri.a)
        b = int(tri.b)
        c = int(tri.c)
        if a < 0 or b < 0 or c < 0:
            continue
        if a >= vcount or b >= vcount or c >= vcount:
            continue
        m = int(tri.material)
        mat_name = material_names[m] if 0 <= m < len(material_names) else material_names[0]
        faces.append((a, b, c, mat_name))

    if not faces:
        return None

    return ObjObject(
        name=_safe_name(name),
        vertices=vertices,
        normals=normals,
        uvs=uvs,
        faces=faces,
    )


def _write_obj_and_mtl(
    obj_path: Path,
    objects: Iterable[ObjObject],
    materials: dict[str, MaterialSpec],
) -> None:
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    mtl_path = obj_path.with_suffix(".mtl")

    mtl_lines: list[str] = []
    for mat_name, spec in materials.items():
        mtl_lines.append(f"newmtl {mat_name}")
        mtl_lines.append(f"Kd {spec.color[0]:.6f} {spec.color[1]:.6f} {spec.color[2]:.6f}")
        mtl_lines.append("Ka 0.000000 0.000000 0.000000")
        mtl_lines.append("Ks 0.000000 0.000000 0.000000")
        mtl_lines.append("d 1.0")
        mtl_lines.append("illum 1")
        if spec.texture_name:
            tex = spec.texture_name
            if "." not in tex:
                tex = f"{tex}.png"
            mtl_lines.append(f"map_Kd {tex}")
        mtl_lines.append("")

    mtl_path.write_text("\n".join(mtl_lines), encoding="utf-8")

    obj_lines: list[str] = [f"mtllib {mtl_path.name}", ""]
    v_off = 1
    vt_off = 1
    vn_off = 1

    for obj in objects:
        obj_lines.append(f"o {obj.name}")
        for vx, vy, vz in obj.vertices:
            obj_lines.append(f"v {vx:.6f} {vy:.6f} {vz:.6f}")
        for tu, tv in obj.uvs:
            obj_lines.append(f"vt {tu:.6f} {tv:.6f}")
        for nx, ny, nz in obj.normals:
            obj_lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")

        has_uv = len(obj.uvs) == len(obj.vertices) and len(obj.uvs) > 0
        has_n = len(obj.normals) == len(obj.vertices) and len(obj.normals) > 0
        cur_mat = ""
        for a, b, c, mat_name in obj.faces:
            if mat_name != cur_mat:
                obj_lines.append(f"usemtl {mat_name}")
                cur_mat = mat_name

            ia = a + v_off
            ib = b + v_off
            ic = c + v_off

            if has_uv and has_n:
                ta = a + vt_off
                tb = b + vt_off
                tc = c + vt_off
                na = a + vn_off
                nb = b + vn_off
                nc = c + vn_off
                obj_lines.append(f"f {ia}/{ta}/{na} {ib}/{tb}/{nb} {ic}/{tc}/{nc}")
            elif has_uv:
                ta = a + vt_off
                tb = b + vt_off
                tc = c + vt_off
                obj_lines.append(f"f {ia}/{ta} {ib}/{tb} {ic}/{tc}")
            elif has_n:
                na = a + vn_off
                nb = b + vn_off
                nc = c + vn_off
                obj_lines.append(f"f {ia}//{na} {ib}//{nb} {ic}//{nc}")
            else:
                obj_lines.append(f"f {ia} {ib} {ic}")

        obj_lines.append("")

        v_off += len(obj.vertices)
        vt_off += len(obj.uvs)
        vn_off += len(obj.normals)

    obj_path.write_text("\n".join(obj_lines), encoding="utf-8")


def _parse_world_materials(blob: bytes, world_chunk: ChunkInfo, dffmod) -> list:
    children = list(_iter_chunks(blob, world_chunk.payload_start, world_chunk.payload_end))
    mat_chunk = next((c for c in children if c.chunk_type == CHUNK_MATERIAL_LIST), None)
    if mat_chunk is None:
        return []

    geom = dffmod.Geometry()
    parser = dffmod.dff()
    parser.data = blob
    parser.pos = mat_chunk.payload_start
    parser.geometry_list = [geom]
    parser.read_material_list(
        dffmod.Chunk(
            mat_chunk.chunk_type,
            mat_chunk.chunk_size,
            mat_chunk.chunk_version,
        )
    )
    return geom.materials


def _find_world_root_sector(blob: bytes, world_chunk: ChunkInfo) -> ChunkInfo | None:
    for ch in _iter_chunks(blob, world_chunk.payload_start, world_chunk.payload_end):
        if ch.chunk_type in (CHUNK_PLANE_SECTOR, CHUNK_ATOMIC_SECTOR):
            return ch
    return None


def _collect_atomic_sectors(blob: bytes, root_sector: ChunkInfo) -> list[ChunkInfo]:
    out: list[ChunkInfo] = []

    def walk(sector: ChunkInfo) -> None:
        if sector.chunk_type == CHUNK_ATOMIC_SECTOR:
            out.append(sector)
            return
        if sector.chunk_type != CHUNK_PLANE_SECTOR:
            return
        for child in _iter_chunks(blob, sector.payload_start, sector.payload_end):
            if child.chunk_type in (CHUNK_PLANE_SECTOR, CHUNK_ATOMIC_SECTOR):
                walk(child)

    walk(root_sector)
    return out


def _parse_atomic_sector_geometry(blob: bytes, atomic_sector: ChunkInfo, world_materials: list, dffmod):
    ext_chunk = None
    for ch in _iter_chunks(blob, atomic_sector.payload_start, atomic_sector.payload_end):
        if ch.chunk_type == CHUNK_EXTENSION:
            ext_chunk = ch
            break
    if ext_chunk is None:
        return None

    geom = dffmod.Geometry()
    geom.flags = dffmod.rpGEOMETRYNATIVE
    geom.materials = list(world_materials)

    parser = dffmod.dff()
    parser.data = blob
    parser.pos = ext_chunk.payload_start

    while parser.pos + 12 <= ext_chunk.payload_end:
        sub = parser.read_chunk()
        sub_end = parser.pos + sub.size
        if sub_end > ext_chunk.payload_end:
            break

        if sub.type == dffmod.types["Bin Mesh PLG"]:
            parser.read_mesh_plg(sub, geom)
        elif sub.type == dffmod.types["Native Data PLG"]:
            parser.read_native_data_plg(sub, geom)
        else:
            parser._read(sub.size)

    if not geom.vertices or not geom.triangles:
        return None
    return geom


def _extract_model_rws(rws_path: Path, out_root: Path, dffmod) -> tuple[int, int, int]:
    blob = rws_path.read_bytes()
    clumps = _find_chunks(
        blob,
        0,
        len(blob),
        CHUNK_CLUMP,
        recurse_types=RECURSE_CHUNK_TYPES,
    )

    exported = 0
    total_objects = 0
    failures = 0

    for ci, clump in enumerate(clumps):
        try:
            parser = dffmod.dff()
            parser.load_memory(blob[clump.header_start : clump.payload_end])

            materials: dict[str, MaterialSpec] = {}
            objects: list[ObjObject] = []
            frame_world = _compute_frame_world_mats(parser.frame_list)

            if parser.atomic_list:
                for ai, atomic in enumerate(parser.atomic_list):
                    if not (0 <= atomic.geometry < len(parser.geometry_list)):
                        continue
                    geometry = parser.geometry_list[atomic.geometry]
                    prefix = f"g{atomic.geometry:03d}"
                    mat_names = _material_names_for_geometry(geometry, prefix, materials)

                    obj_name = f"atomic_{ai:03d}"
                    if 0 <= atomic.frame < len(parser.frame_list):
                        frame_name = parser.frame_list[atomic.frame].name
                        if frame_name:
                            obj_name = frame_name

                    transform = frame_world.get(atomic.frame, _mat_identity())
                    obj = _geometry_to_obj_object(geometry, obj_name, transform, mat_names)
                    if obj is not None:
                        objects.append(obj)
            else:
                for gi, geometry in enumerate(parser.geometry_list):
                    mat_names = _material_names_for_geometry(geometry, f"g{gi:03d}", materials)
                    obj = _geometry_to_obj_object(
                        geometry,
                        f"geometry_{gi:03d}",
                        _mat_identity(),
                        mat_names,
                    )
                    if obj is not None:
                        objects.append(obj)

            if not objects:
                continue

            obj_path = out_root / f"{rws_path.stem}_clump_{ci:03d}.obj"
            _write_obj_and_mtl(obj_path, objects, materials)
            exported += 1
            total_objects += len(objects)
        except Exception:
            failures += 1

    return exported, total_objects, failures


def _extract_terrain_world_rws(rws_path: Path, out_root: Path, dffmod) -> tuple[int, int, int]:
    blob = rws_path.read_bytes()
    worlds = _find_chunks(
        blob,
        0,
        len(blob),
        CHUNK_WORLD,
        recurse_types=RECURSE_CHUNK_TYPES,
    )

    exported = 0
    total_objects = 0
    failures = 0

    for wi, world in enumerate(worlds):
        try:
            world_materials = _parse_world_materials(blob, world, dffmod)
            world_mat_names: list[str] = []
            out_materials: dict[str, MaterialSpec] = {}

            if world_materials:
                for mi, mat in enumerate(world_materials):
                    tex = _texture_name_from_material(mat) or "solid"
                    name = _register_material(out_materials, f"world_m{mi:03d}_{tex}", mat)
                    world_mat_names.append(name)
            else:
                out_materials["__default__"] = MaterialSpec(
                    name="__default__", color=(0.8, 0.8, 0.8), texture_name=None
                )
                world_mat_names.append("__default__")

            root_sector = _find_world_root_sector(blob, world)
            if root_sector is None:
                continue

            sectors = _collect_atomic_sectors(blob, root_sector)
            objects: list[ObjObject] = []
            for si, sector in enumerate(sectors):
                geom = _parse_atomic_sector_geometry(blob, sector, world_materials, dffmod)
                if geom is None:
                    continue
                obj = _geometry_to_obj_object(
                    geom,
                    f"sector_{si:04d}",
                    _mat_identity(),
                    world_mat_names,
                )
                if obj is not None:
                    objects.append(obj)

            if not objects:
                continue

            obj_path = out_root / f"{rws_path.stem}_world_{wi:03d}.obj"
            _write_obj_and_mtl(obj_path, objects, out_materials)
            exported += 1
            total_objects += len(objects)
        except Exception:
            failures += 1

    return exported, total_objects, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract PSP RWS geometry to OBJ (models first, then terrain worlds)."
    )
    parser.add_argument(
        "--dragonff-root",
        type=Path,
        required=True,
        help="Path to DragonFF repository root.",
    )
    parser.add_argument(
        "--models-root",
        type=Path,
        required=True,
        help="Directory containing model .rws files.",
    )
    parser.add_argument(
        "--terrains-root",
        type=Path,
        required=True,
        help="Directory containing terrain .rws files.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output directory for OBJ/MTL exports.",
    )
    parser.add_argument(
        "--mode",
        choices=("models", "terrains", "all"),
        default="all",
        help="Extraction pass to run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional file limit per pass (0 = no limit).",
    )
    args = parser.parse_args()

    if not args.dragonff_root.exists():
        print(f"DragonFF path not found: {args.dragonff_root}", file=sys.stderr)
        return 2
    if args.mode in ("models", "all") and not args.models_root.exists():
        print(f"Models path not found: {args.models_root}", file=sys.stderr)
        return 2
    if args.mode in ("terrains", "all") and not args.terrains_root.exists():
        print(f"Terrains path not found: {args.terrains_root}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(args.dragonff_root))
    from gtaLib import dff as dffmod  # type: ignore

    args.out_root.mkdir(parents=True, exist_ok=True)

    total_exported = 0
    total_objects = 0
    total_failures = 0

    if args.mode in ("models", "all"):
        model_files = sorted(args.models_root.glob("*.rws"))
        if args.limit > 0:
            model_files = model_files[: args.limit]
        model_out = args.out_root / "models"
        model_out.mkdir(parents=True, exist_ok=True)

        for rws in model_files:
            per_out = model_out / rws.stem
            per_out.mkdir(parents=True, exist_ok=True)
            exported, objects, failures = _extract_model_rws(rws, per_out, dffmod)
            total_exported += exported
            total_objects += objects
            total_failures += failures
            print(
                f"[models] {rws.name}: clumps_exported={exported} objects={objects} failures={failures}"
            )

    if args.mode in ("terrains", "all"):
        terrain_files = sorted(args.terrains_root.glob("*.rws"))
        if args.limit > 0:
            terrain_files = terrain_files[: args.limit]
        terrain_out = args.out_root / "terrains"
        terrain_out.mkdir(parents=True, exist_ok=True)

        for rws in terrain_files:
            per_out = terrain_out / rws.stem
            per_out.mkdir(parents=True, exist_ok=True)
            exported, objects, failures = _extract_terrain_world_rws(rws, per_out, dffmod)
            total_exported += exported
            total_objects += objects
            total_failures += failures
            print(
                f"[terrains] {rws.name}: worlds_exported={exported} sector_objects={objects} failures={failures}"
            )

    print(
        f"Done. exports={total_exported} objects={total_objects} failures={total_failures} out={args.out_root}"
    )
    return 0 if total_exported > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
