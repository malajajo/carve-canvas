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

    # 4. Diorama staging — from "slab of terrain" to composed dome.
    #    A real cropped heightfield has arbitrary tilt and edge heights,
    #    which reads as a flat scientific slab. Stage it instead.
    if "staging" in config:
        st = config["staging"]
        mask_path = data_dir / "mask.npy"
        mask = np.load(mask_path) if mask_path.exists() else np.ones(H.shape, bool)

        # 4a. Remove the fitted base plane (regional tilt) inside the boundary
        yy, xx = np.mgrid[0:H.shape[0], 0:H.shape[1]]
        A = np.column_stack([xx[mask], yy[mask], np.ones(mask.sum())])
        coef, *_ = np.linalg.lstsq(A, stylised[mask], rcond=None)
        plane = coef[0] * xx + coef[1] * yy + coef[2]
        stylised = stylised - st["flatten_base"] * (plane - plane[mask].min())

        # 4b. Per-landform amplification: each massif swells around its own
        #     base level -> distinct sculptural bumps, not a uniform stretch
        base_deep = gaussian_filter(stylised, sigma=2500 / px_m)
        mass = gaussian_filter(stylised, sigma=1000 / px_m) - base_deep
        mass_n = np.clip(mass / max(mass.max(), 1), 0, 1)
        stylised = base_deep + (stylised - base_deep) * (1 + st["peak_amp"] * mass_n)

        # 4c. Edge droop: terrain curls down toward the rim -> the island
        #     presents itself as a dome, the classic diorama silhouette
        from scipy.ndimage import distance_transform_edt
        dist_m = distance_transform_edt(mask) * px_m
        f = np.clip(dist_m / st["droop_dist_m"], 0, 1)
        f = f * f * (3 - 2 * f)  # smoothstep
        stylised = stylised - st["edge_droop_m"] * (1 - f)

    # 5. Carve groomed pistes into the snow as gentle grooves, and save the
    #    groom mask so the shader can brighten the corduroy
    feat_path = data_dir / "features.json"
    fcfg = config.get("features", {})
    if "carve_depth_m" in fcfg and feat_path.exists():
        feats = json.loads(feat_path.read_text())
        groom = np.zeros_like(stylised)
        half_w_px = max(fcfg["carve_width_m"] / px_m / 2, 0.6)
        rad = int(np.ceil(half_w_px))
        for piste in feats["pistes"]:
            pts = np.asarray(piste["points"])
            for a, b in zip(pts[:-1], pts[1:]):
                n = max(1, int(np.hypot(*(b - a)) * 2))
                for i in range(n + 1):
                    c, r = a + (b - a) * (i / n)
                    ri, ci = int(round(r)), int(round(c))
                    if not (0 <= ri < groom.shape[0] and 0 <= ci < groom.shape[1]):
                        continue  # piste vertices outside the bbox grid
                    groom[max(ri - rad, 0):min(ri + rad + 1, groom.shape[0]),
                          max(ci - rad, 0):min(ci + rad + 1, groom.shape[1])] = 1
        groom = np.clip(gaussian_filter(groom, sigma=half_w_px) * 1.6, 0, 1)
        stylised = stylised - fcfg["carve_depth_m"] * groom
        np.save(data_dir / "groom.npy", groom.astype(np.float32))
        print(f"carved {len(feats['pistes'])} groomed runs "
              f"({groom.mean() * 100:.0f}% of area)")

    np.save(data_dir / "heightmap_stylised.npy", stylised.astype(np.float32))
    print(f"wrote {data_dir}/heightmap_stylised.npy "
          f"(relief {stylised.max() - stylised.min():.0f} m, "
          f"was {H.max() - H.min():.0f} m)")


if __name__ == "__main__":
    main()
