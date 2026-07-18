#!/usr/bin/env python3
"""Fetch Copernicus GLO-30 DEM tiles covering a resort's bounding box.

Downloads the 1x1 degree tiles from the public AWS Open Data bucket
(no account needed), merges them, crops to the resort bbox and writes
data/<slug>/dem.tif.

Usage:
    python pipeline/fetch_dem.py resorts/val-disere.toml
"""

import math
import sys
import tomllib
import urllib.request
from pathlib import Path

import rasterio
from rasterio.merge import merge
from rasterio.transform import Affine

ROOT = Path(__file__).resolve().parent.parent
TILE_CACHE = ROOT / "data" / "tiles"
TILE_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"


def tile_name(lat: int, lon: int) -> str:
    """Copernicus tile name for the 1x1 degree cell whose SW corner is (lat, lon)."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def tiles_for_bbox(west, south, east, north):
    for lat in range(math.floor(south), math.ceil(north)):
        for lon in range(math.floor(west), math.ceil(east)):
            yield tile_name(lat, lon)


def download_tile(name: str) -> Path:
    dest = TILE_CACHE / f"{name}.tif"
    if dest.exists():
        print(f"  cached   {name}")
        return dest
    url = TILE_URL.format(name=name)
    print(f"  download {name} ...")
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    print(f"           {dest.stat().st_size / 1e6:.1f} MB")
    return dest


def main() -> None:
    config_path = Path(sys.argv[1])
    config = tomllib.loads(config_path.read_text())
    slug = config["slug"]
    b = config["bbox"]

    TILE_CACHE.mkdir(parents=True, exist_ok=True)
    out_dir = ROOT / "data" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "dem.tif"

    print(f"[{config['name']}] bbox W{b['west']} S{b['south']} E{b['east']} N{b['north']}")
    tile_paths = [download_tile(t) for t in tiles_for_bbox(**b)]

    datasets = [rasterio.open(p) for p in tile_paths]
    mosaic, transform = merge(datasets, bounds=(b["west"], b["south"], b["east"], b["north"]))

    # merge() fills pixels outside every source tile with 0 — this happens at the
    # crop edges because adjacent tiles' pixel grids are half-a-pixel offset.
    # Trim any fully-zero edge rows/cols and shift the transform to match.
    data = mosaic[0]
    top, bottom, left, right = 0, data.shape[0], 0, data.shape[1]
    while top < bottom and (data[top] == 0).all():
        top += 1
    while bottom > top and (data[bottom - 1] == 0).all():
        bottom -= 1
    while left < right and (data[:, left] == 0).all():
        left += 1
    while right > left and (data[:, right - 1] == 0).all():
        right -= 1
    if (top, left) != (0, 0) or (bottom, right) != data.shape:
        print(f"  trimmed empty merge edges: rows {top}..{bottom}, cols {left}..{right}")
        mosaic = mosaic[:, top:bottom, left:right]
        transform = transform * Affine.translation(left, top)

    profile = datasets[0].profile
    profile.update(
        height=mosaic.shape[1],
        width=mosaic.shape[2],
        transform=transform,
        driver="GTiff",
        compress="deflate",
    )
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(mosaic)
    for ds in datasets:
        ds.close()

    print(f"wrote {out_path} ({mosaic.shape[2]}x{mosaic.shape[1]} px)")


if __name__ == "__main__":
    main()
