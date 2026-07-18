# Carve Canvas — Claude context

Transforms real ski resorts into stylised floating 3D dioramas. Read
`Project_Vision_Stylised_Ski_Resorts.md` for the vision. **Append
decisions and learnings to `MEMORY.md` as they happen.**

## Style north star

`docs/style-reference.png` — soft, toy-like floating snow island:
thick marshmallow snow with a dripping lip at the edges, chunky stylised
pines, wooden chalets, chairlifts with visible chairs, warm sunny
lighting, blue sky with clouds. Saturated, rounded, collectible.
Soft terrain is a feature, not a bug — but geography must stay
recognisable.

## Commands

Full pipeline for a resort (each stage cached by its output files):

```bash
.venv/bin/python pipeline/fetch_dem.py resorts/<slug>.toml
.venv/bin/python pipeline/make_heightmap.py resorts/<slug>.toml
.venv/bin/python pipeline/fetch_osm.py resorts/<slug>.toml
.venv/bin/python pipeline/make_boundary.py resorts/<slug>.toml
.venv/bin/python pipeline/fetch_landcover.py resorts/<slug>.toml
.venv/bin/python pipeline/make_features.py resorts/<slug>.toml
.venv/bin/python pipeline/stylise.py resorts/<slug>.toml
scripts/blender.sh --background --python blender/build_terrain.py -- resorts/<slug>.toml
```

Preview render (view it with Read to evaluate visually — always render
after visual changes, judgement by numbers alone is not enough):

```bash
scripts/blender.sh -b output/<slug>.blend -E CYCLES --python-expr \
  "import bpy; s=bpy.context.scene; s.cycles.samples=64; s.cycles.device='CPU'; \
   s.render.resolution_x=1280; s.render.resolution_y=960" \
  -o $PWD/output/view_top_ -f 1
```

## Architecture rules

- **Two-stage split is load-bearing**: geodata work (rasterio/shapely/scipy)
  happens in `pipeline/` plain-Python scripts writing npy/json to
  `data/<slug>/`. Blender scripts (`blender/`) may import ONLY numpy +
  bpy — Blender's bundled Python cannot see the venv.
- Per-resort knobs live in `resorts/<slug>.toml`. The universal palette
  (SNOW/FOREST/ROCK/GLACIER/piste colours) lives in code — every resort
  must feel like the same universe.
- Everything is procedural and re-runnable; no manual Blender edits.
  All scene objects are parented to the terrain object.

## Hard-won gotchas (do not rediscover)

- Blender viewport defaults to Solid mode → materials look grey. We set
  Material Preview on save; keep doing that.
- Packing generated image textures into .blend from headless Blender
  silently produces black textures. Use vertex colour attributes instead.
- Terrain relief is invisible under high sun — evaluate renders with the
  raking NW sun the build script sets up.
- rasterio merge() zero-fills edge columns where tile grids are offset
  half a pixel; UTM reprojection leaves nodata corner slivers. Both are
  trimmed automatically in the pipeline — expect similar dirt in any new
  geodata source.
- OSM pistes: ~13% are closed polygons (areas, not lines), many are
  <200 m connector stubs, and long runs are fragmented into many ways.
  make_features.py joins/filters these.
- The user is new to Blender — explain viewport/UI things when they
  come up, one concept at a time.

## Workflow

- Commit + push (github.com/malajajo/carve-canvas) at each milestone.
- data/, output/, tools/ are gitignored and regenerable.
- Iterate visually: change → build → render → Read the PNG → judge.
