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

## 2026-07-19 — "This is flat" correction (user verdict was right)

The user rejected the first showcase: flat, nowhere near the reference.
Root causes and fixes — REMEMBER THESE:

1. **True-scale geography is a pancake.** 11km wide + 1.5km relief =
   6:1. The reference is ~1.5:1. z_exaggeration 1.6 was timid; now
   2.6–3.2 per resort, targeting relief ≈ 40% of island width
   (x = 0.4 * width_m / relief_m as starting point).
2. **Slope thresholds must scale with exaggeration** — after raising
   exag, 45° rock threshold caught half the terrain (muddy brown
   mountains). Now rock 62°, outcrops 46° at ~3x exag.
3. **AgX was the greyness.** Standard view transform + art-directed
   gradient sky (deep zenith blue → pale horizon) + warm sun lamp
   (1.0, 0.86, 0.62) beats the physical Nishita sky for this style.
   Exposure -0.8. Nishita+AgX = washed cold porridge.
4. **Camera composition matters as much as geometry**: from above the
   rim looking into the bowl (offsets 0.78/0.78/0.62 of distance),
   per-resort camera_distance knob (elongated islands need 1.6).
5. **Underside dome**: sine z-profile + ring smoothing (3+4k iters)
   kills the cone/rib artefacts. depth_frac 0.15 with tall relief.
6. Snow material: roughness 0.95, specular 0.1 — else chrome sheen.

Process lesson: I judged "looks good" from too few angles and accepted
muted colour because each step was a small improvement on the last.
Compare against docs/style-reference.png directly, not against the
previous render.

## 2026-07-19 — The charm pass ("still scientific and dull" verdict)

Exaggeration alone didn't create charm. The five structural moves that
finally did (apply together — none works alone):

1. **Sculpt, don't sample**: heightmap 320px + smooth_m 120 +
   macro_m 900 + detail_gain 0.55 → ~soft clay landforms instead of
   200k DEM facets. This killed "scientific" more than anything.
2. **Snow that glows**: Subsurface Weight 0.3 (radius .10/.13/.18,
   scale .05) + AO node (dist 0.35) multiplying a blue crevice tint
   (0.62,0.68,0.85). Marshmallow form-definition.
3. **Pistes = pastel snow-tints** (e.g. easy (0.68,0.80,0.96)), wide
   ribbons (0.022) — engraved groomed runs, not GIS spaghetti.
4. **Chunky props**: trees 70m/density 1.0, chalets 30m, thick
   cables/pylons/chairs.
5. **Miniature camera**: DOF fstop 1.6 focused on centre + above-rim
   angle. Instant diorama feel.

Blender 5.x gotchas hit: Scene.node_tree gone (compositing_node_group
+ interface sockets), Glare options moved into input sockets ("Type"
= "Bloom"), GPU compositor crashes headless (set
render.compositor_device="CPU") and even then produced blank output —
bloom shelved for now (do it in post or EEVEE later).

Trade-off accepted: geographic micro-detail sacrificed for charm.
The [style] knobs can walk it back per-resort if a peak loses its
identity. Verify silhouettes of famous peaks still read.

## 2026-07-19 — The staging thesis (user: "still fundamentally flat")

THE core architectural insight of this project so far:

**Cartography vs caricature.** A cropped real heightfield — however
smoothed/exaggerated — is a field of semi-random relief with arbitrary
tilt and edge heights. It reads as a slab cut from a bigger world,
i.e. "scientific and flat". The reference image is a COMPOSED
caricature: identity elements (village low in front, distinct peaks
framing the back) re-staged on a domed pedestal. The DEM must become
the *reference* we extract identity from, not the surface itself.

Phase 1 shipped — staging transforms in stylise.py [staging]:
  a. flatten_base: subtract fitted base plane (kills regional tilt)
  b. peak_amp: per-landform amplification — each massif swells around
     its own base (1000m/2500m gaussian split) -> sculptural bumps
  c. edge_droop_m/droop_dist_m: terrain curls down toward the rim
     (distance transform + smoothstep) -> dome presentation
Proven on Val d'Isère: the slab is gone, massifs read as sculpture.

Phase 2 (next): landmark caricature — extract iconic peaks (DEM
prominence + OSM named summits), village anchor, main bowls; compress
boring expanses, enlarge identity zones; village-front camera
composition.
Phase 3 (if needed): full recomposition from peak/valley primitives.

Also user flagged: trees are bad (deal with later).

## 2026-07-19 — The vignette + CC0 assets (the breakthrough session)

User approved scope cut to village + 3 slopes, CC0 assets, gap-analysis
process. Result: first render in the same visual family as the
reference. What shipped:

- **Vignette bbox** (6.950-7.005, 45.418-45.462, ~4x5km): village,
  La Face de Bellevarde, Solaise. Tight scale makes objects readable —
  the single highest-impact change of the whole project.
- **Kenney Holiday Kit (CC0)** in assets/kenney/: tiered snowy pines,
  snow-capped cabin roofs, lanterns/benches/snowmen/sleds/snow-piles.
  GLBs reference Textures/colormap.png RELATIVE — must copy the
  Textures dir next to the glbs or everything renders magenta.
- **Chalets** = procedural timber box + kit snow-roof (modular wall
  assembly judged too risky blind; box+roof looks right).
- **Carved groomed runs**: stylise.py rasterises pistes into the
  heightmap (carve_depth_m/carve_width_m) + groom.npy mask -> shader
  brightens runs toward clean white. Colored ribbons OFF
  (piste_radius=0 skips them) — runs are terrain now, not overlay.
  GOTCHA: OSM ways extend past the bbox -> negative grid coords ->
  python negative-slice near-filled the whole mask. Bounds-check stamps.
- **Composed camera**: village anchor from built-mask centroid; camera
  beyond village looking inward (0.95/0.55 of target_size), DOF f/3.2
  focused on village; sun azimuth adaptive (over camera's shoulder).
- **Placement erosion**: erode(mask) keeps trees/buildings off the rim
  (they were spilling onto the underside).
- Catalog-render trick: render all kit pieces in a row FIRST to see
  dims/origins before writing placement code.
- Factory-settings scenes default to EEVEE which crashes headless —
  always set engine CYCLES in ad-hoc render scripts.

Remaining gaps vs reference (next session): warmer golden light;
clouds around island + sky; thicker cornice drips on the lip; village
readability (fewer, larger, better-arranged buildings + window glow);
tree colour variety; foreground props scale.
