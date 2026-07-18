#!/usr/bin/env python3
"""Project OSM pistes and lifts onto the heightmap grid.

Reads data/<slug>/osm.json, projects every way into fractional pixel
coordinates (col, row) on the heightmap, and writes features.json for
the Blender stage (which has numpy but no geo libraries).

Usage:
    python pipeline/make_features.py resorts/val-disere.toml
"""

import json
import sys
import tomllib
from pathlib import Path

from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform

ROOT = Path(__file__).resolve().parent.parent

# aerialway values that are not passenger ski lifts
LIFT_EXCLUDE = {"goods", "pylon", "station", "zip_line"}


def main() -> None:
    config_path = Path(sys.argv[1])
    config = tomllib.loads(config_path.read_text())
    data_dir = ROOT / "data" / config["slug"]

    meta = json.loads((data_dir / "meta.json").read_text())
    osm = json.loads((data_dir / "osm.json").read_text())
    inv = ~Affine(*meta["transform"])  # UTM (x, y) -> pixel (col, row)

    def project(geom):
        lons = [p["lon"] for p in geom]
        lats = [p["lat"] for p in geom]
        xs, ys = warp_transform("EPSG:4326", meta["crs"], lons, lats)
        return [list(inv * (x, y)) for x, y in zip(xs, ys)]

    pistes, lifts = [], []
    for el in osm["elements"]:
        tags = el.get("tags", {})
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        if "piste:type" in tags:
            pistes.append({
                "name": tags.get("name", ""),
                "difficulty": tags.get("piste:difficulty", "intermediate"),
                "points": project(geom),
            })
        elif "aerialway" in tags and tags["aerialway"] not in LIFT_EXCLUDE:
            lifts.append({
                "name": tags.get("name", ""),
                "type": tags["aerialway"],
                "points": project(geom),
            })

    out = data_dir / "features.json"
    out.write_text(json.dumps({"pistes": pistes, "lifts": lifts}))
    print(f"wrote {out} ({len(pistes)} pistes, {len(lifts)} lifts)")


if __name__ == "__main__":
    main()
