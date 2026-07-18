# Memory — decisions & learnings log

Append-only. Newest at the bottom. Keep entries short.

## 2026-07-18 — Foundation sessions

- **Stack**: Copernicus GLO-30 DEM (AWS, no auth) + OSM Overpass +
  ESA WorldCover 10m (AWS COG, streamed windows) + Blender 5.2 headless
  on this Linux box (`scripts/blender.sh`; user views .blend on Windows).
- **Boundary = buffered hull of OSM pistes+lifts** (600m buffer, closed,
  simplified). Replaced the dumb bbox rectangle. Covers 48% of DEM for
  Val d'Isère → 60 km² island. This is "Risk #2" v0.1 and it works.
- **Underside**: 14 tapering rings, each smoothed harder than the last,
  converging to a tip. Rectangle-slab look eliminated.
- **Stylisation** (`stylise.py`): macro/detail split + detail attenuation
  + valley gamma. First attempt over-smoothed ("marshmallow couch") —
  but the style reference later showed soft is largely right. Current:
  smooth_m=35, macro_m=400, detail_gain=0.8, valley_gamma=1.2.
- **Materials**: WorldCover masks (rock/forest/glacier/built) baked as
  vertex colours, shader mixes universal palette; winter logic = mapped
  bare ground shows as rock only on >28° slopes, >45° always rock.
  Noise only roughens mask edges. Procedural fallback exists for
  resorts without landcover data.
- **Pistes/lifts**: difficulty-coloured curve ribbons draped on terrain
  (European colour convention), lift cables at fixed clearance + pylon
  prisms every 350m. Parented to terrain after user hit the
  "rotated terrain, pistes stayed" surprise.
- **User's style reference saved** at docs/style-reference.png. Gap
  list vs current state: snow lip/overhang at edges, 3D trees, chalets,
  chairs on cables, sky world + clouds, warmer lighting, snowier
  underside (reference bottom is snow/cloud-like, ours is rock).
- **Piste spaghetti diagnosis**: 246 ways = 33 closed areas + 72 stubs
  <200m + heavy fragmentation (174 named ways forming far fewer real
  pistes).
- **Piste cleanup shipped**: drop closed ways, chain same-name+difficulty
  ways with touching endpoints (1.5px tol), drop chains <250m
  (min_piste_len_m), endpoint-preserving smoothing on ribbons.
  246 → 119 ribbons. Village beginner zone still dense — future idea:
  render nursery/converging zones as groomed area patches, not lines.
- **Style calibration from user**: reference image = mood board only.
  Aim midpoint between it and reality — near-true scale, not hyper-toy.
- **Sky + light + snow lip shipped**: Nishita sky (elev 22°, rotation 0°
  — chosen by 3-way A/B render; 90° is flat, 225° moody), exposure -1.3,
  AgX Punchy look. Snow lip = outward bulge on upper skirt rings +
  "snow_lip" float attribute suppressing rock in shader. Underside now
  reads all-snow (taper walls are <45°) — revisit if too floaty; option:
  steeper rock threshold or AO-darkened belly.
- **Lesson**: physically-correct sky needs exposure management — always
  re-check exposure after lighting changes, and A/B test sun azimuth
  with cheap 16-sample renders instead of guessing.
