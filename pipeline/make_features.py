#!/usr/bin/env python3
"""Project OSM pistes and lifts onto the heightmap grid.

Reads data/<slug>/osm.json, projects every way into fractional pixel
coordinates (col, row) on the heightmap, and writes features.json for
the Blender stage (which has numpy but no geo libraries).

Usage:
    python pipeline/make_features.py resorts/val-disere.toml
"""

import json
import math
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

    raw_pistes, lifts = [], []
    n_closed = 0
    for el in osm["elements"]:
        tags = el.get("tags", {})
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        if "piste:type" in tags:
            # Skip area-mapped pistes (closed polygons) — as ribbons they
            # render as ugly loops. TODO: could become groomed-area patches.
            if geom[0] == geom[-1]:
                n_closed += 1
                continue
            raw_pistes.append({
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

    # --- Chain fragmented ways: same name+difficulty, touching endpoints ---
    def near(a, b, tol=1.5):
        return math.hypot(a[0] - b[0], a[1] - b[1]) < tol

    groups = {}
    for p in raw_pistes:
        groups.setdefault((p["name"], p["difficulty"]), []).append(p["points"])

    pistes = []
    for (name, diff), ways in groups.items():
        chains = []
        while ways:
            cur = ways.pop()
            extended = True
            while extended:
                extended = False
                for i, w in enumerate(ways):
                    if near(cur[-1], w[0]):
                        cur = cur + w[1:]
                    elif near(cur[-1], w[-1]):
                        cur = cur + w[::-1][1:]
                    elif near(cur[0], w[-1]):
                        cur = w[:-1] + cur
                    elif near(cur[0], w[0]):
                        cur = w[::-1][:-1] + cur
                    else:
                        continue
                    ways.pop(i)
                    extended = True
                    break
            chains.append(cur)
        for chain in chains:
            pistes.append({"name": name, "difficulty": diff, "points": chain})

    # --- Drop stubs: short connectors clutter the map ---
    px_m = meta["pixel_size_m"][0]
    min_len = config["features"].get("min_piste_len_m", 250)

    def length_m(points):
        return sum(math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(points[:-1], points[1:])) * px_m

    kept = [p for p in pistes if length_m(p["points"]) >= min_len]

    out = data_dir / "features.json"
    out.write_text(json.dumps({"pistes": kept, "lifts": lifts}))
    print(f"wrote {out}: {len(raw_pistes)} open ways ({n_closed} closed dropped) "
          f"-> {len(pistes)} chains -> {len(kept)} pistes >= {min_len}m; {len(lifts)} lifts")


if __name__ == "__main__":
    main()
