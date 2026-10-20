"""Read a Tenkabana export's .unity3d bundles and write what Blender needs.

    pip install UnityPy
    python prism_extract.py <export folder> [--out <folder>]

Writes <export>/blender/ (or --out):

    model.glb        every renderer as its own object, in the bind pose, with
                     both UV sets, one material slot per submesh and the
                     outline directions the game bakes into tangents
    materials.json   which object uses which material, and every material's
                     shader, textures, floats and colours as the game set them
    textures/*.png   the textures those materials reference, plus
                     <cubemap>_equirect.png for cubemaps (the hair's angel ring)

tenkabana_toon.py picks these up on Import and builds the game's shading from
them instead of guessing from file names.

Standalone CPython, not Blender's: UnityPy ships compiled decoders.
"""

import argparse
import json
import math
import os
import re
import struct
import sys

try:
    import UnityPy
    from UnityPy.helpers.MeshHelper import MeshHandler
except ImportError:
    sys.exit("UnityPy is required:  pip install UnityPy")


# Parts the base body ships with a placeholder ('Lit') material. At runtime the
# game hands them the costume's face / eye / skin material, which the prefab
# keeps on otherwise empty holder renderers named after the role.
RUNTIME_ROLES = (
    ("face", re.compile(r"^(face|faceline|eyebrow|eyelash|mouthline|teeth|tongue)", re.I)),
    ("eye", re.compile(r"^eyesclera", re.I)),
    ("skin", re.compile(r"^bodyskin", re.I)),
    ("hair", re.compile(r"^inback", re.I)),
)


def holder_role(name):
    low = name.lower()
    if low.startswith("m_face"):
        return "face"
    if low.startswith("m_eye"):
        return "eye"
    if "_skin_" in low:
        return "skin"
    return None


def pptr_read(p):
    try:
        if p is None or not p.path_id:
            return None
        return p.read()
    except Exception:
        return None


def material_info(mat):
    sp = mat.m_SavedProperties
    shader = pptr_read(mat.m_Shader)
    tex = {}
    for name, env in sp.m_TexEnvs:
        t = pptr_read(env.m_Texture)
        if t is not None:
            tex[name] = {"name": t.m_Name, "type": t.object_reader.type.name,
                         "scale": [env.m_Scale.x, env.m_Scale.y],
                         "offset": [env.m_Offset.x, env.m_Offset.y]}
    return {
        "shader": shader.m_ParsedForm.m_Name if shader is not None else None,
        "keywords": list(getattr(mat, "m_ValidKeywords", None) or
                         (getattr(mat, "m_ShaderKeywords", "") or "").split()),
        "textures": tex,
        "floats": {k: float(v) for k, v in sp.m_Floats},
        "colors": {k: [c.r, c.g, c.b, c.a] for k, c in sp.m_Colors},
    }, [t for t in (pptr_read(env.m_Texture) for _, env in sp.m_TexEnvs) if t is not None]


def mesh_of(renderer, go):
    if renderer.object_reader.type.name == "SkinnedMeshRenderer":
        return pptr_read(renderer.m_Mesh)
    for comp in go.m_Component:
        c = pptr_read(getattr(comp, "component", comp))
        if c is not None and c.object_reader.type.name == "MeshFilter":
            return pptr_read(c.m_Mesh)
    return None


# ---------------------------------------------------------------------------
# glTF writer (static meshes, two UV sets)
# ---------------------------------------------------------------------------

class Glb:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accessors, self.meshes, self.nodes = [], [], [], []
        self.materials, self.mat_index = [], {}

    def _accessor(self, data, fmt, count, kind, ctype, target, minmax=False):
        while len(self.bin) % 4:
            self.bin.append(0)
        off = len(self.bin)
        self.bin += struct.pack(f"<{len(data)}{fmt}", *data)
        self.views.append({"buffer": 0, "byteOffset": off, "byteLength": len(self.bin) - off, "target": target})
        acc = {"bufferView": len(self.views) - 1, "componentType": ctype, "count": count, "type": kind}
        if minmax:
            n = {"VEC3": 3, "VEC2": 2}[kind]
            acc["min"] = [min(data[i::n]) for i in range(n)]
            acc["max"] = [max(data[i::n]) for i in range(n)]
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def material(self, name):
        if name not in self.mat_index:
            self.mat_index[name] = len(self.materials)
            self.materials.append({"name": name})
        return self.mat_index[name]

    def add(self, name, h, submesh_tris, materials):
        n = h.m_VertexCount
        # Unity is left-handed Y-up; glTF right-handed Y-up: mirror X.
        pos = [c for v in h.m_Vertices for c in (-v[0], v[1], v[2])]
        attrs = {"POSITION": self._accessor(pos, "f", n, "VEC3", 5126, 34962, minmax=True)}
        if h.m_Normals:
            nrm = []
            for v in h.m_Normals:
                l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
                nrm += (-v[0] / l, v[1] / l, v[2] / l)
            attrs["NORMAL"] = self._accessor(nrm, "f", n, "VEC3", 5126, 34962)
        # The game bakes smoothed outline directions into TANGENT (w = 0).
        tan = h.m_Tangents
        if tan and len(tan) == n and all(len(t) > 3 and t[3] == 0 for t in tan[:16]):
            attrs["_OUTLINE_DIR"] = self._accessor(
                [c for t in tan for c in (-t[0], t[1], t[2])], "f", n, "VEC3", 5126, 34962)
        for i, uv in enumerate((h.m_UV0, h.m_UV1)):
            if uv:
                attrs[f"TEXCOORD_{i}"] = self._accessor(
                    [c for t in uv for c in (t[0], 1.0 - t[1])], "f", n, "VEC2", 5126, 34962)
        prims = []
        for si, tris in enumerate(submesh_tris):
            if not tris:
                continue
            # Mirroring flipped handedness, so flip winding back.
            idx = [i for t in tris for i in (t[0], t[2], t[1])]
            p = {"attributes": attrs,
                 "indices": self._accessor(idx, "I", len(idx), "SCALAR", 5125, 34963)}
            if materials:
                p["material"] = self.material(materials[min(si, len(materials) - 1)])
            prims.append(p)
        if prims:
            self.meshes.append({"name": name, "primitives": prims})
            self.nodes.append({"name": name, "mesh": len(self.meshes) - 1})

    def write(self, path):
        while len(self.bin) % 4:
            self.bin.append(0)
        doc = {
            "asset": {"version": "2.0", "generator": "Tenkabana prism_extract"},
            "scene": 0, "scenes": [{"nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes, "meshes": self.meshes, "materials": self.materials,
            "accessors": self.accessors, "bufferViews": self.views,
            "buffers": [{"byteLength": len(self.bin)}],
        }
        js = json.dumps(doc, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942) + self.bin)


# ---------------------------------------------------------------------------
# Cubemap -> equirect (Blender's Environment Texture convention, Z-up)
# ---------------------------------------------------------------------------

def _decode_faces(cube):
    import texture2ddecoder
    from PIL import Image
    w = cube.m_Width
    sd = cube.m_StreamData
    if sd is not None and sd.size:
        res = cube.object_reader.assets_file.parent.files[sd.path.rsplit("/", 1)[-1]]
        res.Position = 0
        raw = bytes(res.read(res.Length))[sd.offset:sd.offset + sd.size]
    else:
        raw = bytes(cube.image_data)
    face = cube.m_CompleteImageSize
    fmt = int(cube.m_TextureFormat)
    decoders = {10: (texture2ddecoder.decode_bc1, w * w // 2),
                12: (texture2ddecoder.decode_bc3, w * w)}
    if fmt not in decoders:
        return None
    dec, size = decoders[fmt]
    return [Image.frombytes("RGBA", (w, w), dec(raw[i * face:i * face + size], w, w), "raw", "BGRA")
            for i in range(6)]


def _cube_sample(faces, x, y, z):
    ax, ay, az = abs(x), abs(y), abs(z)
    if ax >= ay and ax >= az:
        f, u, v, m = (0, -z, -y, ax) if x > 0 else (1, z, -y, ax)
    elif ay >= az:
        f, u, v, m = (2, x, z, ay) if y > 0 else (3, x, -z, ay)
    else:
        f, u, v, m = (4, x, -y, az) if z > 0 else (5, -x, -y, az)
    im = faces[f]
    w = im.size[0]
    return im.getpixel((min(w - 1, int((u / m * 0.5 + 0.5) * w)), min(w - 1, int((v / m * 0.5 + 0.5) * w))))


def write_equirect(cube, path, width=512):
    from PIL import Image
    faces = _decode_faces(cube)
    if faces is None:
        return False
    h = width // 2
    out = Image.new("RGB", (width, h))
    px = out.load()
    for r in range(h):
        theta = (1.0 - (r + 0.5) / h - 0.5) * math.pi
        for c in range(width):
            phi = ((c + 0.5) / width - 0.5) * 2 * math.pi
            bx, by, bz = -math.cos(phi) * math.cos(theta), math.sin(phi) * math.cos(theta), math.sin(theta)
            # Blender (x, y, z) <- Unity (-x, -z, y)
            px[c, r] = _cube_sample(faces, -bx, bz, -by)[:3]
    out.save(path)
    return True


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export")
    ap.add_argument("--out")
    args = ap.parse_args()

    src = os.path.abspath(args.export)
    out = os.path.abspath(args.out or os.path.join(src, "blender"))
    tex_dir = os.path.join(out, "textures")
    os.makedirs(tex_dir, exist_ok=True)

    bundles = [os.path.join(src, f) for f in sorted(os.listdir(src)) if f.endswith(".unity3d")]
    if not bundles:
        sys.exit(f"no .unity3d bundles in {src}")
    env = UnityPy.load(*bundles)

    materials, textures, renderers = {}, {}, []
    for obj in env.objects:
        if obj.type.name not in ("SkinnedMeshRenderer", "MeshRenderer"):
            continue
        r = obj.read()
        go = pptr_read(r.m_GameObject)
        if go is None:
            continue
        mats = []
        for p in r.m_Materials:
            m = pptr_read(p)
            if m is None:
                mats.append(None)
                continue
            if m.m_Name not in materials:
                materials[m.m_Name], used = material_info(m)
                for t in used:
                    textures[t.m_Name] = t
            mats.append(m.m_Name)
        renderers.append((go.m_Name, mesh_of(r, go), mats))

    # Resolve placeholders the way the game does at runtime.
    holders = {}
    for name, mesh, mats in renderers:
        role = holder_role(name)
        if role and mats and mats[0]:
            holders.setdefault(role, mats[0])
    hair_mat = next((m for _, _, ms in renderers for m in ms
                     if m and (materials[m]["shader"] or "").endswith("/Hair")), None)
    holders.setdefault("hair", hair_mat)
    costume = {n.lower() for n, _, ms in renderers
               if any(m and materials[m]["shader"] and materials[m]["shader"].startswith("Anim2D3D") for m in ms)}

    def is_placeholder(m):
        return m is None or not (materials[m]["shader"] or "").startswith("Anim2D3D")

    glb = Glb()
    objects, skipped, seen = [], [], {}
    for name, mesh, mats in renderers:
        if mesh is None or holder_role(name):
            continue
        h = MeshHandler(mesh)
        h.process()
        if h.m_VertexCount < 8:
            continue
        if all(is_placeholder(m) for m in mats):
            if name.lower() in costume:
                skipped.append({"name": name, "why": "replaced by the costume's own part"})
                continue
            role = next((role for role, rx in RUNTIME_ROLES if rx.match(name)), None)
            if role and holders.get(role):
                mats = [holders[role]] * max(1, len(mats))
            else:
                skipped.append({"name": name, "why": "base-body part with no costume material"})
                continue
        tris = h.get_triangles()
        # More materials than submeshes: extras re-draw the last submesh (FakeShadow).
        extra = mats[len(tris):] if len(mats) > len(tris) else []
        mats = mats[:len(tris)] or mats
        unique = name if name not in seen else f"{name}.{seen[name]:03d}"
        seen[name] = seen.get(name, 0) + 1
        glb.add(unique, h, tris, mats)
        objects.append({"name": unique, "mesh": mesh.m_Name, "vertices": h.m_VertexCount,
                        "materials": mats, "extra_passes": extra, "has_uv2": bool(h.m_UV1)})

    glb.write(os.path.join(out, "model.glb"))

    written = {}
    for name, t in textures.items():
        kind = t.object_reader.type.name
        try:
            if kind == "Texture2D":
                path = os.path.join(tex_dir, f"{name}.png")
                t.image.save(path)
                written[name] = os.path.relpath(path, out)
            elif kind == "Cubemap":
                path = os.path.join(tex_dir, f"{name}_equirect.png")
                if write_equirect(t, path):
                    written[name] = os.path.relpath(path, out)
        except Exception as e:
            print(f"  texture {name}: {e}")

    m = re.search(r"(\d{2})[a-z]{4}|(\d{2})_[a-z]+$", os.path.basename(src).lower())
    with open(os.path.join(out, "materials.json"), "w", encoding="utf-8") as f:
        json.dump({"source": os.path.basename(src),
                   "idol": int(next(g for g in m.groups() if g)) if m else None,
                   "objects": objects, "skipped": skipped,
                   "materials": materials, "textures": written}, f, indent=1)

    print(f"{len(objects)} objects, {len(materials)} materials, {len(written)} textures -> {out}")
    for s in skipped:
        print(f"  skipped {s['name']}: {s['why']}")


if __name__ == "__main__":
    main()
