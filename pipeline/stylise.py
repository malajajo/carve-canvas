#!/usr/bin/env python3
"""Stylise the raw heightmap: smooth noise, emphasise major forms.

Non-destructive — reads heightmap.npy, writes heightmap_stylised.npy
(the Blender stage prefers the stylised file when present).

The recipe, all tunable from the resort config [style] section:

1. Split the terrain into macro forms (heavy blur) and detail.
2. Smooth the detail layer to kill 30m DEM noise, and attenuate it,
   so major ridges/valleys dominate.
3. Apply a gamma curve to the normalised height: valleys deepen,
   peaks stand proud (the classic "heroic terrain" move).

Usage:
    python pipeline/stylise.py resorts/val-disere.toml
"""

import json
import sys
import tomllib
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    config_path = Path(sys.argv[1])
    config = tomllib.loads(config_path.read_text())
    s = config["style"]
    data_dir = ROOT / "data" / config["slug"]

    H = np.load(data_dir / "heightmap.npy").astype(np.float64)
    meta = json.loads((data_dir / "meta.json").read_text())
    px_m = meta["pixel_size_m"][0]  # metres per pixel

    # 1. Macro / detail split
    macro = gaussian_filter(H, sigma=s["macro_m"] / px_m)
    detail = H - macro

    # 2. De-noise and attenuate the detail layer
    detail = gaussian_filter(detail, sigma=s["smooth_m"] / px_m)
    stylised = macro + detail * s["detail_gain"]

    # 3. Valley-deepening gamma on normalised height
    lo, hi = stylised.min(), stylised.max()
    norm = (stylised - lo) / (hi - lo)
    stylised = lo + (norm ** s["valley_gamma"]) * (hi - lo)

    np.save(data_dir / "heightmap_stylised.npy", stylised.astype(np.float32))
    print(f"wrote {data_dir}/heightmap_stylised.npy "
          f"(relief {stylised.max() - stylised.min():.0f} m, "
          f"was {H.max() - H.min():.0f} m)")


if __name__ == "__main__":
    main()
