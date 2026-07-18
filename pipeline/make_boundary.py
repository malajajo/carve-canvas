#!/usr/bin/env python3
"""Derive the floating-world boundary from OSM ski infrastructure.

Takes every piste/lift geometry, buffers and unions them into one blob,
rounds and simplifies the outline, and rasterises the result onto the
heightmap grid. Outputs:

    data/<slug>/mask.npy        bool per heightmap vertex: inside the world?
    data/<slug>/boundary.json   the polygon (UTM coords) for later stages

Usage:
    python pipeline/make_boundary.py resorts/val-disere.toml
"""

import json
import sys
import tomllib
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    config_path = Path(sys.argv[1])
    config = tomllib.loads(config_path.read_text())
    bcfg = config["boundary"]
    data_dir = ROOT / "data" / config["slug"]

    meta = json.loads((data_dir / "meta.json").read_text())
    osm = json.loads((data_dir / "osm.json").read_text())

    # --- Collect piste/lift geometries, projected to the heightmap's UTM CRS ---
    lines = []
    for el in osm["elements"]:
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        lons = [p["lon"] for p in geom]
        lats = [p["lat"] for p in geom]
        xs, ys = warp_transform("EPSG:4326", meta["crs"], lons, lats)
        lines.append(LineString(zip(xs, ys)))
    if not lines:
        raise SystemExit("no OSM geometries found — run fetch_osm.py first?")

    # --- Buffer + union into a single blob, then round and simplify ---
    blob = unary_union([l.buffer(bcfg["buffer_m"]) for l in lines])
    if blob.geom_type == "MultiPolygon":
        blob = max(blob.geoms, key=lambda g: g.area)
    # Morphological closing rounds concavities; exterior-only drops holes
    blob = blob.buffer(bcfg["smooth_m"]).buffer(-bcfg["smooth_m"])
    if blob.geom_type == "MultiPolygon":
        blob = max(blob.geoms, key=lambda g: g.area)
    poly = Polygon(blob.exterior).simplify(bcfg["simplify_m"])

    # --- Clip to the heightmap extent (inset one pixel so the skirt has room) ---
    t = Affine(*meta["transform"])
    w, h = meta["width_px"], meta["height_px"]
    x0, y0 = t * (1, h - 1)
    x1, y1 = t * (w - 1, 1)
    poly = poly.intersection(box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)

    # --- Rasterise onto the heightmap vertex grid ---
    mask = rasterize([(poly, 1)], out_shape=(h, w), transform=t, dtype="uint8").astype(bool)

    np.save(data_dir / "mask.npy", mask)
    (data_dir / "boundary.json").write_text(json.dumps({
        "crs": meta["crs"],
        "exterior_xy": list(map(list, poly.exterior.coords)),
    }))
    covered = mask.mean() * 100
    print(f"wrote {data_dir}/mask.npy — boundary covers {covered:.0f}% of the DEM "
          f"({poly.area / 1e6:.1f} km², {len(poly.exterior.coords)} outline points)")


if __name__ == "__main__":
    main()
