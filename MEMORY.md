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

## 2026-07-18 — Autonomous session (populate + repeatability)

- **Chamonix proves repeatability**: zero pipeline changes needed for
  resort #2. Only fix: Overpass mirror fallback (overpass-api.de 504s
  routinely; kumi.systems + private.coffee as backups, retry loop).
- **Trees**: pine_template() = 4-sided trunk + 3 stacked 7-sided cones,
  ~2x real scale (48m knob), placed per-cell by forest fraction
  (density 0.8), pistes+buildings keep-out, 35% snow-dusted, single
  merged mesh. VDI 2.9k trees, Chamonix 9.2k.
- **Chalets**: gabled template, built-fraction sampling (extra building
  in dense cells), snow roofs + timber walls, base sunk 25% into slope.
  ~2.5k buildings reads as believable town fabric at near-real scale.
- **Chairs**: 8-vert prisms hung every 130m along cables. Merged mesh.
- **All object generators follow the same pattern**: numpy template ×
  instance transforms → single mesh → 1-2 flat materials → parent to
  terrain. Fast to build, fast to render, easy to restyle.
- **Showcase renders** in docs/ (val-disere.png, chamonix.png,
  val-disere-village.png), embedded in README.
- **Lift stations shipped**: chunky sheds at both ends of every cable
  (shared add_box helper with pylons).
- **Ridge scouring shipped**: Cycles Pointiness > 0.58 blends partial
  rock on convex crests (×0.55, applied before snow-lip suppression so
  the rim stays snowy). EEVEE ignores it gracefully.
- **Whistler proves "any mountain on Earth"**: W-longitude naming,
  UTM 10N, >50°N Copernicus band, two-lobe island — zero code changes.
  59% forest → 19k trees; 807 OSM piste ways (NA mapping is dense).
  Three resorts in README gallery now.
- **Known gaps / next ideas**: piste ribbons overlap village roofs
  slightly; nursery-slope area patches instead of line spaghetti;
  clouds below island; night/dusk lighting variant; cable sag;
  southern-hemisphere resort (S-latitude tile naming untested);
  villages could use varied building types (church, hotel blocks).
