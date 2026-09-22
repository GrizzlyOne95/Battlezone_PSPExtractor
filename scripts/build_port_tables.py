#!/usr/bin/env python3
"""Normalize Battlezone PSP gameplay tables into one port-ready JSON file.

Input: either the raw USRDIR/leveldata folder (BZ_*.CSV) or the leveldata JSON produced by
extractors/extract_psp_data_tables.py (data_tables_json/leveldata_csv/BZ_*.json).

Output (port_tables.json):
- tank_motion:   {field: {small, medium, large}} keyed by snake_case CSV row name
- enhancements:  {field: {enum, major, minor}} in the game's eTopSpeed..eFastTeamSpecial order
- weapons:       [{snake_case column: value, "section": CSV section label}]
- projectiles:   [{snake_case column: value}]
- sp_tourney:    raw rows when BZ_SP_TOURNEY_DEFS is present

Values are converted to int/float where possible. Field names come from the CSV header comments,
so the output follows whatever the shipped tables say. See reverse_engineering/port/PORT_SPEC.md
for how the game code consumes each field.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from extractors.extract_psp_data_tables import parse_csv_file  # noqa: E402

SIZES = ("small", "medium", "large")
ENHANCE_ENUM = (
    "top_speed", "nitro_boost", "charge_weapon_energy", "pickup_duration", "hp_recharge_delay",
    "vampire_transfer", "lock_on_range", "damage", "splash_radius", "team_special_recharge",
)


def snake(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or "unnamed"


def number(value: str) -> Any:
    value = value.strip()
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def load_table(root: Path, stem: str) -> dict[str, Any] | None:
    for cand in (root / f"{stem}.json", root / "leveldata_csv" / f"{stem}.json"):
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    for cand in (root / f"{stem}.CSV", root / f"{stem}.csv"):
        if cand.is_file():
            return parse_csv_file(cand)
    return None


def section_name(row: dict[str, Any]) -> str:
    return str(row.get("section", "")).split(",")[0].strip()


def build_tank_motion(table: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in table["rows"]:
        key = snake(section_name(row))
        vals = [number(v) for v in row["values"][1:4]]
        out[key] = dict(zip(SIZES, vals))
    return out


def build_enhancements(table: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for idx, row in enumerate(table["rows"]):
        key = ENHANCE_ENUM[idx] if idx < len(ENHANCE_ENUM) else snake(section_name(row))
        vals = [number(v) for v in row["values"][1:3]]
        out[key] = {"enum": idx, "csv_label": section_name(row), "major": vals[0], "minor": vals[1]}
    return out


def build_rows(table: dict[str, Any], keep_section: bool) -> list[dict[str, Any]]:
    header = [snake(h) for h in table["header"]]
    rows = []
    for row in table["rows"]:
        rec = {header[i] if i < len(header) else f"col_{i}": number(v) for i, v in enumerate(row["values"])}
        if keep_section:
            rec["section"] = section_name(row)
        rows.append(rec)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build port_tables.json from Battlezone PSP gameplay tables.")
    parser.add_argument("--tables", type=Path, required=True,
                        help="USRDIR/leveldata (CSV) or data_tables_json[/leveldata_csv] (JSON) folder.")
    parser.add_argument("--out", type=Path, required=True, help="Output JSON path.")
    args = parser.parse_args()

    result: dict[str, Any] = {"source": Path(args.tables).name}
    missing = []
    builders = (
        ("BZ_TANK_MOTION", "tank_motion", build_tank_motion),
        ("BZ_ENHANCE_DEFS", "enhancements", build_enhancements),
        ("BZ_WEAP_DEFS", "weapons", lambda t: build_rows(t, True)),
        ("BZ_PROJ_DEFS", "projectiles", lambda t: build_rows(t, False)),
        ("BZ_SP_TOURNEY_DEFS", "sp_tourney", lambda t: build_rows(t, True)),
    )
    for stem, key, fn in builders:
        table = load_table(args.tables, stem)
        if table is None:
            missing.append(stem)
            continue
        result[key] = fn(table)
        print(f"[{key}] {stem}: {len(result[key])} entries")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if missing:
        print("Missing tables: " + ", ".join(missing))
    print(f"Done. out={args.out}")
    return 0 if "tank_motion" in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
