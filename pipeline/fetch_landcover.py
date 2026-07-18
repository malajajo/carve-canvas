#!/usr/bin/env python3
"""Fetch ESA WorldCover land-cover masks for the resort area.

WorldCover is a global 10m land-cover map (2021, v200) served as
cloud-optimised GeoTIFFs on AWS — we stream only the window covering the
resort bbox, then reproject each class onto the heightmap grid as
fractional cover (0..1 per cell).

Outputs:
    data/<slug>/landcover.npz       one float32 array per class
    data/<slug>/landcover_preview.png  colour-coded sanity check

Usage:
    python pipeline/fetch_landcover.py resorts/val-disere.toml
"""

import json
import math
import sys
import tomllib
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parent.parent
TILE_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
            "v200/2021/map/ESA_WorldCover_10m_2021_v200_{lat}{lon}_Map.tif")

CLASSES = {
    "forest": 10, "shrub": 20, "grass": 30, "built": 50,
    "rock": 60, "glacier": 70, "moss": 100,
}
PREVIEW_COLOURS = {  # class -> RGB for the sanity-check image
    "forest": (20, 90, 40), "shrub": (110, 130, 40), "grass": (150, 180, 90),
    "built": (200, 60, 60), "rock": (120, 110, 100), "glacier": (190, 220, 250),
    "moss": (180, 170, 120),
}


def tile_names(west, south, east, north):
    """WorldCover tiles are 3x3 degrees, named by SW corner."""
    for lat in range(math.floor(south / 3) * 3, math.floor(north / 3) * 3 + 1, 3):
        for lon in range(math.floor(west / 3) * 3, math.floor(east / 3) * 3 + 1, 3):
            ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
            yield ns, ew


def main() -> None:
    config_path = Path(sys.argv[1])
    config = tomllib.loads(config_path.read_text())
    b = config["bbox"]
    data_dir = ROOT / "data" / config["slug"]
    meta = json.loads((data_dir / "meta.json").read_text())

    dst_transform = Affine(*meta["transform"])
    dst_shape = (meta["height_px"], meta["width_px"])
    masks = {name: np.zeros(dst_shape, dtype=np.float32) for name in CLASSES}

    for ns, ew in tile_names(**b):
        url = TILE_URL.format(lat=ns, lon=ew)
        print(f"[{config['name']}] streaming window from {ns}{ew} ...")
        with rasterio.open(url) as src:
            window = from_bounds(b["west"], b["south"], b["east"], b["north"],
                                 src.transform).round_offsets().round_lengths()
            data = src.read(1, window=window)
            win_transform = src.window_transform(window)
            if data.size == 0:
                continue
            for name, value in CLASSES.items():
                frac = np.zeros(dst_shape, dtype=np.float32)
                reproject(
                    source=(data == value).astype(np.float32),
                    destination=frac,
                    src_transform=win_transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=meta["crs"],
                    resampling=Resampling.average,
                )
                np.maximum(masks[name], frac, out=masks[name])

    np.savez_compressed(data_dir / "landcover.npz", **masks)

    # Colour-coded preview: strongest class wins per pixel
    from PIL import Image
    stack = np.stack([masks[n] for n in CLASSES])
    winner = stack.argmax(axis=0)
    strength = stack.max(axis=0)
    preview = np.full((*dst_shape, 3), 235, dtype=np.uint8)  # default: snow-white
    for idx, name in enumerate(CLASSES):
        sel = (winner == idx) & (strength > 0.3)
        preview[sel] = PREVIEW_COLOURS[name]
    Image.fromarray(preview).save(data_dir / "landcover_preview.png")

    coverage = {n: f"{m.mean() * 100:.0f}%" for n, m in masks.items()}
    print(f"wrote {data_dir}/landcover.npz  coverage: {coverage}")


if __name__ == "__main__":
    main()
