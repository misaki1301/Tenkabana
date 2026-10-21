"""Build a Song for Prism toon preview scene as a .blend.

    blender -b -P build_template.py -- [--idol amana] [--export DIR] [--out FILE]
                                       [--render PNG] [--engine eevee|cycles]

--idol     name or id; defaults to the export folder's idol, else Amana
--export   a Tenkabana export folder. Uses prism_extract.py's blender/ output
           when present (exact parts and materials), else meshes/ + textures/.
           Without it, a stand-in mannequin in the idol's palette is built so the
           shading can be judged before any game assets are involved.
--out      .blend to write (default: <idol>_template.blend next to this script)
--render   also render a still to this path
--engine   render engine for the still (default eevee; cycles works headless
           without a GPU and gives the same image, the shading is all emission)

The add-on is embedded in the .blend as a registered text block, so the
Tenkabana sidebar panel appears when the file is opened with scripts allowed.
"""

import math
import os
import random
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tenkabana_toon as tkb  # noqa: E402

# Stand-in palettes (sRGB). Approximations for the preview mannequin only.
PALETTES = {
    14: {  # Amana: warm light-brown hair with a side tail, pink outfit
        "hair": (0.87, 0.62, 0.47), "skin": (1.0, 0.89, 0.83), "eye": (0.55, 0.32, 0.52),
        "dress": (0.96, 0.47, 0.62), "trim": (1.0, 0.97, 0.97), "side_tail": True,
    },
}
DEFAULT_PALETTE = {
    "hair": (0.35, 0.30, 0.38), "skin": (1.0, 0.89, 0.83), "eye": (0.30, 0.35, 0.60),
    "dress": (0.55, 0.62, 0.85), "trim": (1.0, 1.0, 1.0), "side_tail": False,
}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {"idol": None, "export": None, "out": None, "render": None, "engine": "eevee"}
    it = iter(argv)
    for a in it:
        key = a.lstrip("-")
        if key in opts:
            opts[key] = next(it, None)
    return opts


def resolve_idol(token, export=None):
    if token is None:
        # The export folder's name says whose it is; the stand-in defaults to Amana.
        return (tkb.idol_from_path(export) if export else None) or 14
    if token.isdigit():
        return int(token)
    for i, n in tkb.IDOLS.items():
        if n == token.lower():
            return i
    raise SystemExit(f"unknown idol: {token}")


# ---------------------------------------------------------------------------
# Generated textures (no game art involved)
# ---------------------------------------------------------------------------

def _image(name, w, h, fill):
    img = bpy.data.images.new(name, w, h, alpha=True)
    px = [0.0] * (w * h * 4)
    for y in range(h):
        for x in range(w):
            r, g, b, a = fill(x / (w - 1), y / (h - 1))
            i = (y * w + x) * 4
            px[i:i + 4] = (r, g, b, a)
    img.pixels.foreach_set(px)
    img.pack()
    return img


def stand_in_textures(name, pal):
    rnd = random.Random(14)
    hair, skin, eye = pal["hair"], pal["skin"], pal["eye"]
    dress, trim = pal["dress"], pal["trim"]

    strands = [rnd.random() for _ in range(128)]

    def hair_col(u, v):
        s = 0.9 + 0.1 * strands[int(u * 127)]
        return (*(c * s for c in hair), 1.0)

    def hair_noise(u, v):
        n = strands[int(u * 127)]
        return (n, n, n, 1.0)

    def face_col(u, v):
        # Blender's UV sphere faces -Y at u=0.25, v=0.5 (the equator).
        for cx in (0.215, 0.285):
            du, dv = (u - cx) / 0.022, (v - 0.47) / 0.06
            d = du * du + dv * dv
            if d < 1.0:
                if d < 0.25 and dv > -0.1:
                    return (1.0, 1.0, 1.0, 1.0) if dv > 0.35 else (*(c * 0.6 for c in eye), 1.0)
                return (*eye, 1.0) if dv < 0.6 else (0.15, 0.1, 0.12, 1.0)
        return (*skin, 1.0)

    def dress_col(u, v):
        return (*trim, 1.0) if v < 0.08 or 0.92 < v else (*dress, 1.0)

    def dress_smask(u, v):
        # Painted shadow: folds every 1/12 of the way round.
        fold = 0.55 if (u * 12) % 1.0 < 0.18 and v < 0.85 else 1.0
        return (fold, fold, fold, 1.0)

    def dress_spmap(u, v):
        k = 1.0 if v < 0.08 or 0.92 < v else 0.0
        return (k, k, k, 1.0)

    def flat(c):
        return lambda u, v: (*c, 1.0)

    return {
        "hair": {"col": _image(f"{name}_hair_col", 128, 32, hair_col),
                 "rnoise": _image(f"{name}_hair_rnoise", 128, 32, hair_noise)},
        "face": {"col": _image(f"{name}_face_col", 512, 256, face_col)},
        "skin": {"col": _image(f"{name}_body_col", 8, 8, flat(skin))},
        "dress": {"col": _image(f"{name}_dress_col", 128, 128, dress_col),
                  "smask": _image(f"{name}_dress_smask", 128, 128, dress_smask),
                  "spmap": _image(f"{name}_dress_spmap", 128, 128, dress_spmap)},
    }


# ---------------------------------------------------------------------------
# Stand-in mannequin, roughly 1.55 m, Z-up, facing -Y
# ---------------------------------------------------------------------------

def _prim(op, name, mat, **kw):
    op(**kw)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return obj


def build_stand_in(idol, coll):
    pal = PALETTES.get(idol, DEFAULT_PALETTE)
    name = tkb.IDOLS.get(idol, "idol")
    tex = stand_in_textures(name, pal)
    mats = {part: tkb.build_toon_material(f"TKB {name}_{part}", maps, part)
            for part, maps in tex.items()}

    m = bpy.ops.mesh
    parts = [
        _prim(m.primitive_uv_sphere_add, "Face", mats["face"],
              radius=0.105, location=(0, 0, 1.46), segments=48, ring_count=24),
        _prim(m.primitive_uv_sphere_add, "FrontHair", mats["hair"],
              radius=0.118, location=(0, 0.028, 1.485), segments=48, ring_count=24),
        _prim(m.primitive_cone_add, "BackHair", mats["hair"], vertices=32,
              radius1=0.17, radius2=0.10, depth=0.5, location=(0, 0.07, 1.22)),
        _prim(m.primitive_cylinder_add, "Neck", mats["skin"], vertices=16,
              radius=0.035, depth=0.12, location=(0, 0, 1.33)),
        _prim(m.primitive_cylinder_add, "dress_top", mats["dress"], vertices=32,
              radius=0.125, depth=0.36, location=(0, 0, 1.11)),
        _prim(m.primitive_cone_add, "dress_skirt", mats["dress"], vertices=48,
              radius1=0.31, radius2=0.12, depth=0.36, location=(0, 0, 0.8)),
    ]
    parts[1].scale = (1.0, 1.0, 1.04)
    parts[2].scale = (1.0, 0.45, 1.0)
    parts[4].scale = (1.0, 0.72, 1.0)
    for side in (-1, 1):
        parts.append(_prim(m.primitive_cylinder_add, f"Leg{'LR'[side > 0]}", mats["skin"],
                           vertices=16, radius=0.05, depth=0.68, location=(side * 0.08, 0, 0.34)))
        arm = _prim(m.primitive_cylinder_add, f"Arm{'LR'[side > 0]}", mats["skin"],
                    vertices=16, radius=0.035, depth=0.56, location=(side * 0.19, 0, 1.0))
        arm.rotation_euler = (0, side * math.radians(12), 0)
        parts.append(arm)
    if pal.get("side_tail"):
        tail = _prim(m.primitive_cone_add, "SideTail", mats["hair"], vertices=24,
                     radius1=0.03, radius2=0.075, depth=0.42, location=(0.17, 0.04, 1.3))
        tail.rotation_euler = (0, math.radians(-10), 0)
        parts.append(tail)

    for o in parts:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
        tkb.add_outline(o)
    return parts


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------

def build_stage(coll, target_z=0.82):
    floor_mat = tkb.build_toon_material("TKB floor", {}, "skin")
    prism = floor_mat.node_tree.nodes["Prism"].inputs
    prism["Main Color"].default_value = (0.93, 0.91, 0.95, 1.0)
    prism["Rim Mask"].default_value = 0.0
    bpy.ops.mesh.primitive_circle_add(vertices=64, radius=1.6, fill_type="NGON")
    floor = bpy.context.active_object
    floor.name = "Floor"
    floor.data.materials.append(floor_mat)

    aim = bpy.data.objects.new("TKB Camera Target", None)
    aim.location = (0, 0, target_z)
    cam_data = bpy.data.cameras.new("TKB Camera")
    cam_data.lens = 70
    cam = bpy.data.objects.new("TKB Camera", cam_data)
    cam.location = (1.1, -3.9, 1.3)
    track = cam.constraints.new("TRACK_TO")
    track.target = aim
    for o in (floor, aim, cam):
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    bpy.context.scene.camera = cam


def embed_addon():
    src = os.path.join(HERE, "tenkabana_toon.py")
    txt = bpy.data.texts.get("tenkabana_toon.py") or bpy.data.texts.new("tenkabana_toon.py")
    with open(src, encoding="utf-8") as f:
        txt.from_string(f.read())
    txt.use_module = True


def main():
    opts = parse_args()
    idol = resolve_idol(opts["idol"], opts["export"])
    name = tkb.IDOLS.get(idol, "idol")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1440
    tkb.setup_scene(scene)

    stage = bpy.data.collections.new("TKB Stage")
    scene.collection.children.link(stage)
    build_stage(stage)

    if opts["export"]:
        if not tkb.import_export_folder(opts["export"], idol):
            raise SystemExit(f"nothing importable in {opts['export']}")
    else:
        coll = bpy.data.collections.new(f"Tenkabana {name} (stand-in)")
        scene.collection.children.link(coll)
        tkb.apply_body(build_stand_in(idol, coll), idol)

    embed_addon()
    out = opts["out"] or os.path.join(HERE, f"{name}_template.blend")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out), compress=True)
    print(f"saved {out}")

    if opts["render"]:
        if opts["engine"] == "cycles":
            scene.render.engine = "CYCLES"
            scene.cycles.samples = 16
        scene.render.filepath = os.path.abspath(opts["render"])
        bpy.ops.render.render(write_still=True)
        print(f"rendered {opts['render']}")


if __name__ == "__main__":
    main()
