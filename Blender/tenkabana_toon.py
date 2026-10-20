"""Tenkabana Toon - Song for Prism style cel shading for Blender.

An approximation of the game's character look, rebuilt from node math rather
than ported: the shipped shaders are compiled bytecode, so what is reproduced
here is what the exported textures and the rendered game show.

    * one key light, read from the "TKB Key Light" sun's rotation
    * half-lambert -> hard terminator, shadow side = base colour * shadow tint
    * _smask biases where the terminator falls (painted shadow areas)
    * _spmap gates a stepped specular
    * _rnoise jitters a Kajiya-Kay band on hair (the "angel ring")
    * screen-independent rim light
    * inverted-hull outline tinted from the base colour

Everything is computed by hand and output as emission, so Eevee and Cycles
render the same image and scene lights have no effect except the key light's
direction.

Install: Edit > Preferences > Add-ons > Install from Disk, pick this file.
Panel:   3D Viewport > Sidebar (N) > Tenkabana.
Script:  can also be imported by build_template.py for headless builds.
"""

bl_info = {
    "name": "Tenkabana Toon",
    "author": "Tenkabana",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Tenkabana",
    "description": "Song for Prism style toon materials, outlines and import for Tenkabana exports",
    "category": "Material",
}

import math
import os
import re

import bpy

# ---------------------------------------------------------------------------
# Game data
# ---------------------------------------------------------------------------

# id -> romaji given name; matches Tenkabana's NN_name export folders.
IDOLS = {
    1: "mano", 2: "hiori", 3: "meguru", 4: "kogane", 5: "mamimi", 6: "sakuya",
    7: "yuika", 8: "kiriko", 9: "kaho", 10: "chiyoko", 11: "juri", 12: "rinze",
    13: "natsuha", 14: "amana", 15: "tenka", 16: "chiyuki", 17: "asahi",
    18: "fuyuko", 19: "mei", 20: "toru", 21: "madoka", 22: "koito", 23: "hinana",
    24: "nichika", 25: "mikoto", 26: "luca", 27: "hana", 28: "haruki", 90: "hazuki",
}

# Runtime body parameters read from the game. Only these two are known so far;
# everyone else gets the neutral base body.
BODY = {
    14: {"height": 0.99375, "bust": 0.435},
    15: {"height": 0.99375, "bust": 0.478},
}

MAP_KINDS = ("col", "smask", "rmask", "spmap", "rnoise")
_MAP_RE = re.compile(r"^(?P<base>.+?)_(?P<kind>col|smask|rmask|spmap|rnoise)$", re.IGNORECASE)

# Mesh names carry no idol id (BodySkin, FrontHair, ATAMA_shadow), so OBJ parts
# are matched to texture sets by what they are. (part, mesh hints, texture hints)
_PARTS = (
    ("hair", ("hair", "atama", "bang", "maegami", "ushiro", "tail", "backhead"), ("hair",)),
    ("face", ("face", "eye", "mouth", "mayu", "brow", "teeth", "tooth", "tongue",
              "kao", "lash", "hitomi", "cheek"), ("face",)),
    ("skin", ("body", "skin", "hand", "arm", "leg", "neck"), ("body", "skin")),
    ("dress", ("dress", "cloth", "skirt", "shirt", "coat", "shoe", "boot", "sock",
               "_bs", "bs_"), ("dress",)),
    ("acce", ("acce", "ribbon", "hat", "ring", "earring"), ("acce",)),
)

# Per-part look. Faces are lit more generously, as in game: a hard terminator
# across a face reads as dirt.
PRESETS = {
    "default": {"Threshold": 0.5, "Softness": 0.03},
    "skin": {"Threshold": 0.45, "Softness": 0.05, "Shadow Tint": (0.93, 0.74, 0.74, 1.0)},
    "face": {"Threshold": 0.3, "Softness": 0.08, "Shadow Tint": (0.95, 0.78, 0.78, 1.0),
             "Rim Strength": 0.0},
    "hair": {"Threshold": 0.5, "Softness": 0.03, "Hair Highlight": 1.0},
    "dress": {"Threshold": 0.5, "Softness": 0.03},
    "acce": {"Threshold": 0.5, "Softness": 0.03, "Spec Strength": 1.0},
}

LIGHT_NAME = "TKB Key Light"
LIGHT_GROUP = "TKB Light Direction"
TOON_GROUP = "TKB Toon"


def idol_from_path(path):
    """Idol id from an export folder: '14_amana', '14mgar', 'amana'... or None."""
    name = os.path.basename(os.path.normpath(path)).lower()
    m = re.match(r"^(\d{1,2})(?:_|[a-z]{2}\d{2}$|[a-z]{4}$)", name)
    if m and int(m.group(1)) in IDOLS:
        return int(m.group(1))
    for i, n in IDOLS.items():
        if n in name:
            return i
    return None


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

class _Graph:
    """Thin builder: creates nodes on a loose grid and links or sets inputs."""

    def __init__(self, tree):
        self.tree = tree
        self.n = 0

    def node(self, kind, **props):
        node = self.tree.nodes.new(kind)
        node.location = ((self.n // 12) * 220, -(self.n % 12) * 160)
        self.n += 1
        for k, v in props.items():
            setattr(node, k, v)
        return node

    def feed(self, sock, value):
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, sock)
        elif value is not None:
            if isinstance(value, tuple) and sock.type == "VECTOR":
                value = value[:3]  # colours passed to vector math drop alpha
            sock.default_value = value

    def math(self, op, a, b=None, c=None, clamp=False):
        node = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for sock, v in zip(node.inputs, (a, b, c)):
            self.feed(sock, v)
        return node.outputs[0]

    def vec(self, op, a, b=None, scale=None):
        node = self.node("ShaderNodeVectorMath", operation=op)
        self.feed(node.inputs[0], a)
        if b is not None:
            self.feed(node.inputs[1], b)
        if scale is not None:
            self.feed(node.inputs["Scale"], scale)
        out = "Value" if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else "Vector"
        return node.outputs[out]

    def lerp(self, a, b, t):
        """a + (b - a) * t on floats."""
        return self.math("ADD", a, self.math("MULTIPLY", self.math("SUBTRACT", b, a), t))

    def mix_color(self, a, b, t):
        node = self.node("ShaderNodeMix", data_type="RGBA", blend_type="MIX", clamp_factor=True)
        self.feed(_by_id(node.inputs, "Factor_Float"), t)
        self.feed(_by_id(node.inputs, "A_Color"), a)
        self.feed(_by_id(node.inputs, "B_Color"), b)
        return _by_id(node.outputs, "Result_Color")

    def smoothstep(self, value, lo, hi):
        node = self.node("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP", clamp=True)
        self.feed(node.inputs["Value"], value)
        self.feed(node.inputs["From Min"], lo)
        self.feed(node.inputs["From Max"], hi)
        return node.outputs["Result"]


def _by_id(sockets, identifier):
    for s in sockets:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


def _socket(tree, name, kind, in_out="INPUT", default=None, lo=None, hi=None):
    s = tree.interface.new_socket(name, in_out=in_out, socket_type=kind)
    if default is not None:
        s.default_value = default
    if lo is not None:
        s.min_value = lo
    if hi is not None:
        s.max_value = hi
    return s


# ---------------------------------------------------------------------------
# Key light: a sun whose rotation drives every toon material
# ---------------------------------------------------------------------------

def ensure_key_light(scene=None):
    scene = scene or bpy.context.scene
    obj = bpy.data.objects.get(LIGHT_NAME)
    if obj is None:
        data = bpy.data.lights.new(LIGHT_NAME, type="SUN")
        data.energy = 3.0
        obj = bpy.data.objects.new(LIGHT_NAME, data)
        # Front-left, from above: the usual stage key.
        obj.rotation_euler = (math.radians(55), 0.0, math.radians(-55))
        obj.location = (0.0, -2.0, 3.0)
    if obj.name not in scene.collection.all_objects:
        scene.collection.objects.link(obj)
    return obj


def ensure_light_group():
    """Node group outputting the world-space direction *towards* the key light.

    Drivers on a Combine XYZ rebuild the sun's +Z axis from its world euler
    rotation (XYZ order). Simple expressions only, so it works without
    'Auto Run Python Scripts'.
    """
    tree = bpy.data.node_groups.get(LIGHT_GROUP)
    if tree is not None:
        return tree
    light = ensure_key_light()
    tree = bpy.data.node_groups.new(LIGHT_GROUP, "ShaderNodeTree")
    _socket(tree, "Direction", "NodeSocketVector", in_out="OUTPUT")
    g = _Graph(tree)
    out = g.node("NodeGroupOutput")
    comb = g.node("ShaderNodeCombineXYZ")
    comb.name = "Light Axis"

    exprs = (
        "cos(rx)*sin(ry)*cos(rz) + sin(rx)*sin(rz)",
        "cos(rx)*sin(ry)*sin(rz) - sin(rx)*cos(rz)",
        "cos(rx)*cos(ry)",
    )
    for i, expr in enumerate(exprs):
        fc = comb.inputs[i].driver_add("default_value")
        drv = fc.driver
        drv.type = "SCRIPTED"
        for var_name, channel in (("rx", "ROT_X"), ("ry", "ROT_Y"), ("rz", "ROT_Z")):
            v = drv.variables.new()
            v.name = var_name
            v.type = "TRANSFORMS"
            v.targets[0].id = light
            v.targets[0].transform_type = channel
            v.targets[0].transform_space = "WORLD_SPACE"
        drv.expression = expr

    g.feed(out.inputs[0], g.vec("NORMALIZE", comb.outputs[0]))
    return tree


# ---------------------------------------------------------------------------
# The toon group
# ---------------------------------------------------------------------------

def ensure_toon_group():
    tree = bpy.data.node_groups.get(TOON_GROUP)
    if tree is not None:
        return tree
    light_tree = ensure_light_group()
    tree = bpy.data.node_groups.new(TOON_GROUP, "ShaderNodeTree")

    C, F = "NodeSocketColor", "NodeSocketFloat"
    _socket(tree, "Shader", "NodeSocketShader", in_out="OUTPUT")
    _socket(tree, "Color", C, in_out="OUTPUT")

    _socket(tree, "Base Color", C, default=(0.8, 0.8, 0.8, 1.0))
    _socket(tree, "Alpha", F, default=1.0, lo=0.0, hi=1.0)
    _socket(tree, "Shadow Tint", C, default=(0.78, 0.70, 0.82, 1.0))
    _socket(tree, "Threshold", F, default=0.5, lo=0.0, hi=1.0)
    _socket(tree, "Softness", F, default=0.03, lo=0.001, hi=0.5)
    _socket(tree, "Shadow Mask", F, default=1.0, lo=0.0, hi=1.0)
    _socket(tree, "Shadow Mask Strength", F, default=1.0, lo=0.0, hi=1.0)
    _socket(tree, "AO Mask", F, default=1.0, lo=0.0, hi=1.0)
    _socket(tree, "AO Strength", F, default=0.0, lo=0.0, hi=1.0)
    _socket(tree, "Spec Map", F, default=0.0, lo=0.0, hi=1.0)
    _socket(tree, "Spec Strength", F, default=0.6, lo=0.0, hi=4.0)
    _socket(tree, "Spec Size", F, default=0.08, lo=0.0, hi=1.0)
    _socket(tree, "Rim Color", C, default=(1.0, 0.96, 0.98, 1.0))
    _socket(tree, "Rim Strength", F, default=0.25, lo=0.0, hi=2.0)
    _socket(tree, "Rim Width", F, default=0.3, lo=0.0, hi=1.0)
    _socket(tree, "Hair Highlight", F, default=0.0, lo=0.0, hi=1.0)
    _socket(tree, "Hair Noise", F, default=0.5, lo=0.0, hi=1.0)
    _socket(tree, "Hair Shift", F, default=0.0, lo=-1.0, hi=1.0)
    _socket(tree, "Hair Width", F, default=0.012, lo=0.0, hi=0.2)
    _socket(tree, "Hair Color", C, default=(0.55, 0.5, 0.45, 1.0))
    _socket(tree, "Brightness", F, default=1.0, lo=0.0, hi=4.0)

    g = _Graph(tree)
    gin = g.node("NodeGroupInput")
    gout = g.node("NodeGroupOutput")
    I = gin.outputs

    lnode = g.node("ShaderNodeGroup")
    lnode.node_tree = light_tree
    L = lnode.outputs[0]
    geo = g.node("ShaderNodeNewGeometry")
    N, V = geo.outputs["Normal"], geo.outputs["Incoming"]

    # Diffuse: half-lambert pushed through the painted masks, then a hard step.
    ndl = g.vec("DOT_PRODUCT", N, L)
    half = g.math("MULTIPLY_ADD", ndl, 0.5, 0.5)
    smask = g.lerp(1.0, I["Shadow Mask"], I["Shadow Mask Strength"])
    ao = g.lerp(1.0, I["AO Mask"], I["AO Strength"])
    term = g.math("MULTIPLY", g.math("MULTIPLY", half, smask), ao)
    lo = g.math("SUBTRACT", I["Threshold"], I["Softness"])
    hi = g.math("ADD", I["Threshold"], I["Softness"])
    lit = g.smoothstep(term, lo, hi)

    shade = g.vec("MULTIPLY", I["Base Color"], I["Shadow Tint"])
    diffuse = g.mix_color(shade, I["Base Color"], lit)

    # Specular: Blinn half-vector, stepped, only where the map allows and lit.
    H = g.vec("NORMALIZE", g.vec("ADD", L, V))
    ndh = g.math("MAXIMUM", g.vec("DOT_PRODUCT", N, H), 0.0)
    spec_edge = g.math("SUBTRACT", 1.0, g.math("MULTIPLY", I["Spec Size"], 0.25))
    spec = g.smoothstep(ndh, spec_edge, g.math("ADD", spec_edge, 0.005))
    spec = g.math("MULTIPLY", g.math("MULTIPLY", spec, I["Spec Map"]),
                  g.math("MULTIPLY", I["Spec Strength"], lit))

    # Rim: view-facing falloff, stronger on the lit side.
    ndv = g.math("MAXIMUM", g.vec("DOT_PRODUCT", N, V), 0.0)
    rim_edge = g.math("SUBTRACT", 1.0, I["Rim Width"])
    rim = g.smoothstep(g.math("SUBTRACT", 1.0, ndv), rim_edge, g.math("ADD", rim_edge, 0.08))
    rim = g.math("MULTIPLY", rim, g.math("MULTIPLY", I["Rim Strength"], g.lerp(0.35, 1.0, lit)))

    # Hair: Kajiya-Kay along the strand (UV V), shifted per strand by _rnoise.
    tan = g.node("ShaderNodeTangent", direction_type="UV_MAP")
    strand = g.vec("NORMALIZE", g.vec("CROSS_PRODUCT", N, tan.outputs[0]))
    jitter = g.math("ADD", I["Hair Shift"], g.math("MULTIPLY", g.math("SUBTRACT", I["Hair Noise"], 0.5), 0.6))
    strand = g.vec("NORMALIZE", g.vec("ADD", strand, g.vec("SCALE", N, scale=jitter)))
    tdh = g.vec("DOT_PRODUCT", strand, H)
    sin_th = g.math("SQRT", g.math("MAXIMUM", g.math("SUBTRACT", 1.0, g.math("MULTIPLY", tdh, tdh)), 0.0))
    ring_edge = g.math("SUBTRACT", 1.0, I["Hair Width"])
    ring = g.smoothstep(sin_th, ring_edge, g.math("ADD", ring_edge, 0.004))
    ring = g.math("MULTIPLY", g.math("MULTIPLY", ring, I["Hair Highlight"]), g.lerp(0.2, 1.0, lit))

    color = g.vec("ADD", diffuse, g.vec("SCALE", (1.0, 1.0, 1.0), scale=spec))
    color = g.vec("ADD", color, g.vec("SCALE", I["Rim Color"], scale=rim))
    color = g.vec("ADD", color, g.vec("SCALE", I["Hair Color"], scale=ring))
    color = g.vec("SCALE", color, scale=I["Brightness"])

    emit = g.node("ShaderNodeEmission")
    g.feed(emit.inputs["Color"], color)
    emit.inputs["Strength"].default_value = 1.0
    clear = g.node("ShaderNodeBsdfTransparent")
    mix = g.node("ShaderNodeMixShader")
    g.feed(mix.inputs[0], I["Alpha"])
    g.feed(mix.inputs[1], clear.outputs[0])
    g.feed(mix.inputs[2], emit.outputs[0])

    g.feed(gout.inputs["Shader"], mix.outputs[0])
    g.feed(gout.inputs["Color"], color)
    return tree


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _prep_material(mat):
    try:
        if not mat.use_nodes:
            mat.use_nodes = True
    except AttributeError:
        pass
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "HASHED"
    return mat.node_tree


def _load_image(src, non_color):
    if isinstance(src, bpy.types.Image):
        img = src
    else:
        img = bpy.data.images.load(src, check_existing=True)
    img.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
    if not non_color:
        img.alpha_mode = "CHANNEL_PACKED"
    return img


def build_toon_material(name, maps, part="default"):
    """maps: {'col': path_or_image, 'smask': ..., ...}; missing maps are skipped."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    tree = _prep_material(mat)
    tree.nodes.clear()
    g = _Graph(tree)

    out = g.node("ShaderNodeOutputMaterial")
    toon = g.node("ShaderNodeGroup")
    toon.node_tree = ensure_toon_group()
    toon.name = "Toon"
    toon.width = 220
    preset = dict(PRESETS.get(part, PRESETS["default"]))
    if "rnoise" in maps:
        preset["Hair Highlight"] = 1.0
    for k, v in preset.items():
        toon.inputs[k].default_value = v

    def tex(kind):
        if kind not in maps:
            return None
        node = g.node("ShaderNodeTexImage")
        node.image = _load_image(maps[kind], non_color=kind != "col")
        node.label = kind
        return node

    col = tex("col")
    if col is not None:
        g.feed(toon.inputs["Base Color"], col.outputs["Color"])
        g.feed(toon.inputs["Alpha"], col.outputs["Alpha"])

    smask = tex("smask")
    if smask is not None:
        sep = g.node("ShaderNodeSeparateColor")
        g.feed(sep.inputs[0], smask.outputs["Color"])
        g.feed(toon.inputs["Shadow Mask"], sep.outputs[0])

    rmask = tex("rmask")
    if rmask is not None:
        # Channel meaning is unconfirmed. G is wired as occlusion but left at
        # strength 0; raise "AO Strength" if it reads right on your model.
        sep = g.node("ShaderNodeSeparateColor")
        g.feed(sep.inputs[0], rmask.outputs["Color"])
        g.feed(toon.inputs["AO Mask"], sep.outputs[1])

    spmap = tex("spmap")
    if spmap is not None:
        g.feed(toon.inputs["Spec Map"], spmap.outputs["Color"])

    rnoise = tex("rnoise")
    if rnoise is not None:
        g.feed(toon.inputs["Hair Noise"], rnoise.outputs["Color"])

    g.feed(out.inputs["Surface"], toon.outputs["Shader"])
    mat["tkb_part"] = part
    return mat


def build_outline_material(source):
    """Inverted-hull outline coloured from the source material's base texture."""
    name = f"{source.name} (outline)"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    tree = _prep_material(mat)
    tree.nodes.clear()
    mat.use_backface_culling = True
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    g = _Graph(tree)
    out = g.node("ShaderNodeOutputMaterial")

    tint = (0.42, 0.33, 0.38, 1.0)
    base = None
    if source.node_tree is not None:
        for n in source.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.label == "col" and n.image is not None:
                t = g.node("ShaderNodeTexImage")
                t.image = n.image
                base = t
                break
    if base is not None:
        color = g.vec("MULTIPLY", base.outputs["Color"], tint)
        alpha = base.outputs["Alpha"]
    else:
        color, alpha = tint, 1.0

    emit = g.node("ShaderNodeEmission")
    g.feed(emit.inputs["Color"], color)
    geo = g.node("ShaderNodeNewGeometry")
    clear = g.node("ShaderNodeBsdfTransparent")
    # The hull's normals are flipped: keep only faces that now point at the
    # camera, i.e. its far side peeking out past the silhouette.
    side = g.node("ShaderNodeMixShader")
    g.feed(side.inputs[0], geo.outputs["Backfacing"])
    g.feed(side.inputs[1], emit.outputs[0])
    g.feed(side.inputs[2], clear.outputs[0])
    cut = g.node("ShaderNodeMixShader")
    g.feed(cut.inputs[0], alpha)
    g.feed(cut.inputs[1], clear.outputs[0])
    g.feed(cut.inputs[2], side.outputs[0])
    g.feed(out.inputs["Surface"], cut.outputs[0])
    mat["tkb_outline"] = True
    return mat


OUTLINE_MOD = "TKB Outline"


def add_outline(obj, width=0.0015):
    if obj.type != "MESH":
        return
    remove_outline(obj)
    mats = [m for m in obj.data.materials if m is not None and not m.get("tkb_outline")]
    if not mats:
        return
    count = len(obj.data.materials)
    for m in obj.data.materials[:count]:
        obj.data.materials.append(build_outline_material(m) if m is not None else None)
    mod = obj.modifiers.new(OUTLINE_MOD, "SOLIDIFY")
    mod.thickness = width
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_even_offset = True
    mod.use_rim = False
    mod.material_offset = count


def remove_outline(obj):
    mod = obj.modifiers.get(OUTLINE_MOD)
    if mod is not None:
        obj.modifiers.remove(mod)
    if obj.type == "MESH":
        for i in reversed(range(len(obj.data.materials))):
            m = obj.data.materials[i]
            if m is not None and m.get("tkb_outline"):
                obj.data.materials.pop(index=i)


# ---------------------------------------------------------------------------
# Texture sets and matching
# ---------------------------------------------------------------------------

def scan_textures(folder):
    """{base: {kind: path}} for every *_col/_smask/... image under folder."""
    sets = {}
    if not folder or not os.path.isdir(folder):
        return sets
    for root, _, files in os.walk(folder):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in (".png", ".tga", ".jpg", ".jpeg"):
                continue
            m = _MAP_RE.match(stem)
            if m:
                sets.setdefault(m["base"], {})[m["kind"].lower()] = os.path.join(root, f)
    return sets


def part_of(name):
    low = name.lower()
    for part, mesh_hints, _ in _PARTS:
        if any(h in low for h in mesh_hints):
            return part
    return "default"


def best_set(sets, part, idol=None):
    hints = next((t for p, _, t in _PARTS if p == part), ())
    cands = [b for b in sets if "col" in sets[b] and any(h in b.lower() for h in hints)]
    if not cands:
        return None
    if idol is not None:
        tag = f"{idol:02d}"
        own = [b for b in cands if re.search(rf"(^|_){tag}(_|[a-z])", b.lower())]
        cands = own or cands
    return sorted(cands)[0]


def _set_from_material(mat, sets):
    """Find which texture set an imported (glTF/FBX) material already points at."""
    if mat is None or mat.node_tree is None:
        return None, {}
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image is not None:
            stem = os.path.splitext(os.path.basename(n.image.filepath or n.image.name))[0]
            m = _MAP_RE.match(stem)
            if not m:
                continue
            base = m["base"]
            if base in sets:
                return base, sets[base]
            # Sibling maps next to the image itself.
            folder = os.path.dirname(bpy.path.abspath(n.image.filepath)) if n.image.filepath else ""
            local = scan_textures(folder).get(base, {})
            if not local:
                local = {"col": n.image} if m["kind"].lower() == "col" else {}
            return base, local
    return None, {}


def toonify_object(obj, sets, idol=None, outline=True, width=0.0015):
    """Replace obj's materials with toon materials. Returns texture sets used."""
    if obj.type != "MESH":
        return []
    remove_outline(obj)
    used = []
    part = part_of(obj.name)
    slots = obj.data.materials
    if len(slots) == 0:
        slots.append(None)
    for i, mat in enumerate(list(slots)):
        base, maps = _set_from_material(mat, sets)
        if base is None:
            base = best_set(sets, part, idol)
            maps = sets.get(base, {}) if base else {}
        if base is None:
            continue
        slots[i] = build_toon_material(f"TKB {base}", maps, part_of(base) if part == "default" else part)
        used.append(base)
    if outline and used:
        add_outline(obj, width)
    return used


def apply_body(objects, idol):
    """Height as uniform scale on the roots; bust onto any matching shape key."""
    body = BODY.get(idol)
    if not body:
        return
    for obj in objects:
        if obj.parent is None:
            obj.scale = tuple(s * body["height"] for s in obj.scale)
        keys = getattr(getattr(obj, "data", None), "shape_keys", None)
        if keys:
            for kb in keys.key_blocks:
                if any(t in kb.name.lower() for t in ("bust", "mune", "breast")):
                    kb.value = body["bust"]


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

def setup_scene(scene=None):
    """Colour management and engine settings that match the game's flat look."""
    scene = scene or bpy.context.scene
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    # Filmic/AgX desaturate a toon palette; the game writes straight sRGB.
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0

    world = scene.world or bpy.data.worlds.new("TKB World")
    scene.world = world
    try:
        world.use_nodes = True
    except AttributeError:
        pass
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg is not None:
        bg.inputs["Color"].default_value = (0.82, 0.80, 0.86, 1.0)
        bg.inputs["Strength"].default_value = 1.0
    ensure_key_light(scene)


def import_export_folder(folder, idol=None, outline=True, width=0.0015):
    """Import a Tenkabana export (or an AssetRipper glTF/FBX of one) and toonify it."""
    folder = bpy.path.abspath(folder)
    idol = idol if idol is not None else idol_from_path(folder)
    before = set(bpy.data.objects)

    models = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith((".glb", ".gltf", ".fbx")):
                models.append(os.path.join(root, f))
    if models:
        for path in sorted(models):
            if path.lower().endswith(".fbx"):
                bpy.ops.import_scene.fbx(filepath=path)
            else:
                bpy.ops.import_scene.gltf(filepath=path)
    else:
        mesh_dir = os.path.join(folder, "meshes")
        mesh_dir = mesh_dir if os.path.isdir(mesh_dir) else folder
        for f in sorted(os.listdir(mesh_dir)):
            if f.lower().endswith(".obj"):
                bpy.ops.wm.obj_import(filepath=os.path.join(mesh_dir, f))

    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        return []
    label = IDOLS.get(idol, os.path.basename(folder)) if idol else os.path.basename(folder)
    coll = bpy.data.collections.new(f"Tenkabana {label}")
    bpy.context.scene.collection.children.link(coll)
    for o in new:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)

    sets = scan_textures(folder)
    for o in new:
        toonify_object(o, sets, idol, outline, width)
    if idol is not None:
        apply_body(new, idol)
    setup_scene()
    return new


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

_set_items = []


def _texture_set_items(self, context):
    global _set_items
    folder = bpy.path.abspath(context.scene.tkb.texture_dir)
    sets = scan_textures(folder)
    # Blender needs these strings kept alive while the menu is open.
    _set_items = [(b, b, ", ".join(sorted(sets[b]))) for b in sorted(sets)] or [("NONE", "No texture sets", "")]
    return _set_items


def _idol_items(self, context):
    return [("0", "Auto (from folder name)", "")] + [
        (str(i), f"{i:02d} {n.capitalize()}", "") for i, n in IDOLS.items()]


class TKB_Settings(bpy.types.PropertyGroup):
    texture_dir: bpy.props.StringProperty(
        name="Export folder", subtype="DIR_PATH",
        description="A Tenkabana export folder (contains meshes/ and textures/)")
    idol: bpy.props.EnumProperty(name="Idol", items=_idol_items)
    outline: bpy.props.BoolProperty(name="Outline", default=True)
    outline_width: bpy.props.FloatProperty(
        name="Width", default=0.0015, min=0.0, max=0.02, precision=4, unit="LENGTH")


def _settings_idol(s):
    return int(s.idol) or idol_from_path(bpy.path.abspath(s.texture_dir))


class TKB_OT_import(bpy.types.Operator):
    bl_idname = "tenkabana.import_export"
    bl_label = "Import Export Folder"
    bl_description = "Import meshes from the export folder and give them toon materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.tkb
        if not os.path.isdir(bpy.path.abspath(s.texture_dir)):
            self.report({"ERROR"}, "Set the export folder first")
            return {"CANCELLED"}
        objs = import_export_folder(s.texture_dir, _settings_idol(s), s.outline, s.outline_width)
        self.report({"INFO"}, f"Imported {len(objs)} objects")
        return {"FINISHED"}


class TKB_OT_toonify(bpy.types.Operator):
    bl_idname = "tenkabana.toonify"
    bl_label = "Toonify Selected"
    bl_description = "Replace the selected objects' materials with toon materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.tkb
        sets = scan_textures(bpy.path.abspath(s.texture_dir))
        n = 0
        for o in context.selected_objects:
            n += len(toonify_object(o, sets, _settings_idol(s), s.outline, s.outline_width))
        setup_scene(context.scene)
        self.report({"INFO"}, f"{n} materials converted")
        return {"FINISHED"}


class TKB_OT_assign(bpy.types.Operator):
    bl_idname = "tenkabana.assign_set"
    bl_label = "Assign Texture Set"
    bl_description = "Give the selected objects a specific texture set"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "texture_set"

    texture_set: bpy.props.EnumProperty(name="Texture set", items=_texture_set_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        s = context.scene.tkb
        sets = scan_textures(bpy.path.abspath(s.texture_dir))
        maps = sets.get(self.texture_set)
        if not maps:
            return {"CANCELLED"}
        for o in context.selected_objects:
            if o.type != "MESH":
                continue
            remove_outline(o)
            part = part_of(o.name)
            mat = build_toon_material(f"TKB {self.texture_set}", maps,
                                      part_of(self.texture_set) if part == "default" else part)
            o.data.materials.clear()
            o.data.materials.append(mat)
            if s.outline:
                add_outline(o, s.outline_width)
        return {"FINISHED"}


class TKB_OT_outline(bpy.types.Operator):
    bl_idname = "tenkabana.outline"
    bl_label = "Outline"
    bl_options = {"REGISTER", "UNDO"}

    enable: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        s = context.scene.tkb
        for o in context.selected_objects:
            if self.enable:
                add_outline(o, s.outline_width)
            else:
                remove_outline(o)
        return {"FINISHED"}


class TKB_OT_scene(bpy.types.Operator):
    bl_idname = "tenkabana.setup_scene"
    bl_label = "Set Up Scene"
    bl_description = "Standard view transform, background and the key light"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        setup_scene(context.scene)
        return {"FINISHED"}


class TKB_PT_panel(bpy.types.Panel):
    bl_label = "Tenkabana Toon"
    bl_idname = "TKB_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tenkabana"

    def draw(self, context):
        s = context.scene.tkb
        col = self.layout.column(align=True)
        col.prop(s, "texture_dir", text="")
        col.prop(s, "idol", text="")
        col.operator(TKB_OT_import.bl_idname, icon="IMPORT")
        col.separator()
        col.operator(TKB_OT_toonify.bl_idname, icon="SHADING_RENDERED")
        col.operator(TKB_OT_assign.bl_idname, icon="TEXTURE")
        row = col.row(align=True)
        row.prop(s, "outline")
        row.prop(s, "outline_width")
        row = col.row(align=True)
        row.operator(TKB_OT_outline.bl_idname, text="Add Outline").enable = True
        row.operator(TKB_OT_outline.bl_idname, text="Remove").enable = False
        col.separator()
        col.operator(TKB_OT_scene.bl_idname, icon="WORLD")
        light = bpy.data.objects.get(LIGHT_NAME)
        if light is not None:
            box = self.layout.box()
            box.label(text="Key light", icon="LIGHT_SUN")
            box.prop(light, "rotation_euler", text="")


_classes = (TKB_Settings, TKB_OT_import, TKB_OT_toonify, TKB_OT_assign,
            TKB_OT_outline, TKB_OT_scene, TKB_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.tkb = bpy.props.PointerProperty(type=TKB_Settings)


def unregister():
    del bpy.types.Scene.tkb
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
