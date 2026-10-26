"""Tenkabana Toon - Song for Prism's character shading, rebuilt in Blender.

The node groups here follow the game's own shaders, read from their compiled
D3D11 programs (Anim2D3D/CharacterOutline, Hair, EyeBall, FakeShadow,
Character Unlit). Per-material values come from the game's materials via
prism_extract.py; the few values the game sets per stage at runtime live in
one shared "TKB Prism Globals" group.

What the game does, and so what this does:

    Character   diffuse = MainTex*_Color, shaded by a ramp of
                ShadeTex.r * 0.5 * (N.L + 1) around _DiffuseBorder/_DiffuseBlur,
                coloured _DarkColor -> _BrightColor and scaled by _ShadeEffect.
                (Most skin and face materials set Dark = Bright: no shadow.)
                SPmap R/G/B = specular mask / roughness / metallic feeding a
                GGX-style highlight; Rmask.R gates a rim light.
    Hair        same diffuse, plus the angel ring: the view ray is intersected
                with a sphere round the head, the hit direction (X/Z jittered by
                the noise map on UV2) samples a ring cubemap, masked by
                RingMask.a on UV2.
    Eye         sclera * _Color, iris and highlight alpha-blended over it.
    FakeShadow  the front hair redrawn 6 mm away from the light as a flat
                multiply over the face. The game limits it to skin with a
                stencil; here the shifted copy is laid onto the face surface
                (Geometry Nodes), so the real bangs always cover it and it can
                only darken skin, never the hair itself.
    Outline     inverted hull (Geometry Nodes) along the game's baked outline
                directions, in the camera's view plane, _OutlineWidth metres
                masked by _OutlineTex, pushed back by _OutlineAdjust.

The game renders in gamma space, so the maths runs on raw sRGB values and is
converted to linear once at the end. Everything is emission: Eevee and Cycles
match, and only the key light's direction matters.

Install: Edit > Preferences > Add-ons > Install from Disk, pick this file.
Panel:   3D Viewport > Sidebar (N) > Tenkabana.
"""

bl_info = {
    "name": "Tenkabana Toon",
    "author": "Tenkabana",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Tenkabana",
    "description": "Song for Prism character shading, outlines and import for Tenkabana exports",
    "category": "Material",
}

import json
import math
import os
import re

import bpy

# ---------------------------------------------------------------------------
# Game data
# ---------------------------------------------------------------------------

IDOLS = {
    1: "mano", 2: "hiori", 3: "meguru", 4: "kogane", 5: "mamimi", 6: "sakuya",
    7: "yuika", 8: "kiriko", 9: "kaho", 10: "chiyoko", 11: "juri", 12: "rinze",
    13: "natsuha", 14: "amana", 15: "tenka", 16: "chiyuki", 17: "asahi",
    18: "fuyuko", 19: "mei", 20: "toru", 21: "madoka", 22: "koito", 23: "hinana",
    24: "nichika", 25: "mikoto", 26: "luca", 27: "hana", 28: "haruki", 90: "hazuki",
}

# Runtime body parameters read from the game; everyone else is the neutral base.
BODY = {
    14: {"height": 0.99375, "bust": 0.435},
    15: {"height": 0.99375, "bust": 0.478},
}

# Values the game sets per stage, not per material. Not in any bundle, so these
# are chosen to match how characters look in the game's menus.
GLOBALS = (
    ("Light Color", "RGBA", (1.0, 1.0, 1.0, 1.0)),
    # Skin and face materials ship Dark = Bright (no shadow); the game swaps in
    # a warm shadow colour at runtime. Measured from an in-game screenshot:
    # shadowed skin = texture * (0.886, 0.745, 0.739) = Dark + 0.5.
    ("Skin Shadow", "RGBA", (0.386, 0.245, 0.239, 1.0)),
    # The face's constant t = 0.5 must stay lit, and the arm terminator is crisp.
    ("Diffuse Border", "VALUE", 0.45),
    ("Diffuse Blur", "VALUE", 0.05),
    ("Rim Power", "VALUE", 3.0),
    ("Rim Intensity", "VALUE", 1.0),
    ("Rim Border", "VALUE", 0.55),
    ("Rim Blur", "VALUE", 0.2),
    ("Rim Color", "RGBA", (0.35, 0.35, 0.35, 1.0)),
    ("Ambient", "RGBA", (0.25, 0.25, 0.25, 1.0)),
    ("Reflection", "RGBA", (0.5, 0.5, 0.5, 1.0)),
    ("Outline Scale", "VALUE", 1.0),
)

LIGHT_NAME = "TKB Key Light"
GLOBALS_GROUP = "TKB Prism Globals"
CHAR_GROUP = "TKB Prism Character"
HAIR_GROUP = "TKB Prism Hair"
RING_GROUP = "TKB Prism Ring Vector"
EYE_GROUP = "TKB Prism Eye"
UNLIT_GROUP = "TKB Prism Unlit"
OUTLINE_MOD = "TKB Outline"
OUTLINE_GROUP = "TKB Outline Width"
DECAL_MOD = "TKB Fake Shadow Decal"
DECAL_GROUP = "TKB Fake Shadow Decal"

# Light direction from the key light's world euler (XYZ): its +Z axis.
_AXIS_EXPR = (
    "cos(rx)*sin(ry)*cos(rz) + sin(rx)*sin(rz)",
    "cos(rx)*sin(ry)*sin(rz) - sin(rx)*cos(rz)",
    "cos(rx)*cos(ry)",
)


def idol_from_path(path):
    """Idol id from an export folder: '14_amana', '14mgar', '14sw02', 'amana'... or None."""
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

def _by_id(sockets, identifier):
    for s in sockets:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


class _Graph:
    """Thin builder: creates nodes on a loose grid and links or sets inputs."""

    def __init__(self, tree):
        self.tree = tree
        self.n = 0

    def node(self, kind, **props):
        node = self.tree.nodes.new(kind)
        node.location = ((self.n // 14) * 220, -(self.n % 14) * 150)
        self.n += 1
        for k, v in props.items():
            setattr(node, k, v)
        return node

    def feed(self, sock, value):
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, sock)
        elif value is not None:
            if isinstance(value, (tuple, list)) and sock.type == "VECTOR":
                value = tuple(value)[:3]
            elif isinstance(value, (tuple, list)) and sock.type == "RGBA" and len(value) == 3:
                value = (*value, 1.0)
            elif isinstance(value, (int, float)) and sock.type == "VECTOR":
                value = (value,) * 3
            elif isinstance(value, (int, float)) and sock.type == "RGBA":
                value = (value, value, value, 1.0)
            sock.default_value = value

    def math(self, op, a, b=None, c=None, clamp=False):
        node = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for sock, v in zip(node.inputs, (a, b, c)):
            self.feed(sock, v)
        return node.outputs[0]

    def sat(self, x):
        return self.math("ADD", x, 0.0, clamp=True)

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
        return self.math("ADD", a, self.math("MULTIPLY", self.math("SUBTRACT", b, a), t))

    def mix(self, a, b, t):
        node = self.node("ShaderNodeMix", data_type="RGBA", blend_type="MIX", clamp_factor=True)
        self.feed(_by_id(node.inputs, "Factor_Float"), t)
        self.feed(_by_id(node.inputs, "A_Color"), a)
        self.feed(_by_id(node.inputs, "B_Color"), b)
        return _by_id(node.outputs, "Result_Color")

    def ramp(self, value, border, blur):
        """The game's border/blur step: sat((v - lo) / max(hi - lo, 0.01))."""
        half = self.math("MULTIPLY", blur, 0.5)
        lo = self.sat(self.math("SUBTRACT", border, half))
        hi = self.sat(self.math("ADD", border, half))
        span = self.math("MAXIMUM", self.math("SUBTRACT", hi, lo), 0.01)
        return self.sat(self.math("DIVIDE", self.math("SUBTRACT", value, lo), span))

    def sqrt3(self, v):
        sep = self.node("ShaderNodeSeparateXYZ")
        self.feed(sep.inputs[0], v)
        comb = self.node("ShaderNodeCombineXYZ")
        for i in range(3):
            self.feed(comb.inputs[i], self.math("SQRT", self.math("MAXIMUM", sep.outputs[i], 0.0)))
        return comb.outputs[0]

    def to_linear(self, color):
        """Gamma-space result -> linear for Blender's Standard view transform."""
        node = self.node("ShaderNodeGamma")
        self.feed(node.inputs["Color"], self.vec("MAXIMUM", color, 0.0))
        node.inputs["Gamma"].default_value = 2.2
        return node.outputs[0]

    def emit(self, color, alpha=1.0):
        """Emission, cut by alpha. Returns the shader socket."""
        em = self.node("ShaderNodeEmission")
        self.feed(em.inputs["Color"], self.to_linear(color))
        em.inputs["Strength"].default_value = 1.0
        clear = self.node("ShaderNodeBsdfTransparent")
        mix = self.node("ShaderNodeMixShader")
        self.feed(mix.inputs[0], alpha)
        self.feed(mix.inputs[1], clear.outputs[0])
        self.feed(mix.inputs[2], em.outputs[0])
        return mix.outputs[0]


def _socket(tree, name, kind, in_out="INPUT", default=None):
    s = tree.interface.new_socket(name, in_out=in_out, socket_type=kind)
    if default is not None:
        s.default_value = default
    return s


def _group_node(g, tree):
    n = g.node("ShaderNodeGroup")
    n.node_tree = tree
    return n


# ---------------------------------------------------------------------------
# Key light and globals
# ---------------------------------------------------------------------------

def ensure_key_light(scene=None):
    scene = scene or bpy.context.scene
    obj = bpy.data.objects.get(LIGHT_NAME)
    if obj is None:
        data = bpy.data.lights.new(LIGHT_NAME, type="SUN")
        data.energy = 3.0
        obj = bpy.data.objects.new(LIGHT_NAME, data)
        obj.rotation_euler = (math.radians(55), 0.0, math.radians(-55))
        obj.location = (0.0, -2.0, 3.0)
    if obj.name not in scene.collection.all_objects:
        scene.collection.objects.link(obj)
    return obj


LIGHT_PROP = "tkb_light_dir"


def ensure_light_property(scene, light=None):
    """scene["tkb_light_dir"]: the key light's +Z axis, kept current by drivers."""
    light = light or ensure_key_light(scene)
    if LIGHT_PROP in scene and scene.animation_data and any(
            fc.data_path == f'["{LIGHT_PROP}"]' for fc in scene.animation_data.drivers):
        return
    scene[LIGHT_PROP] = [0.0, 0.0, 1.0]
    for i in range(3):
        fc = scene.driver_add(f'["{LIGHT_PROP}"]', i)
        fc.driver.type = "SCRIPTED"
        for var_name, channel in (("rx", "ROT_X"), ("ry", "ROT_Y"), ("rz", "ROT_Z")):
            v = fc.driver.variables.new()
            v.name = var_name
            v.type = "TRANSFORMS"
            v.targets[0].id = light
            v.targets[0].transform_type = channel
            v.targets[0].transform_space = "WORLD_SPACE"
        fc.driver.expression = _AXIS_EXPR[i]


def ensure_globals():
    tree = bpy.data.node_groups.get(GLOBALS_GROUP)
    if tree is not None:
        return tree
    light = ensure_key_light()
    tree = bpy.data.node_groups.new(GLOBALS_GROUP, "ShaderNodeTree")
    _socket(tree, "Light Direction", "NodeSocketVector", "OUTPUT")
    for name, kind, _ in GLOBALS:
        _socket(tree, name, "NodeSocketColor" if kind == "RGBA" else "NodeSocketFloat", "OUTPUT")
    g = _Graph(tree)
    out = g.node("NodeGroupOutput")

    # Read from a driven scene property: drivers inside node groups don't
    # evaluate reliably once the group is nested in another group at render.
    ensure_light_property(bpy.context.scene, light)
    axis = g.node("ShaderNodeAttribute", attribute_type="VIEW_LAYER", attribute_name=LIGHT_PROP)
    axis.name = "Light Axis"
    g.feed(out.inputs["Light Direction"], g.vec("NORMALIZE", axis.outputs["Vector"]))

    for name, kind, default in GLOBALS:
        node = g.node("ShaderNodeRGB" if kind == "RGBA" else "ShaderNodeValue")
        node.name = node.label = name
        node.outputs[0].default_value = default
        g.feed(out.inputs[name], node.outputs[0])
    return tree


def global_node(name):
    tree = ensure_globals()
    return tree.nodes.get(name)


# ---------------------------------------------------------------------------
# Shared pieces of the character and hair shaders
# ---------------------------------------------------------------------------

def _diffuse(g, I, G, N, L):
    """Albedo * the Dark->Bright shade ramp, as CharacterOutline/Hair do it."""
    albedo = g.vec("MULTIPLY", I["Main Color"], I["Color"])
    ndl = g.vec("DOT_PRODUCT", N, L)
    t = g.math("ADD", g.math("MULTIPLY", ndl, I["Shade On"]), 1.0)
    t = g.math("MULTIPLY", g.math("MULTIPLY", t, I["Shade Mask"]), 0.5)
    ramp = g.ramp(t, G["Diffuse Border"], G["Diffuse Blur"])
    shade = g.mix(I["Dark Color"], I["Bright Color"], ramp)
    k = g.math("SUBTRACT", 1.0, g.math("MULTIPLY", I["Shade Effect"], 0.5))
    shade = g.vec("ADD", g.vec("SCALE", shade, scale=I["Shade Effect"]), k)
    return albedo, shade


def _rim(g, I, G, N, V):
    ndv = g.sat(g.vec("DOT_PRODUCT", N, V))
    p = g.math("POWER", g.math("SUBTRACT", 1.0, ndv), G["Rim Power"])
    r = g.ramp(g.math("MULTIPLY", p, G["Rim Intensity"]), G["Rim Border"], G["Rim Blur"])
    return g.vec("SCALE", G["Rim Color"], scale=g.math("MULTIPLY", r, I["Rim Mask"]))


def _finish(g, I, G, color):
    """_LightInfluence blend toward the light colour, as the game's last step."""
    lit = g.vec("MULTIPLY", color, G["Light Color"])
    return g.mix(color, lit, I["Light Influence"])


_COMMON_INPUTS = (
    ("Main Color", "NodeSocketColor", (0.8, 0.8, 0.8, 1.0)),
    ("Alpha", "NodeSocketFloat", 1.0),
    ("Color", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ("Shade Mask", "NodeSocketFloat", 1.0),
    ("Dark Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0)),
    ("Bright Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0)),
    ("Shade Effect", "NodeSocketFloat", 0.2),
    ("Shade On", "NodeSocketFloat", 1.0),
    ("Rim Mask", "NodeSocketFloat", 1.0),
    ("Light Influence", "NodeSocketFloat", 1.0),
)


def _new_group(name, extra_inputs):
    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    _socket(tree, "Shader", "NodeSocketShader", "OUTPUT")
    _socket(tree, "Color", "NodeSocketColor", "OUTPUT")
    for n, kind, default in _COMMON_INPUTS + extra_inputs:
        _socket(tree, n, kind, default=default)
    g = _Graph(tree)
    gin = g.node("NodeGroupInput")
    gout = g.node("NodeGroupOutput")
    G = _group_node(g, ensure_globals()).outputs
    geo = g.node("ShaderNodeNewGeometry")
    return tree, g, gin.outputs, gout, G, geo


def ensure_character_group():
    tree = bpy.data.node_groups.get(CHAR_GROUP)
    if tree is not None:
        return tree
    tree, g, I, gout, G, geo = _new_group(CHAR_GROUP, (
        ("Spec On", "NodeSocketFloat", 0.0),
        ("Spec Mask", "NodeSocketFloat", 1.0),
        ("Spec Roughness", "NodeSocketFloat", 1.0),
        ("Spec Metallic", "NodeSocketFloat", 1.0),
        ("Roughness", "NodeSocketFloat", 1.0),
        ("Metallic", "NodeSocketFloat", 0.5),
        ("Specular Color", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ))
    N, V, L = geo.outputs["Normal"], geo.outputs["Incoming"], G["Light Direction"]
    albedo, shade = _diffuse(g, I, G, N, L)

    # Metallic only exists in the _SPECULAR_ON variant.
    metal = g.math("MULTIPLY", g.math("MULTIPLY", I["Spec Metallic"], I["Metallic"]), I["Spec On"])
    rough = g.math("MAXIMUM", g.math("MULTIPLY", I["Spec Roughness"], I["Roughness"]), 6.1e-5)
    diffuse_col = g.vec("SCALE", albedo, scale=g.math("SUBTRACT", 1.0, metal))
    f0 = g.vec("ADD", g.vec("SCALE", g.vec("SUBTRACT", albedo, 0.04), scale=metal), 0.04)
    color = g.vec("MULTIPLY", diffuse_col, shade)

    # GGX-ish highlight exactly as the game composes it, sqrt and all.
    H = g.vec("NORMALIZE", g.vec("ADD", L, V))
    ndl = g.sat(g.vec("DOT_PRODUCT", N, L))
    ndh = g.sat(g.vec("DOT_PRODUCT", N, H))
    ldh = g.sat(g.vec("DOT_PRODUCT", L, H))
    a = g.math("MULTIPLY", rough, rough)
    a2 = g.math("MULTIPLY", a, a)
    den = g.math("ADD", g.math("MULTIPLY", g.math("MULTIPLY", ndh, ndh), g.math("SUBTRACT", a2, 1.0)), 1.0)
    D = g.math("DIVIDE", a2, g.math("MAXIMUM", g.math("MULTIPLY", g.math("MULTIPLY", den, den), math.pi), 5.96e-8))
    k = g.math("MULTIPLY", a, 0.5)
    k2 = g.math("MULTIPLY", k, k)
    vis = g.math("DIVIDE", 1.0, g.math("ADD", g.math("MULTIPLY", g.math("MULTIPLY", ldh, ldh),
                                                                g.math("SUBTRACT", 1.0, k2)), k2))
    one_f0 = g.vec("SUBTRACT", 1.0, f0)
    fs = g.vec("ADD", f0, g.vec("SCALE", one_f0, scale=g.math("POWER", g.math("SUBTRACT", 1.0, ldh), 5.0)))
    gate = g.math("MULTIPLY", g.math("MULTIPLY", ndl, I["Spec Mask"]), I["Spec On"])
    s = g.math("MULTIPLY", g.math("SQRT", g.math("MULTIPLY", D, vis)), gate)
    spec = g.vec("MULTIPLY", g.vec("SCALE", I["Specular Color"], scale=s), g.sqrt3(fs))

    # Ambient + reflection, only in the specular variant and under its mask.
    ndv = g.math("MINIMUM", g.math("ABSOLUTE", g.vec("DOT_PRODUCT", N, V)), 1.0)
    fenv = g.vec("ADD", f0, g.vec("SCALE", one_f0, scale=g.math("POWER", g.math("SUBTRACT", 1.0, ndv), 5.0)))
    amb = g.vec("ADD", g.vec("MULTIPLY", diffuse_col, G["Ambient"]), g.vec("MULTIPLY", G["Reflection"], fenv))
    amb = g.vec("SCALE", amb, scale=g.math("MULTIPLY", I["Spec Mask"], I["Spec On"]))

    color = g.vec("ADD", g.vec("ADD", color, spec), amb)
    color = g.vec("ADD", color, _rim(g, I, G, N, V))
    color = _finish(g, I, G, color)
    g.feed(gout.inputs["Shader"], g.emit(color, I["Alpha"]))
    g.feed(gout.inputs["Color"], color)
    return tree


def ensure_ring_group():
    """View ray x head sphere -> direction for the ring cubemap (object space)."""
    tree = bpy.data.node_groups.get(RING_GROUP)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(RING_GROUP, "ShaderNodeTree")
    _socket(tree, "Vector", "NodeSocketVector", "OUTPUT")
    _socket(tree, "Head Center", "NodeSocketVector", default=(0.0, 0.0, 1.44))
    _socket(tree, "Head Radius", "NodeSocketFloat", default=0.12)
    _socket(tree, "Noise", "NodeSocketFloat", default=0.5)
    _socket(tree, "Noise Scale", "NodeSocketFloat", default=0.2)
    _socket(tree, "Noise Offset", "NodeSocketFloat", default=0.9)
    g = _Graph(tree)
    I = g.node("NodeGroupInput").outputs
    out = g.node("NodeGroupOutput")
    geo = g.node("ShaderNodeNewGeometry")
    tp = g.node("ShaderNodeVectorTransform", vector_type="POINT", convert_from="WORLD", convert_to="OBJECT")
    g.feed(tp.inputs[0], geo.outputs["Position"])
    tv = g.node("ShaderNodeVectorTransform", vector_type="VECTOR", convert_from="WORLD", convert_to="OBJECT")
    g.feed(tv.inputs[0], geo.outputs["Incoming"])
    V = g.vec("NORMALIZE", tv.outputs[0])

    d = g.vec("SUBTRACT", tp.outputs[0], I["Head Center"])
    along = g.math("MAXIMUM", g.vec("DOT_PRODUCT", V, d), 6.1e-5)
    p = g.vec("SUBTRACT", d, g.vec("SCALE", V, scale=along))
    r2 = g.math("MULTIPLY", I["Head Radius"], I["Head Radius"])
    pp = g.vec("DOT_PRODUCT", p, p)
    h = g.math("SQRT", g.math("MAXIMUM", g.math("SUBTRACT", r2, pp), 0.0))
    q = g.vec("NORMALIZE", g.vec("ADD", p, g.vec("SCALE", V, scale=h)))

    n = g.math("ADD", g.math("MULTIPLY", I["Noise"], I["Noise Scale"]), I["Noise Offset"])
    sep = g.node("ShaderNodeSeparateXYZ")
    g.feed(sep.inputs[0], q)
    comb = g.node("ShaderNodeCombineXYZ")
    # Unity jitters its X and Z (horizontal); in Blender those are X and Y.
    g.feed(comb.inputs[0], g.math("MULTIPLY", sep.outputs[0], n))
    g.feed(comb.inputs[1], g.math("MULTIPLY", sep.outputs[1], n))
    g.feed(comb.inputs[2], sep.outputs[2])
    g.feed(out.inputs[0], comb.outputs[0])
    return tree


def ensure_hair_group():
    tree = bpy.data.node_groups.get(HAIR_GROUP)
    if tree is not None:
        return tree
    tree, g, I, gout, G, geo = _new_group(HAIR_GROUP, (
        ("Ring", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0)),
        ("Ring Mask", "NodeSocketFloat", 1.0),
        ("Ring Color", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ))
    N, V, L = geo.outputs["Normal"], geo.outputs["Incoming"], G["Light Direction"]
    albedo, shade = _diffuse(g, I, G, N, L)
    ring = g.vec("SCALE", g.vec("MULTIPLY", I["Ring"], I["Ring Color"]), scale=I["Ring Mask"])
    color = g.vec("ADD", g.vec("MULTIPLY", albedo, shade), ring)
    color = g.vec("ADD", color, _rim(g, I, G, N, V))
    color = _finish(g, I, G, color)
    g.feed(gout.inputs["Shader"], g.emit(color, I["Alpha"]))
    g.feed(gout.inputs["Color"], color)
    return tree


def ensure_eye_group():
    tree = bpy.data.node_groups.get(EYE_GROUP)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(EYE_GROUP, "ShaderNodeTree")
    _socket(tree, "Shader", "NodeSocketShader", "OUTPUT")
    for n, kind, d in (("Sclera", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
                       ("Color", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
                       ("Iris", "NodeSocketColor", (0.3, 0.2, 0.3, 1.0)),
                       ("Iris Alpha", "NodeSocketFloat", 0.0),
                       ("Highlight", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
                       ("Highlight Alpha", "NodeSocketFloat", 0.0)):
        _socket(tree, n, kind, default=d)
    g = _Graph(tree)
    I = g.node("NodeGroupInput").outputs
    out = g.node("NodeGroupOutput")
    G = _group_node(g, ensure_globals()).outputs
    c = g.vec("MULTIPLY", I["Sclera"], I["Color"])
    c = g.mix(c, I["Iris"], I["Iris Alpha"])
    c = g.mix(c, I["Highlight"], I["Highlight Alpha"])
    g.feed(out.inputs[0], g.emit(g.vec("MULTIPLY", c, G["Light Color"])))
    return tree


def ensure_unlit_group():
    tree = bpy.data.node_groups.get(UNLIT_GROUP)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(UNLIT_GROUP, "ShaderNodeTree")
    _socket(tree, "Shader", "NodeSocketShader", "OUTPUT")
    _socket(tree, "Main Color", "NodeSocketColor", default=(1.0, 1.0, 1.0, 1.0))
    _socket(tree, "Alpha", "NodeSocketFloat", default=1.0)
    _socket(tree, "Color", "NodeSocketColor", default=(1.0, 1.0, 1.0, 1.0))
    g = _Graph(tree)
    I = g.node("NodeGroupInput").outputs
    out = g.node("NodeGroupOutput")
    g.feed(out.inputs[0], g.emit(g.vec("MULTIPLY", I["Main Color"], I["Color"]), I["Alpha"]))
    return tree


# ---------------------------------------------------------------------------
# Materials from the game's material data
# ---------------------------------------------------------------------------

def _prep_material(mat, blended=False):
    try:
        if not mat.use_nodes:
            mat.use_nodes = True
    except AttributeError:
        pass
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED" if blended else "DITHERED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND" if blended else "HASHED"
    mat.node_tree.nodes.clear()
    return mat.node_tree


def _image(src):
    if src is None:
        return None
    img = src if isinstance(src, bpy.types.Image) else bpy.data.images.load(src, check_existing=True)
    # Gamma-space shading: feed raw texel values, convert once at the end.
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "CHANNEL_PACKED"
    return img


def _flt(info, key, default):
    return info.get("floats", {}).get(key, default)


def _col(info, key, default):
    return tuple(info.get("colors", {}).get(key, default))


def _unity_to_blender(v):
    """Unity (x, y, z) -> Blender object space after the glTF/OBJ import (-x, -z, y)."""
    return (-v[0], -v[2], v[1])


def _uv_offset(g, uv_socket, offset):
    """Add a Unity-space UV offset (V runs the other way in Blender)."""
    if not any(offset):
        return uv_socket
    return g.vec("ADD", uv_socket, (offset[0], -offset[1], 0.0))


def build_prism_material(name, info, images, uv2=None, role=None, ring=None):
    """A material following the game's shader for `info` (a materials.json entry).

    images: texture name -> path or bpy Image. uv2: name of the second UV layer.
    """
    shader = (info.get("shader") or "").split("/")[-1]
    blended = _flt(info, "_BlendSrcFactor", 1) == 5 and _flt(info, "_BlendDstFactor", 0) == 10
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    tree = _prep_material(mat, blended=blended or shader == "FakeShadow")
    g = _Graph(tree)
    out = g.node("ShaderNodeOutputMaterial")
    tex = info.get("textures", {})

    uv_nodes = {}

    def uv(layer):
        if layer not in uv_nodes:
            n = g.node("ShaderNodeUVMap")
            if layer:
                n.uv_map = layer
            uv_nodes[layer] = n.outputs[0]
        return uv_nodes[layer]

    def sample(prop, layer=None, offset=(0.0, 0.0)):
        t = tex.get(prop)
        img = _image(images.get(t["name"])) if t else None
        if img is None:
            return None
        n = g.node("ShaderNodeTexImage")
        n.image = img
        n.label = prop
        if layer or any(offset):
            g.feed(n.inputs["Vector"], _uv_offset(g, uv(layer), offset))
        return n

    def channel(node, i):
        sep = g.node("ShaderNodeSeparateColor")
        g.feed(sep.inputs[0], node.outputs["Color"])
        return sep.outputs[i]

    if shader == "FakeShadow":
        clear = g.node("ShaderNodeBsdfTransparent")
        g.feed(clear.inputs["Color"], g.to_linear(_col(info, "_Color", (0.89, 0.75, 0.72, 1.0))))
        g.feed(out.inputs["Surface"], clear.outputs[0])
        mat["tkb_shader"] = shader
        return mat

    if shader == "EyeBall":
        grp = _group_node(g, ensure_eye_group())
        iris_uv = _col(info, "_IrisUV", (0, 0, 0, 0))
        base = sample("_BaseTex")
        iris = sample("_IrisTex", None, iris_uv[0:2])
        high = sample("_HighLightTex", None, iris_uv[2:4])
        if base:
            g.feed(grp.inputs["Sclera"], base.outputs["Color"])
        if iris:
            g.feed(grp.inputs["Iris"], iris.outputs["Color"])
            g.feed(grp.inputs["Iris Alpha"], iris.outputs["Alpha"])
        if high:
            g.feed(grp.inputs["Highlight"], high.outputs["Color"])
            g.feed(grp.inputs["Highlight Alpha"], high.outputs["Alpha"])
        g.feed(grp.inputs["Color"], _col(info, "_Color", (1, 1, 1, 1)))
        g.feed(out.inputs["Surface"], grp.outputs["Shader"])
        mat["tkb_shader"] = shader
        return mat

    if shader in ("Hair",):
        grp = _group_node(g, ensure_hair_group())
    elif shader in ("Character Unlit", "Unlit"):
        grp = _group_node(g, ensure_unlit_group())
    else:
        grp = _group_node(g, ensure_character_group())
    grp.name = "Prism"
    grp.width = 240
    I = grp.inputs

    main = sample("_MainTex")
    if main:
        g.feed(I["Main Color"], main.outputs["Color"])
    alpha = main.outputs["Alpha"] if main and (blended or _flt(info, "_UseAlphaClip", 0)) else 1.0
    if alpha != 1.0 and _flt(info, "_UseAlphaClip", 0) and not blended:
        alpha = g.math("GREATER_THAN", alpha, _flt(info, "_Cutoff", 0.5))
    g.feed(I["Alpha"], alpha)
    g.feed(I["Color"], _col(info, "_Color", (1, 1, 1, 1)))

    if "Shade Mask" in I:
        shade = sample("_ShadeTex")
        if shade:
            g.feed(I["Shade Mask"], channel(shade, 0))
        if role in ("skin", "face"):
            g.feed(I["Dark Color"], _group_node(g, ensure_globals()).outputs["Skin Shadow"])
        else:
            g.feed(I["Dark Color"], _col(info, "_DarkColor", (0.5, 0.5, 0.5, 1)))
        g.feed(I["Bright Color"], _col(info, "_BrightColor", (0.5, 0.5, 0.5, 1)))
        g.feed(I["Shade Effect"], _flt(info, "_ShadeEffect", 0.2))
        g.feed(I["Shade On"], _flt(info, "_ShadeOn", 1.0))
        g.feed(I["Light Influence"], _flt(info, "_LightInfluence", 1.0))
        rim = sample("_RimEffectTex")
        if rim:
            g.feed(I["Rim Mask"], channel(rim, 0))

    if "Spec On" in I:
        spec_on = 1.0 if ("_SPECULAR_ON" in info.get("keywords", []) or _flt(info, "_SPECULAR", 0)) else 0.0
        g.feed(I["Spec On"], spec_on)
        sp = sample("_SpecularMap")
        if sp and spec_on:
            for i, key in enumerate(("Spec Mask", "Spec Roughness", "Spec Metallic")):
                g.feed(I[key], channel(sp, i))
        g.feed(I["Roughness"], _flt(info, "_Roughness", 1.0))
        g.feed(I["Metallic"], _flt(info, "_Metallic", 0.5))
        g.feed(I["Specular Color"], _col(info, "_SpecularColor", (1, 1, 1, 1)))

    if "Ring" in I:
        ring_tex = tex.get("_AngelRingTex")
        ring_img = _image(images.get(ring_tex["name"])) if ring_tex else None
        if ring_img is None:
            ring_img = procedural_ring_image()
        vec = _group_node(g, ensure_ring_group())
        # The controller's live values beat the material's editor snapshot.
        center = ring["center"] if ring else _col(info, "_CenterPos", (0.0, 1.44, 0.0, 0.0))
        g.feed(vec.inputs["Head Center"], _unity_to_blender(center))
        g.feed(vec.inputs["Head Radius"], ring["radius"] if ring else _flt(info, "_Radius", 0.12))
        g.feed(vec.inputs["Noise Scale"], _flt(info, "_AngelRingNoiseScale", 0.2))
        g.feed(vec.inputs["Noise Offset"], _flt(info, "_AngelRingNoiseOffset", 0.9))
        noise = sample("_NoiseTex", uv2)
        if noise:
            g.feed(vec.inputs["Noise"], channel(noise, 0))
        env = g.node("ShaderNodeTexEnvironment")
        env.image = ring_img
        g.feed(env.inputs["Vector"], vec.outputs[0])
        g.feed(I["Ring"], env.outputs["Color"])
        mask = sample("_MaskTex", uv2)
        if mask:
            g.feed(I["Ring Mask"], mask.outputs["Alpha"])
        g.feed(I["Ring Color"], _col(info, "_AngelRingColor", (1, 1, 1, 1)))

    g.feed(out.inputs["Surface"], grp.outputs["Shader"])

    # What the outline pass needs, read later by add_outline.
    outline = shader == "Hair" or (shader == "CharacterOutline" and _flt(info, "_OutlineSettingGroups", 1.0))
    mat["tkb_shader"] = shader
    mat["tkb_role"] = role or ""
    mat["tkb_outline"] = bool(outline) and not blended
    mat["tkb_outline_width"] = _flt(info, "_OutlineWidth", 0.0005)
    mat["tkb_outline_adjust"] = _flt(info, "_OutlineAdjust", 1.0)
    mat["tkb_outline_color"] = list(_col(info, "_OutlineColor", (0, 0, 0, 1)))
    lm = tex.get("_OutlineTex")
    mat["tkb_outline_tex"] = lm["name"] if lm and lm["name"] in images else ""
    return mat


_RING_IMAGE = "TKB ring (procedural)"


def procedural_ring_image():
    """Stand-in for the game's ring cubemap: the same band, measured from it."""
    img = bpy.data.images.get(_RING_IMAGE)
    if img is not None:
        return img
    w, h = 64, 256
    img = bpy.data.images.new(_RING_IMAGE, w, h)
    px = []
    for r in range(h):  # Blender rows run bottom (elevation -90) to top (+90)
        e = math.sin(((r + 0.5) / h - 0.5) * math.pi)
        v = 0.28 * _smooth(0.615, 0.645, e) * (1 - _smooth(0.725, 0.75, e)) + (0.031 if e > 0.83 else 0.0)
        px += [v, v, v, 1.0] * w
    img.pixels.foreach_set(px)
    img.colorspace_settings.name = "Non-Color"
    img.pack()
    return img


def _smooth(lo, hi, x):
    t = min(max((x - lo) / (hi - lo), 0.0), 1.0)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------------------
# Outline: inverted hull, flat colour, width per material and texture mask
# ---------------------------------------------------------------------------

def build_outline_material(source):
    name = f"{source.name} (outline)"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    tree = _prep_material(mat)
    mat.use_backface_culling = True
    g = _Graph(tree)
    out = g.node("ShaderNodeOutputMaterial")
    clear = g.node("ShaderNodeBsdfTransparent")
    if not source.get("tkb_outline", True):
        g.feed(out.inputs["Surface"], clear.outputs[0])
    else:
        G = _group_node(g, ensure_globals()).outputs
        color = list(source.get("tkb_outline_color", (0.3, 0.22, 0.25, 1.0)))
        em = g.node("ShaderNodeEmission")
        g.feed(em.inputs["Color"], g.to_linear(g.vec("MULTIPLY", tuple(color), G["Light Color"])))
        geo = g.node("ShaderNodeNewGeometry")
        # Flipped hull: keep only faces now pointing at the camera.
        side = g.node("ShaderNodeMixShader")
        g.feed(side.inputs[0], geo.outputs["Backfacing"])
        g.feed(side.inputs[1], em.outputs[0])
        g.feed(side.inputs[2], clear.outputs[0])
        g.feed(out.inputs["Surface"], side.outputs[0])
    mat["tkb_is_outline"] = True
    return mat


# The game pushes the hull back in depth by _OutlineAdjust * a stage constant;
# this is that constant in metres, chosen so the hull hides in concave areas.
OUTLINE_BIAS_PER_ADJUST = 0.001
OUTLINE_HULL_GROUP = "TKB Outline Hull"


def ensure_outline_hull():
    """Geometry Nodes: the game's FORWARD_OUTLINE vertex shader.

    Extrude along the baked outline direction, flattened into the camera's
    view plane, scaled by width * mask; push back from the camera by a bias;
    flip; shift material indices onto the outline slots; join with the source.
    """
    tree = bpy.data.node_groups.get(OUTLINE_HULL_GROUP)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(OUTLINE_HULL_GROUP, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for name, kind in (("Width", "NodeSocketFloat"), ("Bias", "NodeSocketFloat"),
                       ("Camera", "NodeSocketObject"), ("Material Offset", "NodeSocketInt")):
        tree.interface.new_socket(name, in_out="INPUT", socket_type=kind)
    g = _Graph(tree)
    I = g.node("NodeGroupInput").outputs
    out = g.node("NodeGroupOutput")

    def attr(name, kind):
        n = g.node("GeometryNodeInputNamedAttribute", data_type=kind)
        n.inputs["Name"].default_value = name
        return n.outputs["Attribute"]

    cam = g.node("GeometryNodeObjectInfo", transform_space="RELATIVE")
    g.feed(cam.inputs["Object"], I["Camera"])
    pos = g.node("GeometryNodeInputPosition").outputs[0]
    V = g.vec("NORMALIZE", g.vec("SUBTRACT", cam.outputs["Location"], pos))
    d = g.vec("NORMALIZE", attr("tkb_outline_dir", "FLOAT_VECTOR"))
    d = g.vec("SUBTRACT", d, g.vec("SCALE", V, scale=g.vec("DOT_PRODUCT", d, V)))
    w = g.math("MULTIPLY", I["Width"], attr("tkb_outline_w", "FLOAT"))
    offset = g.vec("SUBTRACT", g.vec("SCALE", d, scale=w), g.vec("SCALE", V, scale=I["Bias"]))

    setpos = g.node("GeometryNodeSetPosition")
    g.feed(setpos.inputs["Geometry"], I["Geometry"])
    g.feed(setpos.inputs["Offset"], offset)
    flip = g.node("GeometryNodeFlipFaces")
    g.feed(flip.inputs["Mesh"], setpos.outputs["Geometry"])
    idx = g.node("GeometryNodeInputMaterialIndex").outputs[0]
    setmat = g.node("GeometryNodeSetMaterialIndex")
    g.feed(setmat.inputs["Geometry"], flip.outputs["Mesh"])
    g.feed(setmat.inputs["Material Index"], g.math("ADD", idx, I["Material Offset"]))
    join = g.node("GeometryNodeJoinGeometry")
    g.feed(join.inputs[0], setmat.outputs["Geometry"])
    g.feed(join.inputs[0], I["Geometry"])
    g.feed(out.inputs["Geometry"], join.outputs[0])
    return tree


def _outline_attributes(obj, mask_image=None):
    """tkb_outline_dir / tkb_outline_w on the mesh's points.

    Direction: the game's baked tangent when the mesh has one, else normals
    averaged over coincident positions (what the game builds for the rest).
    Weight: the outline texture's red channel at the vertex's UV.
    """
    me = obj.data
    n = len(me.vertices)
    dirs = [0.0] * (n * 3)
    baked = me.attributes.get("_OUTLINE_DIR")
    if baked is not None and baked.domain == "POINT":
        raw = [0.0] * (n * 3)
        baked.data.foreach_get("vector", raw)
        for i in range(n):  # glTF Y-up -> Blender Z-up; custom attributes arrive unconverted
            dirs[3 * i:3 * i + 3] = (raw[3 * i], -raw[3 * i + 2], raw[3 * i + 1])
    else:
        sums = {}
        key_of = []
        normals = me.corner_normals if hasattr(me, "corner_normals") else None
        vnorm = [[0.0, 0.0, 0.0] for _ in range(n)]
        for loop in me.loops:
            nv = normals[loop.index].vector if normals is not None else loop.normal
            acc = vnorm[loop.vertex_index]
            acc[0] += nv[0]; acc[1] += nv[1]; acc[2] += nv[2]
        for v in me.vertices:
            k = (round(v.co.x, 5), round(v.co.y, 5), round(v.co.z, 5))
            key_of.append(k)
            s = sums.setdefault(k, [0.0, 0.0, 0.0])
            nv = vnorm[v.index]
            s[0] += nv[0]; s[1] += nv[1]; s[2] += nv[2]
        for i, k in enumerate(key_of):
            dirs[3 * i:3 * i + 3] = sums[k]

    weights = [1.0] * n
    if mask_image is not None and me.uv_layers and mask_image.size[0]:
        w, h = mask_image.size
        px = [0.0] * (w * h * 4)
        mask_image.pixels.foreach_get(px)
        uv = me.uv_layers[0].data
        seen = [False] * n
        for loop in me.loops:
            vi = loop.vertex_index
            u, v = uv[loop.index].uv
            x = min(w - 1, max(0, int((u % 1.0) * w)))
            y = min(h - 1, max(0, int((v % 1.0) * h)))
            val = px[(y * w + x) * 4]
            weights[vi] = val if not seen[vi] else min(weights[vi], val)
            seen[vi] = True

    for name, kind, data, field in (("tkb_outline_dir", "FLOAT_VECTOR", dirs, "vector"),
                                    ("tkb_outline_w", "FLOAT", weights, "value")):
        a = me.attributes.get(name) or me.attributes.new(name, kind, "POINT")
        a.data.foreach_set(field, data)


def ensure_camera(scene=None):
    scene = scene or bpy.context.scene
    if scene.camera is None:
        data = bpy.data.cameras.new("TKB Camera")
        data.lens = 70
        cam = bpy.data.objects.new("TKB Camera", data)
        cam.location = (1.1, -3.9, 1.3)
        cam.rotation_euler = (math.radians(85), 0.0, math.radians(16))
        scene.collection.objects.link(cam)
        scene.camera = cam
    return scene.camera


def add_outline(obj, width=None, images=None):
    if obj.type != "MESH":
        return
    remove_outline(obj)
    mats = [m for m in obj.data.materials if m is not None and not m.get("tkb_is_outline")]
    if not any(m.get("tkb_outline", True) for m in mats):
        return
    count = len(obj.data.materials)
    for m in list(obj.data.materials[:count]):
        obj.data.materials.append(build_outline_material(m) if m is not None else None)
    first = next(m for m in mats if m.get("tkb_outline", True))
    tex_name = first.get("tkb_outline_tex", "")
    mask = _image(images[tex_name]) if tex_name and images and tex_name in images else None
    _outline_attributes(obj, mask)

    scale = global_node("Outline Scale").outputs[0].default_value
    tree = ensure_outline_hull()
    mod = obj.modifiers.new(OUTLINE_MOD, "NODES")
    mod.node_group = tree
    _set_input(mod, "Width", (width if width is not None else first.get("tkb_outline_width", 0.0015)) * scale)
    _set_input(mod, "Bias", max(first.get("tkb_outline_adjust", 1.0), 0.5) * OUTLINE_BIAS_PER_ADJUST)
    _set_input(mod, "Camera", ensure_camera())
    _set_input(mod, "Material Offset", count)


def _set_input(mod, name, value):
    """Set a Geometry Nodes modifier input (Blender 4.x id-props, 5.x .properties)."""
    ident = next(s.identifier for s in mod.node_group.interface.items_tree
                 if s.item_type == "SOCKET" and s.in_out == "INPUT" and s.name == name)
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        getattr(props.inputs, ident).value = value
    else:
        mod[ident] = value


def remove_outline(obj):
    mod = obj.modifiers.get(OUTLINE_MOD)
    if mod is not None:
        obj.modifiers.remove(mod)
    if obj.type == "MESH":
        for i in reversed(range(len(obj.data.materials))):
            m = obj.data.materials[i]
            if m is not None and m.get("tkb_is_outline"):
                obj.data.materials.pop(index=i)


def add_fake_shadow(obj, mat, offset):
    """The hair's FakeShadow pass: a copy pushed `offset` away from the key light."""
    light = ensure_key_light()
    shadow = bpy.data.objects.new(f"{obj.name} (fake shadow)", obj.data)
    for c in obj.users_collection:
        c.objects.link(shadow)
    shadow.parent = obj.parent
    shadow.matrix_world = obj.matrix_world
    for slot in shadow.material_slots:
        slot.link = "OBJECT"
        slot.material = mat
    for i in range(3):
        fc = shadow.driver_add("delta_location", i)
        fc.driver.type = "SCRIPTED"
        for var_name, channel in (("rx", "ROT_X"), ("ry", "ROT_Y"), ("rz", "ROT_Z")):
            v = fc.driver.variables.new()
            v.name = var_name
            v.type = "TRANSFORMS"
            v.targets[0].id = light
            v.targets[0].transform_type = channel
            v.targets[0].transform_space = "WORLD_SPACE"
        fc.driver.expression = f"-({_AXIS_EXPR[i]}) * {offset!r}"
    shadow.visible_shadow = False
    return shadow


def ensure_decal_group():
    """Geometry Nodes: lay a mesh onto a skin object (stand-in for the game's stencil).

    Every vertex moves to the nearest point of the skin surface, lifted `Lift` back
    toward where it came from; vertices farther than `Max Distance` from the skin
    (hair hanging clear of the face) are dropped.
    """
    tree = bpy.data.node_groups.get(DECAL_GROUP)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(DECAL_GROUP, "GeometryNodeTree")
    _socket(tree, "Geometry", "NodeSocketGeometry", "OUTPUT")
    _socket(tree, "Geometry", "NodeSocketGeometry")
    _socket(tree, "Skin", "NodeSocketObject")
    _socket(tree, "Max Distance", "NodeSocketFloat", default=0.03)
    _socket(tree, "Lift", "NodeSocketFloat", default=0.0006)
    nodes, links = tree.nodes, tree.links
    gin = nodes.new("NodeGroupInput")
    gout = nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "RELATIVE"
    links.new(gin.outputs["Skin"], info.inputs["Object"])
    prox = nodes.new("GeometryNodeProximity")
    prox.target_element = "FACES"
    links.new(info.outputs["Geometry"], prox.inputs["Target"])
    pos = nodes.new("GeometryNodeInputPosition")
    links.new(pos.outputs[0], prox.inputs["Source Position"])

    far = nodes.new("FunctionNodeCompare")
    far.data_type, far.operation = "FLOAT", "GREATER_THAN"
    links.new(prox.outputs["Distance"], far.inputs["A"])
    links.new(gin.outputs["Max Distance"], far.inputs["B"])
    delete = nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    links.new(gin.outputs["Geometry"], delete.inputs["Geometry"])
    links.new(far.outputs["Result"], delete.inputs["Selection"])

    # nearest + normalize(position - nearest) * lift
    away = nodes.new("ShaderNodeVectorMath")
    away.operation = "SUBTRACT"
    links.new(pos.outputs[0], away.inputs[0])
    links.new(prox.outputs["Position"], away.inputs[1])
    unit = nodes.new("ShaderNodeVectorMath")
    unit.operation = "NORMALIZE"
    links.new(away.outputs[0], unit.inputs[0])
    lift = nodes.new("ShaderNodeVectorMath")
    lift.operation = "SCALE"
    links.new(unit.outputs[0], lift.inputs[0])
    links.new(gin.outputs["Lift"], lift.inputs["Scale"])
    target = nodes.new("ShaderNodeVectorMath")
    target.operation = "ADD"
    links.new(prox.outputs["Position"], target.inputs[0])
    links.new(lift.outputs[0], target.inputs[1])
    setp = nodes.new("GeometryNodeSetPosition")
    links.new(delete.outputs["Geometry"], setp.inputs["Geometry"])
    links.new(target.outputs[0], setp.inputs["Position"])
    links.new(setp.outputs["Geometry"], gout.inputs["Geometry"])
    return tree


def attach_fake_shadow_decal(shadow, skin):
    """Restrict a fake-shadow copy to `skin` (see ensure_decal_group)."""
    mod = shadow.modifiers.get(DECAL_MOD) or shadow.modifiers.new(DECAL_MOD, "NODES")
    mod.node_group = ensure_decal_group()
    _set_input(mod, "Skin", skin)
    return mod


def skin_object(objects):
    """The face mesh among `objects`: the one carrying a face-role material."""
    for o in objects:
        if o.type == "MESH" and any(m is not None and m.get("tkb_role") == "face" for m in o.data.materials):
            return o
    return next((o for o in objects if o.type == "MESH" and o.name.split(".")[0] == "face"), None)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def find_extract(folder):
    """The prism_extract.py output for an export folder, if it has been run."""
    for d in (os.path.join(folder, "blender"), folder):
        if os.path.isfile(os.path.join(d, "materials.json")) and os.path.isfile(os.path.join(d, "model.glb")):
            return d
    return None


def import_prism(folder, idol=None, outline=True):
    """Import prism_extract.py output: exact parts, materials and values from the game."""
    folder = bpy.path.abspath(folder)
    data_dir = find_extract(folder)
    with open(os.path.join(data_dir, "materials.json"), encoding="utf-8") as f:
        data = json.load(f)
    idol = idol or data.get("idol") or idol_from_path(folder)
    images = {n: os.path.join(data_dir, p) for n, p in data.get("textures", {}).items()}

    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(data_dir, "model.glb"))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = {o.name: o for o in new if o.type == "MESH"}

    label = IDOLS.get(idol, data.get("source", "model"))
    coll = bpy.data.collections.new(f"Tenkabana {label}")
    bpy.context.scene.collection.children.link(coll)
    root = bpy.data.objects.new(f"TKB {label}", None)
    coll.objects.link(root)
    for o in new:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
        if o.parent is None:
            o.parent = root

    built = {}
    shadows = []
    role_of = {m: r for r, m in data.get("roles", {}).items()}
    for entry in data["objects"]:
        obj = meshes.get(entry["name"])
        if obj is None:
            continue
        uv2 = obj.data.uv_layers[1].name if len(obj.data.uv_layers) > 1 else None
        for i, slot in enumerate(obj.material_slots):
            mname = entry["materials"][min(i, len(entry["materials"]) - 1)]
            info = data["materials"].get(mname)
            if info is None:
                continue
            key = (mname, uv2)
            if key not in built:
                built[key] = build_prism_material(f"TKB {mname}", info, images, uv2, role_of.get(mname),
                                                  data.get("angel_ring"))
            old = slot.material
            slot.material = built[key]
            if old is not None and old.users == 0:
                bpy.data.materials.remove(old)
        if outline:
            add_outline(obj, images=images)
        for extra in entry.get("extra_passes", []):
            info = data["materials"].get(extra, {})
            if (info.get("shader") or "").endswith("FakeShadow"):
                fake = build_prism_material(f"TKB {extra}", info, images)
                shadows.append(add_fake_shadow(obj, fake, info.get("floats", {}).get("_FakeShadowOffset", 0.006)))

    skin = skin_object(new)
    if skin is not None:
        for shadow in shadows:
            attach_fake_shadow_decal(shadow, skin)
    if idol is not None:
        apply_body([root], idol, new)
    setup_scene()
    return new


# ---------------------------------------------------------------------------
# Fallback: plain OBJ exports, matched by name
# ---------------------------------------------------------------------------

_MAP_RE = re.compile(r"^(?P<base>.+?)_(?P<kind>col|smask|rmask|spmap|rnoise)(?:_\d+)?$", re.IGNORECASE)

_PARTS = (
    ("hair", ("hair", "atama", "bang", "maegami", "ushiro", "tail", "backhead", "inback"), ("hair",)),
    ("eye", ("eyesclera",), ("eye",)),
    ("face", ("face", "eye", "mouth", "mayu", "brow", "teeth", "tooth", "tongue",
              "kao", "lash", "hitomi", "cheek"), ("face",)),
    ("skin", ("body", "skin", "hand", "arm", "leg", "neck"), ("body", "skin")),
    ("dress", ("dress", "cloth", "skirt", "shirt", "coat", "shoe", "boot", "sock",
               "_bs", "bs_", "opacity"), ("dress",)),
    ("acce", ("acce", "ribbon", "hat", "ring", "earring", "gold"), ("acce",)),
)

# What the game's materials typically hold per part, for when no
# materials.json exists. Dress values are Amana's 14mgar dress.
_PART_DEFAULTS = {
    "default": {"_DarkColor": (0.27, 0.14, 0.45, 1), "_ShadeEffect": 0.2, "_OutlineColor": (0.09, 0.09, 0.12, 1),
                "_OutlineWidth": 0.001},
    "dress": {"_DarkColor": (0.27, 0.14, 0.45, 1), "_ShadeEffect": 0.2, "_OutlineColor": (0.09, 0.09, 0.12, 1),
              "_OutlineWidth": 0.001},
    "acce": {"_DarkColor": (0.5, 0.5, 0.5, 1), "_ShadeEffect": 0.0, "_OutlineColor": (0.15, 0.11, 0.23, 1),
             "_OutlineWidth": 0.001, "_Metallic": 0.4},
    "skin": {"_DarkColor": (0.5, 0.5, 0.5, 1), "_ShadeEffect": 1.0, "_OutlineColor": (0.44, 0.35, 0.3, 1),
             "_OutlineWidth": 0.001},
    "face": {"_DarkColor": (0.5, 0.5, 0.5, 1), "_ShadeEffect": 1.0, "_OutlineColor": (0.44, 0.35, 0.3, 1),
             "_OutlineWidth": 0.002},
    "hair": {"_DarkColor": (0.5, 0.5, 0.5, 1), "_ShadeEffect": 0.2, "_ShadeOn": 0.0,
             "_OutlineColor": (0.29, 0.13, 0.16, 1), "_OutlineWidth": 0.002},
}


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
                sets.setdefault(m["base"], {}).setdefault(m["kind"].lower(), os.path.join(root, f))
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


def build_toon_material(name, maps, part="default"):
    """A Prism material from loose maps ({'col': path_or_image, ...}) and part defaults."""
    d = _PART_DEFAULTS.get(part, _PART_DEFAULTS["default"])
    info = {
        "shader": "Anim2D3D/" + ("Hair" if part == "hair" else "CharacterOutline"),
        "keywords": ["_SPECULAR_ON"] if "spmap" in maps else [],
        "textures": {}, "floats": {}, "colors": {},
    }
    for k, v in d.items():
        (info["colors"] if isinstance(v, tuple) else info["floats"])[k] = v
    images = {}
    for kind, prop in (("col", "_MainTex"), ("rmask", "_RimEffectTex"),
                       ("spmap", "_SpecularMap"), ("rnoise", "_NoiseTex")):
        if kind in maps:
            key = f"{name}:{kind}"
            images[key] = maps[kind]
            info["textures"][prop] = {"name": key}
    return build_prism_material(name, info, images, role=part if part in ("skin", "face") else None)


def toonify_object(obj, sets, idol=None, outline=True, width=None):
    if obj.type != "MESH":
        return []
    remove_outline(obj)
    used = []
    part = part_of(obj.name)
    slots = obj.data.materials
    if len(slots) == 0:
        slots.append(None)
    for i in range(len(slots)):
        base = best_set(sets, part, idol)
        if base is None:
            continue
        slots[i] = build_toon_material(f"TKB {base}", sets[base], part)
        used.append(base)
    if outline and used:
        add_outline(obj, width)
    return used


def apply_body(roots, idol, objects=None):
    """Height as uniform scale on the roots; bust onto any matching shape key."""
    body = BODY.get(idol)
    if not body:
        return
    for obj in roots:
        if obj.parent is None:
            obj.scale = tuple(s * body["height"] for s in obj.scale)
    for obj in objects or roots:
        keys = getattr(getattr(obj, "data", None), "shape_keys", None)
        if keys:
            for kb in keys.key_blocks:
                # 'bust_up' on the costume's body and coat; the game drives it
                # with the idol's bust parameter (0..1 of the full shape).
                if any(t in kb.name.lower() for t in ("bust", "mune", "breast")):
                    kb.value = body["bust"]


def import_export_folder(folder, idol=None, outline=True, width=None):
    """Import a Tenkabana export. Uses prism_extract.py output when present."""
    folder = bpy.path.abspath(folder)
    if find_extract(folder):
        return import_prism(folder, idol, outline)

    idol = idol if idol is not None else idol_from_path(folder)
    before = set(bpy.data.objects)
    models = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs
              if f.lower().endswith((".glb", ".gltf", ".fbx"))]
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
    ensure_light_property(scene, ensure_key_light(scene))
    ensure_globals()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

_set_items = []


def _texture_set_items(self, context):
    global _set_items
    sets = scan_textures(bpy.path.abspath(context.scene.tkb.texture_dir))
    # Blender needs these strings kept alive while the menu is open.
    _set_items = [(b, b, ", ".join(sorted(sets[b]))) for b in sorted(sets)] or [("NONE", "No texture sets", "")]
    return _set_items


def _idol_items(self, context):
    return [("0", "Auto (from folder name)", "")] + [
        (str(i), f"{i:02d} {n.capitalize()}", "") for i, n in IDOLS.items()]


class TKB_Settings(bpy.types.PropertyGroup):
    texture_dir: bpy.props.StringProperty(
        name="Export folder", subtype="DIR_PATH",
        description="A Tenkabana export folder (with blender/ from prism_extract.py, or meshes/ and textures/)")
    idol: bpy.props.EnumProperty(name="Idol", items=_idol_items)
    outline: bpy.props.BoolProperty(name="Outline", default=True)
    outline_width: bpy.props.FloatProperty(
        name="Width", default=0.0015, min=0.0, max=0.02, precision=4, unit="LENGTH")


def _settings_idol(s):
    return int(s.idol) or idol_from_path(bpy.path.abspath(s.texture_dir))


class TKB_OT_import(bpy.types.Operator):
    bl_idname = "tenkabana.import_export"
    bl_label = "Import Export Folder"
    bl_description = "Import the export and give it the game's shading"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.tkb
        if not os.path.isdir(bpy.path.abspath(s.texture_dir)):
            self.report({"ERROR"}, "Set the export folder first")
            return {"CANCELLED"}
        exact = find_extract(bpy.path.abspath(s.texture_dir)) is not None
        objs = import_export_folder(s.texture_dir, _settings_idol(s), s.outline, None)
        self.report({"INFO"} if exact else {"WARNING"},
                    f"Imported {len(objs)} objects" + ("" if exact else
                    " by name matching. Run prism_extract.py on the folder for the game's exact materials."))
        return {"FINISHED"}


class TKB_OT_toonify(bpy.types.Operator):
    bl_idname = "tenkabana.toonify"
    bl_label = "Toonify Selected"
    bl_description = "Replace the selected objects' materials, matching textures by name"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.tkb
        sets = scan_textures(bpy.path.abspath(s.texture_dir))
        n = sum(len(toonify_object(o, sets, _settings_idol(s), s.outline, s.outline_width))
                for o in context.selected_objects)
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
        maps = scan_textures(bpy.path.abspath(s.texture_dir)).get(self.texture_set)
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
        for o in context.selected_objects:
            if self.enable:
                add_outline(o)
            else:
                remove_outline(o)
        return {"FINISHED"}


class TKB_OT_scene(bpy.types.Operator):
    bl_idname = "tenkabana.setup_scene"
    bl_label = "Set Up Scene"
    bl_description = "Standard view transform, background, key light and stage globals"
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
        folder = bpy.path.abspath(s.texture_dir)
        if s.texture_dir and os.path.isdir(folder) and not find_extract(folder):
            col.label(text="No prism_extract.py output:", icon="INFO")
            col.label(text="falls back to name matching")
        col.separator()
        col.operator(TKB_OT_toonify.bl_idname, icon="SHADING_RENDERED")
        col.operator(TKB_OT_assign.bl_idname, icon="TEXTURE")
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
        tree = bpy.data.node_groups.get(GLOBALS_GROUP)
        if tree is not None:
            box = self.layout.box()
            box.label(text="Stage (game globals)", icon="WORLD_DATA")
            for name, _, _ in GLOBALS:
                node = tree.nodes.get(name)
                if node is not None:
                    box.prop(node.outputs[0], "default_value", text=name)


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
