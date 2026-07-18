"""Build a floating terrain block in Blender from a prepared heightmap.

Run headless:
    blender --background --python blender/build_terrain.py -- resorts/val-disere.toml

Reads data/<slug>/heightmap.npy + meta.json, builds a watertight mesh
(terrain top + vertical cliff walls + flat bottom), adds a simple
material, sun and camera, and saves output/<slug>.blend.
"""

import json
import sys
import tomllib
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def load_inputs():
    config_path = Path(sys.argv[sys.argv.index("--") + 1])
    config = tomllib.loads(config_path.read_text())
    data_dir = ROOT / "data" / config["slug"]
    stylised = data_dir / "heightmap_stylised.npy"
    heightmap = np.load(stylised if stylised.exists() else data_dir / "heightmap.npy")
    meta = json.loads((data_dir / "meta.json").read_text())
    mask_path = data_dir / "mask.npy"
    mask = np.load(mask_path) if mask_path.exists() else np.ones(heightmap.shape, dtype=bool)
    lc_path = data_dir / "landcover.npz"
    landcover = dict(np.load(lc_path)) if lc_path.exists() else None
    return config, heightmap, mask, landcover, meta


def boundary_loop(faces):
    """Ordered vertex loop around the outside of a set of faces.

    Boundary edges appear in exactly one face; interior edges in two.
    Chains boundary edges into loops and returns the longest.
    """
    edge_count = {}
    for f in faces:
        for k in range(4):
            e = tuple(sorted((f[k], f[(k + 1) % 4])))
            edge_count[e] = edge_count.get(e, 0) + 1

    adjacency = {}
    for (a, b), n in edge_count.items():
        if n == 1:
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)

    loops, remaining = [], set(adjacency)
    while remaining:
        start = next(iter(remaining))
        loop, prev, cur = [start], None, start
        while True:
            nxt = [v for v in adjacency[cur] if v != prev]
            if not nxt or nxt[0] == start:
                break
            prev, cur = cur, nxt[0]
            loop.append(cur)
        remaining -= set(loop)
        loops.append(loop)

    loops.sort(key=len, reverse=True)
    if len(loops) > 1:
        print(f"warning: {len(loops)} boundary loops found, keeping the longest "
              f"({[len(l) for l in loops]}) — mask may have stray islands")
    return loops[0]


def build_mesh(config, H, mask, meta, landcover=None):
    t = config["terrain"]
    ny, nx = H.shape
    extent_x, extent_y = meta["extent_m"]

    # Scale real-world metres down to Blender units
    scale = t["target_size"] / max(extent_x, extent_y)
    dx = extent_x / (nx - 1) * scale
    dy = extent_y / (ny - 1) * scale

    # Relative elevation (0 = lowest point inside the boundary), exaggerated
    Z = (H - H[mask].min()) * scale * t["z_exaggeration"]

    # --- Top surface vertices (row 0 of H is north => +Y) ---
    xs = (np.arange(nx) - (nx - 1) / 2) * dx
    ys = ((ny - 1) / 2 - np.arange(ny)) * dy
    top = np.empty((ny, nx, 3), dtype=np.float64)
    top[..., 0] = xs[None, :]
    top[..., 1] = ys[:, None]
    top[..., 2] = Z
    verts = top.reshape(-1, 3)

    # --- Top surface quads: only where all four corners are inside the mask ---
    face_ok = mask[:-1, :-1] & mask[1:, :-1] & mask[1:, 1:] & mask[:-1, 1:]

    # Keep only the largest connected blob of faces — stray mask islands
    # would otherwise produce floating slivers with no proper skirt.
    from collections import deque
    comp = np.full(face_ok.shape, -1, dtype=np.int32)
    sizes = []
    for si, sj in zip(*np.nonzero(face_ok)):
        if comp[si, sj] != -1:
            continue
        label = len(sizes)
        queue, comp[si, sj], size = deque([(si, sj)]), label, 0
        while queue:
            ci, cj = queue.popleft()
            size += 1
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ni, nj = ci + di, cj + dj
                if (0 <= ni < face_ok.shape[0] and 0 <= nj < face_ok.shape[1]
                        and face_ok[ni, nj] and comp[ni, nj] == -1):
                    comp[ni, nj] = label
                    queue.append((ni, nj))
        sizes.append(size)
    if len(sizes) > 1:
        print(f"dropping {len(sizes) - 1} stray mask island(s): {sorted(sizes)[:-1]}")
        face_ok &= comp == int(np.argmax(sizes))

    fi, fj = np.nonzero(face_ok)
    v00 = fi * nx + fj
    faces = np.stack([v00, v00 + nx, v00 + nx + 1, v00 + 1], axis=1).tolist()

    # --- Boundary loop around the masked region ---
    boundary = boundary_loop(faces)

    # Smooth the loop: the rasterised mask edge stair-steps at pixel size,
    # which reads as vertical fluting on the cliff walls. A few rounds of
    # neighbour-averaging the boundary vertices fixes it.
    loop_pts = verts[boundary].copy()
    for _ in range(4):
        loop_pts = 0.5 * loop_pts + 0.25 * (np.roll(loop_pts, 1, axis=0) + np.roll(loop_pts, -1, axis=0))
    verts[boundary] = loop_pts

    # --- Floating-island underside: rings taper inward to a low point ---
    # Each ring is the previous ring smoothed (rounds off the lobes as we
    # descend) and scaled toward its centroid, with z blending from the
    # terrain edge down to the island's lowest point.
    u = config["underside"]
    depth = u["depth_frac"] * t["target_size"]
    n_rings = u["rings"]
    nb = len(boundary)

    top_ring = verts[boundary]
    ring_xy = top_ring[:, :2].copy()
    ring_z0 = top_ring[:, 2].copy()

    lip_weight = {}  # vertex id -> snow-lip strength (1 = fully snowy wall)
    prev_ids = list(boundary)
    for k in range(1, n_rings + 1):
        tk = k / n_rings
        # progressively stronger smoothing merges the lobes near the bottom
        for _ in range(1 + 2 * k):
            ring_xy = 0.5 * ring_xy + 0.25 * (np.roll(ring_xy, 1, axis=0) + np.roll(ring_xy, -1, axis=0))
        centroid = ring_xy.mean(axis=0)
        s = np.cos(0.96 * tk * np.pi / 2) ** u["taper"]
        # Snow-lip bulge: the rim curls slightly outward before tapering in,
        # like a thick blanket of snow overhanging the edge
        s *= 1.0 + u["lip_bulge"] * np.sin(np.pi * min(tk / 0.3, 1.0))
        xy = centroid + (ring_xy - centroid) * s
        z = ring_z0 * (1 - tk) + (-depth) * tk

        ring_start = len(verts)
        verts = np.vstack([verts, np.column_stack([xy, z])])
        ring_ids = list(range(ring_start, ring_start + nb))
        lw = max(0.0, 1.0 - (k - 1) / max(u["lip_rings"], 1))
        for vid in ring_ids:
            lip_weight[vid] = lw
        for i in range(nb):
            i2 = (i + 1) % nb
            faces.append([prev_ids[i], prev_ids[i2], ring_ids[i2], ring_ids[i]])
        prev_ids = ring_ids

    # Close with a fan to a single low point
    tip = len(verts)
    tip_xy = verts[prev_ids, :2].mean(axis=0)
    verts = np.vstack([verts, [[tip_xy[0], tip_xy[1], -depth * 1.04]]])
    for i in range(nb):
        faces.append([prev_ids[i], prev_ids[(i + 1) % nb], tip])

    # --- Compact: drop vertices outside the boundary that no face uses ---
    used = np.unique(np.concatenate([np.asarray(f) for f in faces]))
    remap = np.full(len(verts), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    verts = verts[used]
    faces = [[int(remap[v]) for v in f] for f in faces]

    mesh = bpy.data.meshes.new("terrain")
    mesh.from_pydata(verts.tolist(), [], faces)
    mesh.validate()
    mesh.update()

    # Consistent outward normals regardless of loop winding
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()

    for poly in mesh.polygons:
        poly.use_smooth = True

    # --- Bake land-cover masks into a vertex colour attribute ---
    # R=rock G=forest B=glacier A=built. Top-surface vertices map 1:1 to
    # heightmap cells; underside ring vertices stay 0 (they read as rock
    # via the slope term anyway).
    if landcover is not None:
        grid_n = ny * nx
        col = np.zeros((len(used), 4), dtype=np.float32)
        on_grid = used < grid_n
        gi = used[on_grid]
        for channel, name in enumerate(("rock", "forest", "glacier", "built")):
            col[on_grid, channel] = landcover[name].ravel()[gi]
        attr = mesh.color_attributes.new(name="landcover", type="FLOAT_COLOR", domain="POINT")
        attr.data.foreach_set("color", col.ravel())

    # --- Snow-lip weights: upper skirt rings read as snow, not rock ---
    lip = np.zeros(len(used), dtype=np.float32)
    for vid, w in lip_weight.items():
        idx = remap[vid]
        if idx >= 0:
            lip[idx] = w
    lip_attr = mesh.attributes.new(name="snow_lip", type="FLOAT", domain="POINT")
    lip_attr.data.foreach_set("value", lip)

    obj = bpy.data.objects.new(config["name"], mesh)
    bpy.context.collection.objects.link(obj)
    return obj


# The universal Carve Canvas palette (linear RGB) — every resort shares it
SNOW = (0.87, 0.90, 0.93, 1.0)
FOREST = (0.03, 0.09, 0.045, 1.0)
ROCK = (0.16, 0.14, 0.13, 1.0)
GLACIER = (0.62, 0.74, 0.88, 1.0)


def add_material(obj, config, elev0_m, z_scale, landcover=None):
    """Zone shader. With landcover masks: real forest/rock/glacier placement,
    noise only roughens edges. Without: procedural altitude/slope fallback.

    elev0_m: real-world elevation that sits at scene z=0
    z_scale: scene units per metre of elevation
    map_extent: scene-unit (x, y) size of the heightmap footprint
    """
    from math import cos, radians

    m = config["materials"]
    mat = bpy.data.materials.new("terrain_zones")
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.8

    def node(kind, **props):
        n = nt.nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def map_range(value_out, lo, hi, to_lo=0.0, to_hi=1.0):
        mr = node("ShaderNodeMapRange")
        mr.inputs["From Min"].default_value = lo
        mr.inputs["From Max"].default_value = hi
        mr.inputs["To Min"].default_value = to_lo
        mr.inputs["To Max"].default_value = to_hi
        nt.links.new(value_out, mr.inputs["Value"])
        return mr.outputs["Result"]

    geo = node("ShaderNodeNewGeometry")
    nrm = node("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Normal"], nrm.inputs[0])

    # Steepness: 0 = gentle, 1 = cliff (normal Z falls as slope grows)
    steep = map_range(
        nrm.outputs["Z"],
        cos(radians(m["rock_slope_deg"] + m["rock_blend_deg"])),
        cos(radians(m["rock_slope_deg"] - m["rock_blend_deg"])),
        to_lo=1.0, to_hi=0.0,
    )
    # Moderate-slope gate for outcrops / bare patches
    gate = map_range(
        nrm.outputs["Z"],
        cos(radians(m["outcrop_slope_deg"] + 8)),
        cos(radians(m["outcrop_slope_deg"] - 8)),
        to_lo=1.0, to_hi=0.0,
    )

    # Shared edge-roughening noise: +/-0.15 around zero
    edge_noise = node("ShaderNodeTexNoise")
    edge_noise.inputs["Scale"].default_value = 8.0
    edge_noise.inputs["Detail"].default_value = 4.0
    edge_c = node("ShaderNodeMath", operation="SUBTRACT")
    edge_c.inputs[1].default_value = 0.5
    nt.links.new(edge_noise.outputs["Fac"], edge_c.inputs[0])
    edge = node("ShaderNodeMath", operation="MULTIPLY")
    edge.inputs[1].default_value = 0.3
    nt.links.new(edge_c.outputs[0], edge.inputs[0])

    def roughen(sock):
        add = node("ShaderNodeMath", operation="ADD")
        nt.links.new(sock, add.inputs[0])
        nt.links.new(edge.outputs[0], add.inputs[1])
        return add.outputs[0]

    glacier_fac = None
    if landcover is not None:
        # Real land-cover masks baked into vertex colours by build_mesh
        attr = node("ShaderNodeAttribute", attribute_name="landcover")
        sep = node("ShaderNodeSeparateColor")
        nt.links.new(attr.outputs["Color"], sep.inputs["Color"])

        forest_fac = map_range(roughen(sep.outputs["Green"]), 0.25, 0.55)
        glacier_fac = map_range(roughen(sep.outputs["Blue"]), 0.2, 0.6)

        # Winter logic: mapped bare ground shows as rock only on slopes that
        # won't hold snow; genuine cliffs are always rock
        bare = map_range(roughen(sep.outputs["Red"]), 0.25, 0.5)
        bare_sloped = node("ShaderNodeMath", operation="MULTIPLY")
        nt.links.new(bare, bare_sloped.inputs[0])
        nt.links.new(gate, bare_sloped.inputs[1])
        rockiness = node("ShaderNodeMath", operation="MAXIMUM")
        nt.links.new(steep, rockiness.inputs[0])
        nt.links.new(bare_sloped.outputs[0], rockiness.inputs[1])
        rockiness = rockiness.outputs[0]
    else:
        # Procedural fallback: treeline altitude + noise outcrops
        pos = node("ShaderNodeSeparateXYZ")
        nt.links.new(geo.outputs["Position"], pos.inputs[0])
        tl_z = (m["treeline_m"] - elev0_m) * z_scale
        tl_blend = m["treeline_blend_m"] * z_scale
        forest_fac = map_range(roughen(pos.outputs["Z"]), tl_z - tl_blend, tl_z + tl_blend,
                               to_lo=1.0, to_hi=0.0)

        patches_noise = node("ShaderNodeTexNoise")
        patches_noise.inputs["Scale"].default_value = m["outcrop_scale"]
        patches_noise.inputs["Detail"].default_value = 4.0
        lo = 0.78 - 0.38 * m["outcrop_amount"]
        patches = map_range(patches_noise.outputs["Fac"], lo, lo + 0.04)
        outcrop = node("ShaderNodeMath", operation="MULTIPLY")
        nt.links.new(gate, outcrop.inputs[0])
        nt.links.new(patches, outcrop.inputs[1])
        rockiness = node("ShaderNodeMath", operation="MAXIMUM")
        nt.links.new(steep, rockiness.inputs[0])
        nt.links.new(outcrop.outputs[0], rockiness.inputs[1])
        rockiness = rockiness.outputs[0]

    # Snow lip: suppress rock on the upper skirt so the rim reads as a
    # thick snow blanket folding over the edge
    lip_attr = node("ShaderNodeAttribute", attribute_name="snow_lip")
    lip_sub = node("ShaderNodeMath", operation="SUBTRACT", use_clamp=True)
    nt.links.new(rockiness, lip_sub.inputs[0])
    lip_scaled = node("ShaderNodeMath", operation="MULTIPLY")
    lip_scaled.inputs[1].default_value = 1.5
    nt.links.new(lip_attr.outputs["Fac"], lip_scaled.inputs[0])
    nt.links.new(lip_scaled.outputs[0], lip_sub.inputs[1])
    rockiness = lip_sub.outputs[0]

    # snow (glacier-tinted where applicable) -> forest -> rock
    base = SNOW
    if glacier_fac is not None:
        mix_glacier = node("ShaderNodeMix", data_type="RGBA")
        mix_glacier.inputs[6].default_value = SNOW     # A
        mix_glacier.inputs[7].default_value = GLACIER  # B
        nt.links.new(glacier_fac, mix_glacier.inputs["Factor"])
        base = mix_glacier.outputs[2]

    mix_forest = node("ShaderNodeMix", data_type="RGBA")
    if isinstance(base, tuple):
        mix_forest.inputs[6].default_value = base
    else:
        nt.links.new(base, mix_forest.inputs[6])
    mix_forest.inputs[7].default_value = FOREST  # B
    nt.links.new(forest_fac, mix_forest.inputs["Factor"])

    mix_rock = node("ShaderNodeMix", data_type="RGBA")
    mix_rock.inputs[7].default_value = ROCK  # B
    nt.links.new(mix_forest.outputs[2], mix_rock.inputs[6])
    nt.links.new(rockiness, mix_rock.inputs["Factor"])

    nt.links.new(mix_rock.outputs[2], bsdf.inputs["Base Color"])
    obj.data.materials.append(mat)


# European piste colour convention (linear RGB)
PISTE_COLORS = {
    "novice": (0.10, 0.55, 0.15, 1.0),
    "easy": (0.08, 0.25, 0.75, 1.0),
    "intermediate": (0.75, 0.06, 0.06, 1.0),
    "advanced": (0.01, 0.01, 0.01, 1.0),
    "expert": (0.01, 0.01, 0.01, 1.0),
    "freeride": (0.90, 0.60, 0.05, 1.0),
}
STEEL = (0.06, 0.06, 0.07, 1.0)


def add_features(config, H, mask, meta, parent=None):
    """Drape pistes as coloured ribbons and lifts as cables with pylons.

    All created objects are parented to `parent` (the terrain) so the
    whole diorama moves/rotates/scales as one model.
    """
    data_dir = ROOT / "data" / config["slug"]
    fpath = data_dir / "features.json"
    if not fpath.exists():
        print("no features.json — skipping pistes/lifts")
        return
    feats = json.loads(fpath.read_text())

    t, f = config["terrain"], config["features"]
    ny, nx = H.shape
    extent_x, extent_y = meta["extent_m"]
    scale = t["target_size"] / max(extent_x, extent_y)
    dx = extent_x / (nx - 1) * scale
    dy = extent_y / (ny - 1) * scale
    Z = (H - H[mask].min()) * scale * t["z_exaggeration"]

    def sample_z(cols, rows):
        c = np.clip(cols, 0, nx - 1.001)
        r = np.clip(rows, 0, ny - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        return (Z[r0, c0] * (1 - fc) * (1 - fr) + Z[r0, c0 + 1] * fc * (1 - fr)
                + Z[r0 + 1, c0] * (1 - fc) * fr + Z[r0 + 1, c0 + 1] * fc * fr)

    def to_scene_xy(cols, rows):
        return (cols - (nx - 1) / 2) * dx, ((ny - 1) / 2 - rows) * dy

    def inside(cols, rows):
        c = np.round(cols).astype(int)
        r = np.round(rows).astype(int)
        ok = (c >= 0) & (c < nx) & (r >= 0) & (r < ny)
        res = np.zeros(len(cols), dtype=bool)
        res[ok] = mask[r[ok], c[ok]]
        return res

    def densify(pts, step):
        pts = np.asarray(pts, dtype=np.float64)
        out = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            n = max(1, int(np.hypot(*(b - a)) / step))
            for i in range(1, n + 1):
                out.append(a + (b - a) * (i / n))
        return np.array(out)

    def smooth_polyline(pts, iters=3):
        """Endpoint-preserving relaxation — swoopy stylised lines, not GPS jags."""
        p = np.asarray(pts, dtype=np.float64).copy()
        for _ in range(iters):
            if len(p) < 3:
                break
            p[1:-1] = 0.5 * p[1:-1] + 0.25 * (p[:-2] + p[2:])
        return p

    def runs_inside(ok):
        """Contiguous index runs where ok is True."""
        runs, start = [], None
        for i, v in enumerate(ok):
            if v and start is None:
                start = i
            elif not v and start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, len(ok)))
        return [r for r in runs if r[1] - r[0] >= 2]

    def flat_material(name, color):
        mat = bpy.data.materials.new(name)
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.6
        return mat

    def new_curve_obj(name, color, radius):
        cd = bpy.data.curves.new(name, "CURVE")
        cd.dimensions = "3D"
        cd.bevel_depth = radius
        cd.bevel_resolution = 2
        cd.materials.append(flat_material(f"{name}_mat", color))
        obj = bpy.data.objects.new(name, cd)
        obj.parent = parent
        bpy.context.collection.objects.link(obj)
        return cd

    def add_spline(cd, x, y, z):
        sp = cd.splines.new("POLY")
        sp.points.add(len(x) - 1)
        sp.points.foreach_set("co", np.column_stack([x, y, z, np.ones(len(x))]).ravel())

    # --- Pistes: one curve object per difficulty, terrain-hugging ribbons ---
    r_piste = f["piste_radius"]
    by_difficulty = {}
    n_pistes = 0
    for piste in feats["pistes"]:
        diff = piste["difficulty"] if piste["difficulty"] in PISTE_COLORS else "intermediate"
        pts = densify(smooth_polyline(piste["points"]), step=0.6)
        cols, rows = pts[:, 0], pts[:, 1]
        ok = inside(cols, rows)
        for a, b in runs_inside(ok):
            if diff not in by_difficulty:
                by_difficulty[diff] = new_curve_obj(
                    f"pistes_{diff}", PISTE_COLORS[diff], r_piste)
            x, y = to_scene_xy(cols[a:b], rows[a:b])
            z = sample_z(cols[a:b], rows[a:b]) + r_piste * 0.4
            add_spline(by_difficulty[diff], x, y, z)
            n_pistes += 1

    # --- Lifts: draped cables + pylon prisms ---
    clearance = f["lift_clearance"]
    cable = new_curve_obj("lift_cables", STEEL, 0.004)
    spacing_px = f["pylon_spacing_m"] / meta["pixel_size_m"][0]
    py_verts, py_faces = [], []
    w = 0.006  # pylon half-width
    n_lifts = 0
    for lift in feats["lifts"]:
        pts = densify(lift["points"], step=1.5)
        cols, rows = pts[:, 0], pts[:, 1]
        ok = inside(cols, rows)
        for a, b in runs_inside(ok):
            x, y = to_scene_xy(cols[a:b], rows[a:b])
            ground = sample_z(cols[a:b], rows[a:b])
            add_spline(cable, x, y, ground + clearance)
            n_lifts += 1
            # pylons at regular arc-length intervals
            seg = np.hypot(np.diff(cols[a:b]), np.diff(rows[a:b]))
            arc = np.concatenate([[0], np.cumsum(seg)])
            for d in np.arange(spacing_px / 2, arc[-1], spacing_px):
                i = int(np.searchsorted(arc, d))
                base = len(py_verts)
                x0, y0 = x[i], y[i]
                z0, z1 = ground[i] - 0.01, ground[i] + clearance
                for zz in (z0, z1):
                    py_verts += [(x0 - w, y0 - w, zz), (x0 + w, y0 - w, zz),
                                 (x0 + w, y0 + w, zz), (x0 - w, y0 + w, zz)]
                for k in range(4):
                    k2 = (k + 1) % 4
                    py_faces.append([base + k, base + k2, base + 4 + k2, base + 4 + k])

    if py_verts:
        pm = bpy.data.meshes.new("pylons")
        pm.from_pydata(py_verts, [], py_faces)
        pm.validate()
        pm.materials.append(flat_material("pylon_mat", STEEL))
        obj = bpy.data.objects.new("lift_pylons", pm)
        obj.parent = parent
        bpy.context.collection.objects.link(obj)

    print(f"features: {n_pistes} piste segments, {n_lifts} lift cables, "
          f"{len(py_verts) // 8} pylons")


def add_lighting_and_camera(obj):
    from math import radians

    # Physically-based sky (Nishita): blue sky, warm low sun, alpine light.
    # The sky's own sun disc is the light source — no separate sun lamp.
    world = bpy.data.worlds.new("world")
    nt = world.node_tree
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sun_elevation = radians(22)   # low = raking shadows + warm tone
    sky.sun_rotation = radians(0)     # chosen by A/B render test — best relief
    sky.altitude = 2000               # metres — thinner alpine atmosphere
    nt.links.new(sky.outputs["Color"], nt.nodes["Background"].inputs["Color"])
    bpy.context.scene.world = world

    # The physical sky is bright — pull exposure down and add contrast
    vs = bpy.context.scene.view_settings
    vs.exposure = -1.3
    try:
        vs.look = "AgX - Punchy"
    except TypeError:
        pass  # look name differs across versions; default is fine

    # Frame the camera on the object's bounding box from a 3/4 angle
    bb = np.array(obj.bound_box)
    center = bb.mean(axis=0)
    d = float(max(bb.max(axis=0) - bb.min(axis=0))) * 1.1

    target = bpy.data.objects.new("CameraTarget", None)
    target.location = center
    bpy.context.collection.objects.link(target)

    cam_data = bpy.data.cameras.new("camera")
    cam = bpy.data.objects.new("Camera", cam_data)
    cam.location = (center[0] + d, center[1] - d, center[2] + d * 0.8)
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam


def main():
    config, heightmap, mask, landcover, meta = load_inputs()

    # Start from an empty scene
    bpy.ops.wm.read_factory_settings(use_empty=True)

    obj = build_mesh(config, heightmap, mask, meta, landcover)

    t = config["terrain"]
    z_scale = t["target_size"] / max(meta["extent_m"]) * t["z_exaggeration"]
    add_material(obj, config, float(heightmap[mask].min()), z_scale, landcover)
    add_features(config, heightmap, mask, meta, parent=obj)
    add_lighting_and_camera(obj)

    # Open in Material Preview so colours show immediately (Solid mode is
    # Blender's default and renders everything grey)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.type = "MATERIAL"

    out = ROOT / "output" / f"{config['slug']}.blend"
    out.parent.mkdir(exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
