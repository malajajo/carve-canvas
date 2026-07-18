#!/usr/bin/env python3
"""Convert a cropped DEM (dem.tif) into a clean heightmap grid for Blender.

Reprojects from WGS84 lat/lon to the local UTM zone (so distances are in
metres), resamples to the configured grid size, and writes:

    data/<slug>/heightmap.npy   float32 elevations in metres, row 0 = north
    data/<slug>/meta.json       grid dimensions, extent in metres, elevation range

Usage:
    python pipeline/make_heightmap.py resorts/val-disere.toml
"""

import json
import sys
import tomllib
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject

ROOT = Path(__file__).resolve().parent.parent


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def main() -> None:
    config_path = Path(sys.argv[1])
    config = tomllib.loads(config_path.read_text())
    slug = config["slug"]
    size = config["terrain"]["heightmap_size"]

    data_dir = ROOT / "data" / slug
    with rasterio.open(data_dir / "dem.tif") as src:
        center_lon = (src.bounds.left + src.bounds.right) / 2
        center_lat = (src.bounds.bottom + src.bounds.top) / 2
        dst_crs = f"EPSG:{utm_epsg(center_lon, center_lat)}"

        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        # Scale so the longest side is `size` pixels
        scale = max(width, height) / size
        dst_w, dst_h = round(width / scale), round(height / scale)
        transform = transform * transform.scale(width / dst_w, height / dst_h)

        heightmap = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=heightmap,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

    # The lat/lon rectangle is slightly non-rectangular in UTM, leaving
    # nodata slivers at the corners. Trim edge rows/cols until clean,
    # tracking offsets so the geo transform stays correct.
    top_off = left_off = 0
    while np.isnan(heightmap).any():
        edges = {
            "top": np.isnan(heightmap[0]).sum(),
            "bottom": np.isnan(heightmap[-1]).sum(),
            "left": np.isnan(heightmap[:, 0]).sum(),
            "right": np.isnan(heightmap[:, -1]).sum(),
        }
        worst = max(edges, key=edges.get)
        if edges[worst] == 0:
            raise RuntimeError("nodata found in DEM interior, not just edges")
        if worst == "top":
            heightmap, top_off = heightmap[1:], top_off + 1
        elif worst == "bottom":
            heightmap = heightmap[:-1]
        elif worst == "left":
            heightmap, left_off = heightmap[:, 1:], left_off + 1
        else:
            heightmap = heightmap[:, :-1]
    transform = transform * transform.translation(left_off, top_off)
    dst_h, dst_w = heightmap.shape

    pixel_m = (abs(transform.a), abs(transform.e))
    meta = {
        "slug": slug,
        "name": config["name"],
        "crs": dst_crs,
        "width_px": dst_w,
        "height_px": dst_h,
        "pixel_size_m": pixel_m,
        "extent_m": [dst_w * pixel_m[0], dst_h * pixel_m[1]],
        "transform": list(transform)[:6],  # affine: pixel (col,row) -> UTM (x,y)
        "elevation_min_m": float(heightmap.min()),
        "elevation_max_m": float(heightmap.max()),
    }

    np.save(data_dir / "heightmap.npy", heightmap)
    (data_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {data_dir}/heightmap.npy ({dst_w}x{dst_h} px, "
          f"{meta['extent_m'][0]/1000:.1f} x {meta['extent_m'][1]/1000:.1f} km, "
          f"elevation {meta['elevation_min_m']:.0f}-{meta['elevation_max_m']:.0f} m)")


if __name__ == "__main__":
    main()
