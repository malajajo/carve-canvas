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
ASSET_DIR = ROOT / "assets" / "kenney"


def load_assets(names):
    """Import CC0 glb models; return dict name -> object (kept out of scene)."""
    lib = {}
    for name in names:
        path = ASSET_DIR / f"{name}.glb"
        if not path.exists():
            print(f"asset missing: {name}")
            continue
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(path))
        new = [o for o in bpy.data.objects if o not in before]
        for o in new:
            for c in list(o.users_collection):
                c.objects.unlink(o)
        meshes = [o for o in new if o.type == "MESH"]
        if meshes:
            lib[name] = meshes[0]
    return lib


def place(asset, loc, rot_z, scale, parent):
    o = asset.copy()
    o.location = loc
    o.rotation_euler = (0.0, 0.0, rot_z)
    o.scale = scale if isinstance(scale, tuple) else (scale, scale, scale)
    o.parent = parent
    bpy.context.collection.objects.link(o)
    return o


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
    groom_path = data_dir / "groom.npy"
    groom = np.load(groom_path) if groom_path.exists() else None
    return config, heightmap, mask, landcover, groom, meta


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


def build_mesh(config, H, mask, meta, landcover=None, groom=None):
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
        for _ in range(3 + 4 * k):
            ring_xy = 0.5 * ring_xy + 0.25 * (np.roll(ring_xy, 1, axis=0) + np.roll(ring_xy, -1, axis=0))
        centroid = ring_xy.mean(axis=0)
        s = np.cos(0.96 * tk * np.pi / 2) ** u["taper"]
        # Snow-lip bulge: the rim curls slightly outward before tapering in,
        # like a thick blanket of snow overhanging the edge
        s *= 1.0 + u["lip_bulge"] * np.sin(np.pi * min(tk / 0.3, 1.0))
        xy = centroid + (ring_xy - centroid) * s
        # Sine profile: drops fast at the rim, flattens at the bottom -> a
        # rounded dome instead of a cone
        zt = np.sin(tk * np.pi / 2)
        z = ring_z0 * (1 - zt) + (-depth) * zt

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

    # --- Groomed-piste mask as a vertex attribute for the shader ---
    if groom is not None:
        grid_n = ny * nx
        garr = np.zeros(len(used), dtype=np.float32)
        on_g = used < grid_n
        garr[on_g] = groom.ravel()[used[on_g]]
        g_attr = mesh.attributes.new(name="groom", type="FLOAT", domain="POINT")
        g_attr.data.foreach_set("value", garr)

    obj = bpy.data.objects.new(config["name"], mesh)
    bpy.context.collection.objects.link(obj)
    return obj


# The universal Carve Canvas palette (linear RGB) — every resort shares it
SNOW = (0.87, 0.90, 0.93, 1.0)
FOREST = (0.03, 0.09, 0.045, 1.0)
ROCK = (0.16, 0.14, 0.13, 1.0)
GLACIER = (0.62, 0.74, 0.88, 1.0)


def design_sun(village):
    """Warm key light over the camera's shoulder. Returns (euler, photon_dir)."""
    rx = 1.15
    if village is not None:
        vx, vy, _ = village
        vdir = np.array([vx, vy])
        vdir = vdir / (np.linalg.norm(vdir) + 1e-9)
        ang = 0.61
        ca, sa = np.cos(ang), np.sin(ang)
        lx = vdir[0] * ca - vdir[1] * sa
        ly = vdir[0] * sa + vdir[1] * ca
        rz = float(np.arctan2(lx, -ly))
    else:
        rz = 0.785
    d = (-np.sin(rz) * np.sin(rx), np.cos(rz) * np.sin(rx), -np.cos(rx))
    return (rx, 0.0, rz), d


def light_ramp_nodes(nt, node, sun_dir, stops):
    """dot(N, -sun) -> designed colour ramp: the core of the NPR look."""
    geo = node("ShaderNodeNewGeometry")
    dot = node("ShaderNodeVectorMath", operation="DOT_PRODUCT")
    dot.inputs[1].default_value = tuple(-c for c in sun_dir)
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    t = node("ShaderNodeMapRange")
    t.inputs["From Min"].default_value = -1.0
    t.inputs["From Max"].default_value = 1.0
    nt.links.new(dot.outputs["Value"], t.inputs["Value"])
    ramp = node("ShaderNodeValToRGB")
    elems = ramp.color_ramp.elements
    while len(elems) < len(stops):
        elems.new(0.5)
    for el, (pos, col) in zip(elems, stops):
        el.position = pos
        el.color = col
    nt.links.new(t.outputs["Result"], ramp.inputs["Fac"])
    return ramp.outputs["Color"]


SNOW_STOPS = [(0.35, (0.42, 0.54, 0.90, 1.0)),   # shadow: deep soft blue
              (0.62, (0.82, 0.88, 1.00, 1.0)),   # mid: pale blue-white
              (0.85, (1.00, 0.98, 0.92, 1.0))]   # lit: warm cream
ROCK_STOPS = [(0.30, (0.24, 0.22, 0.30, 1.0)),
              (0.60, (0.46, 0.40, 0.35, 1.0)),
              (0.85, (0.66, 0.57, 0.46, 1.0))]
ASSET_STOPS = [(0.30, (0.62, 0.68, 0.95, 1.0)),
               (0.60, (0.95, 0.95, 0.97, 1.0)),
               (0.85, (1.08, 1.04, 0.96, 1.0))]


def ramp_kit_materials(sun_dir):
    """Fold imported asset materials into the same designed light language."""
    for mat in bpy.data.materials:
        if not (mat.name.startswith("colormap") or mat.name == "chalet_wood"):
            continue
        nt = mat.node_tree

        def node(kind, **props):
            n = nt.nodes.new(kind)
            for k, v in props.items():
                setattr(n, k, v)
            return n

        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None or not bsdf.inputs["Base Color"].links:
            continue
        src_socket = bsdf.inputs["Base Color"].links[0].from_socket
        lr = light_ramp_nodes(nt, node, sun_dir, ASSET_STOPS)
        mult = node("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
        mult.inputs["Factor"].default_value = 1.0
        nt.links.new(src_socket, mult.inputs[6])
        nt.links.new(lr, mult.inputs[7])
        nt.links.new(mult.outputs[2], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 1.0
        bsdf.inputs["Emission Color"].default_value = (1, 1, 1, 1)
        if not bsdf.inputs["Emission Strength"].links:
            bsdf.inputs["Emission Strength"].default_value = 0.0
        # fake bounce: slight self-lift so shadows never go dead
        nt.links.new(mult.outputs[2], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 0.25


def add_material(obj, config, elev0_m, z_scale, landcover=None, sun_dir=(0.5, 0.5, -0.7)):
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
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Specular IOR Level"].default_value = 0.05
    bsdf.inputs["Subsurface Weight"].default_value = 0.12
    bsdf.inputs["Subsurface Radius"].default_value = (0.10, 0.13, 0.18)
    bsdf.inputs["Subsurface Scale"].default_value = 0.05

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

    snow_col = light_ramp_nodes(nt, node, sun_dir, SNOW_STOPS)
    rock_col = light_ramp_nodes(nt, node, sun_dir, ROCK_STOPS)

    glacier_fac = None
    if landcover is not None:
        # Real land-cover masks baked into vertex colours by build_mesh
        attr = node("ShaderNodeAttribute", attribute_name="landcover")
        sep = node("ShaderNodeSeparateColor")
        nt.links.new(attr.outputs["Color"], sep.inputs["Color"])

        forest_raw = map_range(roughen(sep.outputs["Green"]), 0.25, 0.55)
        forest_scale = node("ShaderNodeMath", operation="MULTIPLY")
        forest_scale.inputs[1].default_value = 0.3
        nt.links.new(forest_raw, forest_scale.inputs[0])
        forest_fac = forest_scale.outputs[0]
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

    # Wind-scoured ridgelines: convex crests (high pointiness) show partial
    # bare rock — characteristic alpine look. Cycles-only; harmless in EEVEE.
    ridge = map_range(geo.outputs["Pointiness"], 0.58, 0.70)
    ridge_soft = node("ShaderNodeMath", operation="MULTIPLY")
    ridge_soft.inputs[1].default_value = 0.55
    nt.links.new(ridge, ridge_soft.inputs[0])
    ridge_max = node("ShaderNodeMath", operation="MAXIMUM")
    nt.links.new(rockiness, ridge_max.inputs[0])
    nt.links.new(ridge_soft.outputs[0], ridge_max.inputs[1])
    rockiness = ridge_max.outputs[0]

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

    # snow ramp (glacier-tinted where applicable) -> forest -> rock ramp
    base = snow_col
    if glacier_fac is not None:
        mix_glacier = node("ShaderNodeMix", data_type="RGBA")
        nt.links.new(snow_col, mix_glacier.inputs[6])   # A
        mix_glacier.inputs[7].default_value = GLACIER   # B
        nt.links.new(glacier_fac, mix_glacier.inputs["Factor"])
        base = mix_glacier.outputs[2]

    mix_forest = node("ShaderNodeMix", data_type="RGBA")
    nt.links.new(base, mix_forest.inputs[6])
    mix_forest.inputs[7].default_value = FOREST  # B
    nt.links.new(forest_fac, mix_forest.inputs["Factor"])

    mix_rock = node("ShaderNodeMix", data_type="RGBA")
    nt.links.new(rock_col, mix_rock.inputs[7])  # B: designed rock ramp
    nt.links.new(mix_forest.outputs[2], mix_rock.inputs[6])
    nt.links.new(rockiness, mix_rock.inputs["Factor"])

    # Groomed runs: brighten toward clean corduroy white
    groom_attr = node("ShaderNodeAttribute", attribute_name="groom")
    groom_f = node("ShaderNodeMath", operation="MULTIPLY")
    groom_f.inputs[1].default_value = 0.8
    nt.links.new(groom_attr.outputs["Fac"], groom_f.inputs[0])
    mix_groom = node("ShaderNodeMix", data_type="RGBA")
    mix_groom.inputs[7].default_value = (0.72, 0.82, 0.97, 1.0)  # cool groomed tracks
    nt.links.new(mix_rock.outputs[2], mix_groom.inputs[6])
    nt.links.new(groom_f.outputs[0], mix_groom.inputs["Factor"])

    # Soft blue shading in crevices (ambient-occlusion multiply) gives the
    # marshmallow form-definition the flat sun cannot
    ao = node("ShaderNodeAmbientOcclusion")
    ao.inputs["Distance"].default_value = 0.35
    shade = node("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    shade.inputs["Factor"].default_value = 1.0
    shade.inputs[7].default_value = (0.62, 0.68, 0.85, 1.0)  # blue crevice tint
    nt.links.new(mix_groom.outputs[2], shade.inputs[6])
    final = node("ShaderNodeMix", data_type="RGBA")
    nt.links.new(ao.outputs["AO"], final.inputs["Factor"])
    nt.links.new(shade.outputs[2], final.inputs[6])   # occluded -> shaded
    nt.links.new(mix_groom.outputs[2], final.inputs[7])  # open -> full colour
    nt.links.new(final.outputs[2], bsdf.inputs["Base Color"])

    # Two-scale snow relief: soft drifts + fine surface texture
    drift = node("ShaderNodeTexNoise")
    drift.inputs["Scale"].default_value = 10.0
    drift.inputs["Detail"].default_value = 2.0
    fine = node("ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = 90.0
    fine.inputs["Detail"].default_value = 3.0
    bump1 = node("ShaderNodeBump")
    bump1.inputs["Strength"].default_value = 0.18
    nt.links.new(drift.outputs["Fac"], bump1.inputs["Height"])
    bump2 = node("ShaderNodeBump")
    bump2.inputs["Strength"].default_value = 0.10
    nt.links.new(fine.outputs["Fac"], bump2.inputs["Height"])
    nt.links.new(bump1.outputs["Normal"], bump2.inputs["Normal"])
    nt.links.new(bump2.outputs["Normal"], bsdf.inputs["Normal"])
    # fake bounce: designed colour glows softly so shadows never go dead
    nt.links.new(final.outputs[2], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 0.22
    obj.data.materials.append(mat)


WOOD = (0.30, 0.17, 0.09, 1.0)  # timber matched to the kit palette


class Placer:
    """Spatial-hash min-distance placement so objects never interpenetrate."""

    def __init__(self, cell=0.1):
        self.cell = cell
        self.grid = {}

    def try_place(self, x, y, r):
        kx, ky = int(x // self.cell), int(y // self.cell)
        reach = int((r + 0.08) // self.cell) + 1
        for i in range(kx - reach, kx + reach + 1):
            for j in range(ky - reach, ky + reach + 1):
                for px, py, pr in self.grid.get((i, j), ()):
                    if (px - x) ** 2 + (py - y) ** 2 < (pr + r) ** 2:
                        return False
        self.grid.setdefault((kx, ky), []).append((x, y, r))
        return True


def add_variation_nodes(mat, hue_amount=0.06, val_amount=0.3):
    """Per-instance colour variation: every copy differs slightly."""
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or not bsdf.inputs["Base Color"].links:
        return
    src_socket = bsdf.inputs["Base Color"].links[0].from_socket
    info = nt.nodes.new("ShaderNodeObjectInfo")
    hue = nt.nodes.new("ShaderNodeMapRange")
    hue.inputs["To Min"].default_value = 0.5 - hue_amount / 2
    hue.inputs["To Max"].default_value = 0.5 + hue_amount / 2
    nt.links.new(info.outputs["Random"], hue.inputs["Value"])
    val = nt.nodes.new("ShaderNodeMapRange")
    val.inputs["To Min"].default_value = 1.0 - val_amount / 2
    val.inputs["To Max"].default_value = 1.0 + val_amount / 2
    nt.links.new(info.outputs["Random"], val.inputs["Value"])
    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    nt.links.new(hue.outputs["Result"], hsv.inputs["Hue"])
    nt.links.new(val.outputs["Result"], hsv.inputs["Value"])
    nt.links.new(src_socket, hsv.inputs["Color"])
    nt.links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])


def erode(mask, n=3):
    """Shrink a boolean mask by n cells (keeps placements off the rim)."""
    m = mask.copy()
    for _ in range(n):
        m = (m & np.roll(m, 1, 0) & np.roll(m, -1, 0)
               & np.roll(m, 1, 1) & np.roll(m, -1, 1))
    return m


def sample_grid(rng, prob):
    """Bernoulli-sample grid cells; returns (rows, cols) of hits."""
    return np.nonzero(rng.random(prob.shape) < np.clip(prob, 0, 1))


def scatter_coords(rng, rows, cols, H, mask, config, meta):
    """Jittered scene xyz for grid cell hits."""
    t = config["terrain"]
    ny, nx = H.shape
    extent_x, extent_y = meta["extent_m"]
    scale = t["target_size"] / max(extent_x, extent_y)
    dx = extent_x / (nx - 1) * scale
    dy = extent_y / (ny - 1) * scale
    Z = (H - H[mask].min()) * scale * t["z_exaggeration"]
    jit = rng.random((len(rows), 2))
    tc, tr = cols + jit[:, 0], rows + jit[:, 1]
    x = (tc - (nx - 1) / 2) * dx
    y = ((ny - 1) / 2 - tr) * dy
    c0 = np.clip(tc, 0, nx - 1.001).astype(int)
    r0 = np.clip(tr, 0, ny - 1.001).astype(int)
    fc, fr = tc - c0, tr - r0
    z = (Z[r0, c0] * (1 - fc) * (1 - fr) + Z[r0, c0 + 1] * fc * (1 - fr)
         + Z[r0 + 1, c0] * (1 - fc) * fr + Z[r0 + 1, c0 + 1] * fc * fr)
    return x, y, z, scale


def add_trees(config, H, mask, landcover, groom, meta, assets, parent):
    """Kenney snowy pines scattered by the real forest mask."""
    if landcover is None or "trees" not in config:
        return
    tcfg = config["trees"]
    forest = landcover["forest"] * erode(mask)
    keep_out = landcover["built"] > 0.15
    if groom is not None:
        keep_out |= groom > 0.35

    rng = np.random.default_rng(42)
    p = forest * tcfg["density"]
    p[keep_out] = 0
    rows, cols = sample_grid(rng, p)
    if len(rows) == 0:
        return
    x, y, z, scale = scatter_coords(rng, rows, cols, H, mask, config, meta)

    variants = [assets[n] for n in ("tree-snow-a", "tree-snow-b", "tree-snow-c") if n in assets]
    plain = assets.get("tree")
    h_units = tcfg["height_m"] * scale * config["terrain"]["z_exaggeration"]
    placer = config["_placer"]
    n_placed = 0
    for i in range(len(rows)):
        snowy = rng.random() < tcfg["snowy_share"]
        asset = variants[int(rng.integers(len(variants)))] if (snowy or plain is None) else plain
        s = h_units * rng.uniform(0.55, 1.5) / asset.dimensions.z
        canopy_r = 0.26 * asset.dimensions.x * s
        if not placer.try_place(x[i], y[i], canopy_r):
            continue
        sz = s * rng.uniform(0.85, 1.3)  # height jitter independent of girth
        place(asset, (x[i], y[i], z[i] - 0.02 * s), rng.uniform(0, 6.283),
              (s, s, sz), parent)
        n_placed += 1
    print(f"trees: {n_placed} kit pines placed ({len(rows) - n_placed} culled by spacing)")


def add_chalets(config, H, mask, landcover, meta, assets, parent):
    """Chalets: timber box bodies + Kenney thick-snow roofs."""
    if landcover is None:
        return
    built = landcover["built"] * erode(mask, 4)
    rng = np.random.default_rng(7)
    rows, cols = sample_grid(rng, built * 1.1)
    if len(rows) == 0:
        return
    x, y, z, scale = scatter_coords(rng, rows, cols, H, mask, config, meta)

    # body prototype: simple box, no bottom
    mat_wood = bpy.data.materials.new("chalet_wood")
    bsdf = mat_wood.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = WOOD
    bsdf.inputs["Roughness"].default_value = 0.85
    bm_verts = [(-0.5, -0.55, 0), (0.5, -0.55, 0), (0.5, 0.55, 0), (-0.5, 0.55, 0),
                (-0.5, -0.55, 0.62), (0.5, -0.55, 0.62), (0.5, 0.55, 0.62), (-0.5, 0.55, 0.62)]
    bm_faces = [[0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7], [4, 5, 6, 7]]
    body_mesh = bpy.data.meshes.new("chalet_body")
    body_mesh.from_pydata(bm_verts, [], bm_faces)
    body_mesh.materials.append(mat_wood)
    body_proto = bpy.data.objects.new("chalet_body", body_mesh)

    roofs = [assets[n] for n in ("cabin-roof-snow", "cabin-roof-snow-chimney",
                                 "cabin-roof-snow-point") if n in assets]
    size_units = config["chalets"]["size_m"] * scale

    # Dominant village orientation: buildings align to the contour of the
    # slope (perpendicular to downhill), with jitter — not uniform random
    gy, gx = np.gradient(H)
    r0, c0 = int(np.mean(rows)), int(np.mean(cols))
    downhill = np.arctan2(-gy[r0, c0], gx[r0, c0])
    contour = downhill + np.pi / 2

    placer = config["_placer"]
    n_placed = 0
    for i in range(len(rows)):
        s = size_units * rng.uniform(0.75, 1.8)
        if not placer.try_place(x[i], y[i], 0.75 * s):
            continue
        rot = contour + rng.uniform(-0.3, 0.3) + (np.pi / 2 if rng.random() < 0.25 else 0)
        base_z = z[i] - 0.12 * s
        aspect = rng.uniform(0.85, 1.25)
        place(body_proto, (x[i], y[i], base_z), rot, (s, s * aspect, s), parent)
        roof = roofs[int(rng.integers(len(roofs)))]
        rs = s * 1.05 / roof.dimensions.x
        place(roof, (x[i], y[i], base_z + 0.62 * s), rot,
              (rs, rs * aspect, rs), parent)
        n_placed += 1
    print(f"chalets: {n_placed} buildings placed ({len(rows) - n_placed} culled by spacing)")


def add_props(config, H, mask, landcover, meta, assets, parent):
    """Village life: lanterns, benches, snowmen, sleds, snow piles."""
    if landcover is None:
        return
    built = landcover["built"] * erode(mask, 4)
    rng = np.random.default_rng(99)
    rows, cols = sample_grid(rng, built * 0.7)
    if len(rows) == 0:
        return
    x, y, z, scale = scatter_coords(rng, rows, cols, H, mask, config, meta)
    kinds = [("lantern", 3.2), ("bench", 1.4), ("snowman", 2.0), ("sled", 1.0), ("snow-pile", 1.2)]
    kinds = [(assets[n], h) for n, h in kinds if n in assets]
    zx = config["terrain"]["z_exaggeration"]
    placer = config["_placer"]
    n = 0
    for i in range(len(rows)):
        asset, h_m = kinds[int(rng.integers(len(kinds)))]
        s = h_m * scale * zx / asset.dimensions.z
        if not placer.try_place(x[i], y[i], 0.5 * asset.dimensions.x * s):
            continue
        place(asset, (x[i], y[i], z[i] - 0.05 * s), rng.uniform(0, 6.283), s, parent)
        n += 1
    print(f"props: {n} village props placed")


# Pastel snow-tints: groomed runs read as white swaths with a hint of
# difficulty colour, not GIS overlay lines
PISTE_COLORS = {
    "novice": (0.72, 0.88, 0.76, 1.0),
    "easy": (0.68, 0.80, 0.96, 1.0),
    "intermediate": (0.95, 0.72, 0.70, 1.0),
    "advanced": (0.55, 0.56, 0.62, 1.0),
    "expert": (0.55, 0.56, 0.62, 1.0),
    "freeride": (0.95, 0.85, 0.62, 1.0),
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
    for piste in (feats["pistes"] if r_piste > 0 else []):
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
    cable = new_curve_obj("lift_cables", STEEL, 0.006)
    spacing_px = f["pylon_spacing_m"] / meta["pixel_size_m"][0]
    py_verts, py_faces = [], []
    w = 0.009  # pylon half-width

    def add_box(x0, y0, z0, z1, hw):
        base = len(py_verts)
        for zz in (z0, z1):
            py_verts.extend([(x0 - hw, y0 - hw, zz), (x0 + hw, y0 - hw, zz),
                             (x0 + hw, y0 + hw, zz), (x0 - hw, y0 + hw, zz)])
        for k in range(4):
            k2 = (k + 1) % 4
            py_faces.append([base + k, base + k2, base + 4 + k2, base + 4 + k])
        py_faces.append([base + 4, base + 5, base + 6, base + 7])  # roof
    n_lifts = 0
    ch_verts, ch_faces = [], []
    chair_spacing = 130 / meta["pixel_size_m"][0]  # a chair every ~130m
    cw = 0.006  # chair half-size

    def add_chair(x0, y0, ztop):
        base = len(ch_verts)
        # hanger bar + seat box hanging under the cable
        for zz in (ztop, ztop - 0.010):
            ch_verts.extend([(x0 - cw, y0 - cw, zz), (x0 + cw, y0 - cw, zz),
                             (x0 + cw, y0 + cw, zz), (x0 - cw, y0 + cw, zz)])
        for k in range(4):
            k2 = (k + 1) % 4
            ch_faces.append([base + k, base + k2, base + 4 + k2, base + 4 + k])
        ch_faces.append([base + 4, base + 5, base + 6, base + 7])

    for lift in feats["lifts"]:
        pts = densify(lift["points"], step=1.5)
        cols, rows = pts[:, 0], pts[:, 1]
        ok = inside(cols, rows)
        for a, b in runs_inside(ok):
            x, y = to_scene_xy(cols[a:b], rows[a:b])
            ground = sample_z(cols[a:b], rows[a:b])
            add_spline(cable, x, y, ground + clearance)
            n_lifts += 1
            seg = np.hypot(np.diff(cols[a:b]), np.diff(rows[a:b]))
            arc = np.concatenate([[0], np.cumsum(seg)])
            for d in np.arange(chair_spacing / 3, arc[-1], chair_spacing):
                i = int(np.searchsorted(arc, d))
                add_chair(x[i], y[i], ground[i] + clearance)
            # pylons at regular arc-length intervals
            for d in np.arange(spacing_px / 2, arc[-1], spacing_px):
                i = int(np.searchsorted(arc, d))
                add_box(x[i], y[i], ground[i] - 0.01, ground[i] + clearance, w)
            # stations: chunky sheds at both cable ends
            for i in (0, len(x) - 1):
                add_box(x[i], y[i], ground[i] - 0.01, ground[i] + clearance * 0.75, 0.012)

    if py_verts:
        pm = bpy.data.meshes.new("pylons")
        pm.from_pydata(py_verts, [], py_faces)
        pm.validate()
        pm.materials.append(flat_material("pylon_mat", STEEL))
        obj = bpy.data.objects.new("lift_pylons", pm)
        obj.parent = parent
        bpy.context.collection.objects.link(obj)

    if ch_verts:
        cm = bpy.data.meshes.new("chairs")
        cm.from_pydata(ch_verts, [], ch_faces)
        cm.validate()
        cm.materials.append(flat_material("chair_mat", STEEL))
        obj = bpy.data.objects.new("lift_chairs", cm)
        obj.parent = parent
        bpy.context.collection.objects.link(obj)

    print(f"features: {n_pistes} piste segments, {n_lifts} lift cables, "
          f"{len(py_verts) // 8} pylons, {len(ch_verts) // 8} chairs")


def add_lighting_and_camera(obj, cam_dist=1.15, village=None, target_size=10.0, sun_euler=(1.15, 0.0, 0.785)):
    from math import radians

    # Art-directed sky: saturated blue gradient backdrop (physical Nishita
    # sky came out grey and cold through AgX), plus a warm sun lamp and
    # cool-blue ambient fill — the stylised alpine postcard look.
    world = bpy.data.worlds.new("world")
    nt = world.node_tree
    bg = nt.nodes["Background"]
    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(coord.outputs["Generated"], sep.inputs[0])
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = -0.05   # view-direction Z
    mr.inputs["From Max"].default_value = 0.65
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.55, 0.75, 0.98, 1.0)  # pale warm horizon
    ramp.color_ramp.elements[1].color = (0.10, 0.32, 0.78, 1.0)  # saturated zenith
    nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
    # camera rays see the pretty gradient; diffuse rays see a soft cool fill
    lp = nt.nodes.new("ShaderNodeLightPath")
    fill = nt.nodes.new("ShaderNodeRGB")
    fill.outputs[0].default_value = (0.80, 0.85, 0.97, 1.0)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Factor"])
    nt.links.new(fill.outputs[0], mix.inputs[6])
    nt.links.new(ramp.outputs["Color"], mix.inputs[7])
    nt.links.new(mix.outputs[2], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.75
    bpy.context.scene.world = world

    # Warm golden sun, raking angle proven by earlier A/B tests
    # Soft warm sun: real cast shadows for depth, but the ramps carry the
    # tonal design — the lamp is support, not the star
    sun_data = bpy.data.lights.new("sun", type="SUN")
    sun_data.energy = 2.4
    sun_data.color = (1.0, 0.88, 0.70)
    sun_data.angle = 0.15
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = sun_euler
    bpy.context.collection.objects.link(sun)

    # Standard transform keeps the saturated postcard colours (AgX greyed
    # everything out — chosen by A/B renders)
    vs = bpy.context.scene.view_settings
    vs.view_transform = "Standard"
    vs.exposure = -0.8


    # Frame the camera on the object's bounding box from a 3/4 angle,
    # or — when a village anchor exists — compose village-front like a
    # postcard: village in the foreground, slopes rising behind.
    bb = np.array(obj.bound_box)
    center = bb.mean(axis=0)
    d = float(max(bb.max(axis=0) - bb.min(axis=0))) * cam_dist

    target = bpy.data.objects.new("CameraTarget", None)
    target.location = center
    bpy.context.collection.objects.link(target)

    cam_data = bpy.data.cameras.new("camera")
    cam_data.dof.use_dof = True            # tilt-shift miniature feel
    cam_data.dof.aperture_fstop = 3.2
    cam = bpy.data.objects.new("Camera", cam_data)
    if village is not None:
        vx, vy, vz = village
        vdir = np.array([vx, vy])
        vdir = vdir / (np.linalg.norm(vdir) + 1e-9)
        cam.location = (vx + vdir[0] * 0.95 * target_size,
                        vy + vdir[1] * 0.95 * target_size,
                        vz + 0.55 * target_size)
        target.location = (vx - vdir[0] * 0.30 * target_size,
                           vy - vdir[1] * 0.30 * target_size,
                           vz + 0.02 * target_size)
    else:
        cam.location = (center[0] + d * 0.78, center[1] - d * 0.78, center[2] + d * 0.62)
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    cam_data.dof.focus_object = target
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam


def main():
    config, heightmap, mask, landcover, groom, meta = load_inputs()

    # Start from an empty scene
    bpy.ops.wm.read_factory_settings(use_empty=True)

    obj = build_mesh(config, heightmap, mask, meta, landcover, groom)

    t = config["terrain"]
    z_scale = t["target_size"] / max(meta["extent_m"]) * t["z_exaggeration"]

    # Village anchor first: composition AND light design both hang off it
    village = None
    if landcover is not None and (landcover["built"] * mask).sum() > 0:
        b = landcover["built"] * mask
        ny, nx = heightmap.shape
        cy = float((b * np.arange(ny)[:, None]).sum() / b.sum())
        cx = float((b * np.arange(nx)[None, :]).sum() / b.sum())
        scl = t["target_size"] / max(meta["extent_m"])
        dxx = meta["extent_m"][0] / (nx - 1) * scl
        dyy = meta["extent_m"][1] / (ny - 1) * scl
        vz = (heightmap[int(cy), int(cx)] - heightmap[mask].min()) * scl * t["z_exaggeration"]
        village = ((cx - (nx - 1) / 2) * dxx, ((ny - 1) / 2 - cy) * dyy, vz)
    sun_euler, sun_dir = design_sun(village)

    add_material(obj, config, float(heightmap[mask].min()), z_scale, landcover, sun_dir)
    add_features(config, heightmap, mask, meta, parent=obj)

    assets = load_assets(["tree-snow-a", "tree-snow-b", "tree-snow-c", "tree",
                          "cabin-roof-snow", "cabin-roof-snow-chimney", "cabin-roof-snow-point",
                          "lantern", "bench", "snowman", "sled", "snow-pile"])
    # Per-instance colour variation on every kit material
    for mat in bpy.data.materials:
        if mat.name.startswith("colormap"):
            add_variation_nodes(mat)
    config["_placer"] = Placer()
    add_chalets(config, heightmap, mask, landcover, meta, assets, parent=obj)
    add_trees(config, heightmap, mask, landcover, groom, meta, assets, parent=obj)
    add_props(config, heightmap, mask, landcover, meta, assets, parent=obj)
    for mat in bpy.data.materials:
        if mat.name == "chalet_wood":
            add_variation_nodes(mat, hue_amount=0.04, val_amount=0.5)
    ramp_kit_materials(sun_dir)

    add_lighting_and_camera(obj, config["terrain"].get("camera_distance", 1.15),
                            village, t["target_size"], sun_euler)

    # Open looking through the composed camera, in Material Preview with
    # OUR sun and sky (not Blender's default studio HDRI) — so what the
    # user sees on open matches the renders as closely as the viewport can
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.type = "MATERIAL"
                        space.shading.use_scene_world = True
                        space.shading.use_scene_lights = True
                        space.region_3d.view_perspective = "CAMERA"

    out = ROOT / "output" / f"{config['slug']}.blend"
    out.parent.mkdir(exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
