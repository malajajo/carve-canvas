#!/usr/bin/env python3
"""Fetch ski infrastructure (downhill pistes + lifts) from OpenStreetMap.

Queries the Overpass API for the resort's bounding box and caches the raw
response at data/<slug>/osm.json. This data drives boundary generation now
and piste/lift modelling later.

Usage:
    python pipeline/fetch_osm.py resorts/val-disere.toml
"""

import json
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY = """
[out:json][timeout:90];
(
  way["piste:type"~"downhill|snow_park"]({s},{w},{n},{e});
  way["aerialway"]({s},{w},{n},{e});
);
out geom;
"""


def main() -> None:
    config_path = Path(sys.argv[1])
    config = tomllib.loads(config_path.read_text())
    b = config["bbox"]

    query = QUERY.format(s=b["south"], w=b["west"], n=b["north"], e=b["east"])
    req = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": "carve-canvas terrain pipeline"},
    )
    print(f"[{config['name']}] querying Overpass for pistes + lifts ...")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)

    out = ROOT / "data" / config["slug"] / "osm.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data))

    pistes = sum(1 for el in data["elements"] if "piste:type" in el.get("tags", {}))
    lifts = sum(1 for el in data["elements"] if "aerialway" in el.get("tags", {}))
    print(f"wrote {out} ({pistes} piste ways, {lifts} lift ways)")


if __name__ == "__main__":
    main()
