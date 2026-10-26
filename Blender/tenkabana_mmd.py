"""Tenkabana MMD - turn a Song for Prism skeleton into an MMD-ready rig.

The game's characters share one Unity humanoid skeleton (Hips, Spine..Spine2,
LeftArm, LeftHandIndex1..4, ...) plus game helpers (*_rot twist bones,
LeftElbow, LeftRing palm bone) and spring chains (HAIR_*, Skirt_*, Bow_*).
VMD motions and PMX export expect something else, and the gaps are exactly what
makes a dance look broken on an unconverted rig:

    Rest pose   VMD rotations are deltas from an A-pose (arms ~35 deg down).
                Played on the game's T-pose they rotate every arm about the
                wrong axis: elbows bend tens of degrees off and forearms twist.
    Bone axes   Game bones carry stub tails (sideways or forward), so IK and
                twist have no limb axis to work with.
    Twist       *_rot helpers are plain children; the game turns them in code.
                Left alone, wrist and shoulder roll collapses into one ring of
                vertices (the candy-wrapper).
    Names/IK    VMD tracks are keyed by Japanese names, and the legs are driven
                by 左足ＩＫ / 左つま先ＩＫ targets under 全ての親 / センター.

Convert to MMD does, in order and in place on the selected armature:

    1. points every limb bone at its child (helpers follow their parent), with
       a consistent roll: knees bend about local X, twist is local Y;
    2. re-rests the arms at the A-pose angle, baking the change into every
       skinned mesh, its shape keys, custom normals and outline directions;
    3. adds 全ての親 / センター / グルーブ and leg + toe IK with knee limits;
    4. drives the twist helpers (ForeArm_rot takes part of the wrist twist,
       Arm1/2_rot unwind the shoulder twist, Shoulder_rot follows the arm);
    5. keeps at most 4 normalised weights per vertex (PMX BDEF4) and turns off
       deform on bones that weight nothing;
    6. renames bones to the MMD standard names (vertex groups follow), and
       fills mmd_tools' name_j / name_e when that add-on is installed.

Existing actions were keyed for the old rest pose and bone axes; re-import or
re-retarget motion after converting. Load VMDs with mmd_tools (Import Motion).

Install: Edit > Preferences > Add-ons > Install from Disk, pick this file.
Panel:   3D Viewport > Sidebar (N) > Tenkabana > Tenkabana MMD.
"""

bl_info = {
    "name": "Tenkabana MMD",
    "author": "Tenkabana",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Tenkabana",
    "description": "Convert Song for Prism skeletons to MMD-ready rigs (A-pose, IK, twist, names)",
    "category": "Rigging",
}

import math

import bpy
from mathutils import Matrix, Quaternion, Vector

# ---------------------------------------------------------------------------
# Skeleton tables (game names)
# ---------------------------------------------------------------------------

SIDES = (("Left", "左"), ("Right", "右"))
FINGERS = (("Thumb", "親指", "０１２先"), ("Index", "人指", "１２３先"),
           ("Middle", "中指", "１２３先"), ("Ring", "薬指", "１２３先"),
           ("Pinky", "小指", "１２３先"))

MMD_NAMES = {"Hips": "下半身", "Spine": "上半身", "Spine1": "上半身2", "Spine2": "上半身3",
             "Neck": "首", "Head": "頭", "EYE_L": "左目", "EYE_R": "右目"}
# Each limb bone points at this child.
NEXT = {"Spine": "Spine1", "Spine1": "Spine2", "Spine2": "Neck", "Neck": "Head"}
for _s, _j in SIDES:
    MMD_NAMES.update({
        _s + "Shoulder": _j + "肩", _s + "Arm": _j + "腕", _s + "ForeArm": _j + "ひじ",
        _s + "Hand": _j + "手首", _s + "UpLeg": _j + "足", _s + "Leg": _j + "ひざ",
        _s + "Foot": _j + "足首", _s + "Toe": _j + "つま先"})
    NEXT.update({
        _s + "Shoulder": _s + "Arm", _s + "Arm": _s + "ForeArm", _s + "ForeArm": _s + "Hand",
        _s + "Hand": _s + "HandMiddle1", _s + "Ring": _s + "HandRing1",
        _s + "UpLeg": _s + "Leg", _s + "Leg": _s + "Foot", _s + "Foot": _s + "Toe",
        _s + "Toe": _s + "Toe_null"})
    for _f, _jf, _nums in FINGERS:
        for _i, _n in enumerate(_nums, 1):
            MMD_NAMES[f"{_s}Hand{_f}{_i}"] = _j + _jf + _n
            if _i < 4:
                NEXT[f"{_s}Hand{_f}{_i}"] = f"{_s}Hand{_f}{_i + 1}"

REQUIRED = ("Hips", "Spine", "LeftArm", "RightArm", "LeftUpLeg", "RightUpLeg", "LeftFoot", "RightFoot")
ROOT, CENTER, GROOVE = "全ての親", "センター", "グルーブ"
# Vector attributes that must turn with the mesh. _OUTLINE_DIR is raw glTF (Y-up).
VECTOR_ATTRS = ("tkb_outline_dir",)
GLTF_ATTRS = ("_OUTLINE_DIR",)


def find_rig(context):
    obj = context.active_object
    if obj is not None and obj.type == "MESH":
        mod = next((m for m in obj.modifiers if m.type == "ARMATURE" and m.object), None)
        obj = mod.object if mod else None
    return obj if obj is not None and obj.type == "ARMATURE" else None


def skinned_meshes(rig):
    return [o for o in bpy.data.objects if o.type == "MESH"
            and any(m.type == "ARMATURE" and m.object == rig for m in o.modifiers)]


def _set_mode(context, rig, mode):
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    context.view_layer.objects.active = rig
    if mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)


# ---------------------------------------------------------------------------
# 1. Bone axes
# ---------------------------------------------------------------------------

def _point(eb, vec, length=None):
    """Aim an edit bone along vec. Vertical bones roll their Z forward (-Y), so
    a knee bends about local X; horizontal ones roll Z up."""
    d = vec.normalized()
    eb.tail = eb.head + d * (length or vec.length)
    eb.align_roll(Vector((0.0, -1.0, 0.0)) if abs(d.z) > 0.7 else Vector((0.0, 0.0, 1.0)))


def fix_bone_axes(rig):
    ebs = rig.data.edit_bones
    for eb in ebs:
        eb.use_connect = False
    done = set()
    for name, child in NEXT.items():
        eb, c = ebs.get(name), ebs.get(child)
        if eb and c and (c.head - eb.head).length > 1e-4:
            _point(eb, c.head - eb.head)
            done.add(name)
    if "Hips" in ebs:
        _point(ebs["Hips"], Vector((0.0, 0.0, -1.0)), 0.1 * max(ebs["Hips"].head.z, 0.5))
        done.add("Hips")
    if "Head" in ebs:
        _point(ebs["Head"], Vector((0.0, 0.0, 1.0)), 0.1 * max(ebs["Head"].head.z, 1.0))
        done.add("Head")
    # Tips and helpers (finger 4s, Toe_null, *_rot, Elbow) follow their parent's
    # axis, so a helper's local Y is the limb's twist axis. Parents first.
    for eb in sorted(ebs, key=lambda b: len(b.parent_recursive)):
        if eb.name in done or eb.parent is None:
            continue
        game = eb.name.endswith("_rot") or eb.name.endswith("Elbow") or \
            eb.name.endswith("Toe_null") or (eb.name[-1] == "4" and "Hand" in eb.name)
        if game:
            _point(eb, eb.parent.vector, max(eb.length, 0.01))
            done.add(eb.name)
    return len(done)


# ---------------------------------------------------------------------------
# 2. A-pose: pose the arms down, bake into the meshes, make it the rest
# ---------------------------------------------------------------------------

def _gltf_to_blender(v):
    return Vector((v[0], -v[2], v[1]))


def _blender_to_gltf(v):
    return Vector((v[0], v[2], -v[1]))


def bake_pose_into_mesh(obj, rig, deltas):
    """Linear-blend-skin the mesh by the current pose and write the result as its
    new rest: base positions, every shape key, custom normals, outline vectors."""
    me = obj.data
    to_arm = rig.matrix_world.inverted() @ obj.matrix_world
    back = to_arm.inverted()
    names = {g.index: g.name for g in obj.vertex_groups}
    moved = {n for n, d in deltas.items() if d is not None}
    per_vertex = [None] * len(me.vertices)
    for v in me.vertices:
        groups = [(names.get(g.group), g.weight) for g in v.groups if g.weight > 0.0]
        groups = [(n, w) for n, w in groups if n in deltas]
        if not any(n in moved for n, _ in groups):
            continue
        acc = Matrix.Diagonal((0.0, 0.0, 0.0, 0.0))
        total = 0.0
        for n, w in groups:
            acc += (deltas[n] or Matrix.Identity(4)) * w
            total += w
        per_vertex[v.index] = back @ (acc * (1.0 / total)) @ to_arm
    if not any(m is not None for m in per_vertex):
        return 0

    def move(coords):
        for i, m in enumerate(per_vertex):
            if m is not None:
                coords[i] = m @ coords[i]

    custom = None
    if getattr(me, "has_custom_normals", False):
        custom = [Vector(n.vector) for n in me.corner_normals]
    if me.shape_keys:
        for kb in me.shape_keys.key_blocks:
            co = [Vector(p.co) for p in kb.data]
            move(co)
            for p, c in zip(kb.data, co):
                p.co = c
    co = [Vector(v.co) for v in me.vertices]
    move(co)
    for v, c in zip(me.vertices, co):
        v.co = c
    for name in VECTOR_ATTRS + GLTF_ATTRS:
        a = me.attributes.get(name)
        if a is None or a.domain != "POINT" or a.data_type != "FLOAT_VECTOR":
            continue
        for i, m in enumerate(per_vertex):
            if m is None:
                continue
            r = m.to_3x3()
            if name in GLTF_ATTRS:
                a.data[i].vector = _blender_to_gltf(r @ _gltf_to_blender(a.data[i].vector))
            else:
                a.data[i].vector = r @ Vector(a.data[i].vector)
    if custom is not None:
        for loop in me.loops:
            m = per_vertex[loop.vertex_index]
            if m is not None:
                custom[loop.index] = (m.to_3x3() @ custom[loop.index]).normalized()
        me.normals_split_custom_set(custom)
    me.update()
    return sum(m is not None for m in per_vertex)


def apply_a_pose(context, rig, angle_deg):
    """Rotate each upper arm so it points angle_deg below horizontal."""
    _set_mode(context, rig, "POSE")
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    context.view_layer.update()
    turned = {}
    for side, _ in SIDES:
        arm, fore = rig.pose.bones.get(side + "Arm"), rig.pose.bones.get(side + "ForeArm")
        if arm is None or fore is None:
            continue
        d = (fore.bone.head_local - arm.bone.head_local).normalized()
        flat = Vector((d.x, d.y, 0.0))
        if flat.length < 1e-4:
            continue
        current = math.degrees(math.atan2(-d.z, flat.length))   # below horizontal
        change = angle_deg - current
        if abs(change) < 0.5:
            continue
        axis = d.cross(Vector((0.0, 0.0, -1.0))).normalized()
        q = Quaternion(axis, math.radians(change))
        m = q.to_matrix().to_4x4() @ arm.bone.matrix_local.to_3x3().to_4x4()
        m.translation = arm.bone.head_local
        arm.matrix = m
        turned[side] = round(change, 1)
    if not turned:
        _set_mode(context, rig, "OBJECT")
        return turned, 0
    context.view_layer.update()
    # Deform bones only; None marks a bone the pose did not move.
    deltas = {}
    for pb in rig.pose.bones:
        if not pb.bone.use_deform:
            continue
        d = pb.matrix @ pb.bone.matrix_local.inverted()
        moved = any(abs(d[i][j] - (1.0 if i == j else 0.0)) > 1e-6 for i in range(4) for j in range(4))
        deltas[pb.name] = d if moved else None
    pose = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    _set_mode(context, rig, "OBJECT")
    verts = sum(bake_pose_into_mesh(o, rig, deltas) for o in skinned_meshes(rig))
    _set_mode(context, rig, "EDIT")
    for eb in rig.data.edit_bones:
        length = eb.length
        eb.matrix = pose[eb.name]
        eb.length = length
    _set_mode(context, rig, "POSE")
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    _set_mode(context, rig, "OBJECT")
    return turned, verts


# ---------------------------------------------------------------------------
# 3. Control and IK bones
# ---------------------------------------------------------------------------

def add_controls(rig):
    ebs = rig.data.edit_bones
    hips = ebs["Hips"]
    height = max((ebs["Head"].head.z if "Head" in ebs else hips.head.z * 1.8), 0.5)
    unit = height * 0.05

    def new(name, head, tail, parent):
        eb = ebs.get(name) or ebs.new(name)
        eb.head, eb.tail = head, tail
        eb.parent = parent
        eb.use_deform = False
        return eb

    root = new(ROOT, Vector((0, 0, 0)), Vector((0, 0, unit * 2)), None)
    # MMD puts センター below the waist (~0.65 of hip height), groove on top of it.
    c_head = Vector((hips.head.x, hips.head.y, hips.head.z * 0.65))
    center = new(CENTER, c_head, c_head + Vector((0, 0, unit)), root)
    groove = new(GROOVE, c_head + Vector((0, 0, unit * 0.2)), c_head + Vector((0, 0, unit * 1.2)), center)
    for name in ("Hips", "Spine"):
        if name in ebs:
            ebs[name].parent = groove
    for b in ebs:
        if b.parent is None and b.name not in (ROOT,):
            b.parent = root
    for side, jp in SIDES:
        foot, toe = ebs.get(side + "Foot"), ebs.get(side + "Toe")
        if not foot or not toe:
            continue
        ik = new(jp + "足ＩＫ", foot.head.copy(), foot.head + Vector((0, unit, 0)), root)
        new(jp + "つま先ＩＫ", toe.head.copy(), toe.head + Vector((0, 0, -unit)), ik)


def add_leg_ik(rig):
    pbs = rig.pose.bones
    for side, jp in SIDES:
        knee, ankle = pbs.get(side + "Leg"), pbs.get(side + "Foot")
        if knee is None or ankle is None:
            continue
        for pb, target, chain in ((knee, jp + "足ＩＫ", 2), (ankle, jp + "つま先ＩＫ", 1)):
            c = pb.constraints.get("TKB IK") or pb.constraints.new("IK")
            c.name = "TKB IK"
            c.target, c.subtarget = rig, target
            c.chain_count = chain
            c.use_tail = True
            c.iterations = 40
        # Local X is the knee hinge (see _point); the lower leg swings backwards
        # for negative angles. MMD keeps a hair of bend so the solve never locks.
        knee.lock_ik_y = knee.lock_ik_z = True
        knee.use_ik_limit_x = True
        knee.ik_min_x, knee.ik_max_x = math.radians(-180.0), math.radians(-0.5)


# ---------------------------------------------------------------------------
# 4. Twist helpers
# ---------------------------------------------------------------------------

def _twist(pb, rig, source, share):
    """pb turns about its own Y by share x the source's swing-twist Y twist."""
    c = pb.constraints.get("TKB Twist") or pb.constraints.new("TRANSFORM")
    c.name = "TKB Twist"
    c.target, c.subtarget = rig, source
    c.owner_space, c.target_space = "LOCAL", "LOCAL_OWNER_ORIENT"
    c.map_from, c.map_to = "ROTATION", "ROTATION"
    c.from_rotation_mode = "SWING_TWIST_Y"
    c.from_min_y_rot, c.from_max_y_rot = -math.pi, math.pi
    c.map_to_x_from = c.map_to_y_from = c.map_to_z_from = "Y"
    c.to_min_x_rot = c.to_max_x_rot = c.to_min_z_rot = c.to_max_z_rot = 0.0
    c.to_min_y_rot, c.to_max_y_rot = -math.pi * share, math.pi * share
    c.mix_mode_rot = "REPLACE"


def add_twist(rig, wrist=0.5, shoulder=0.5):
    pbs = rig.pose.bones
    n = 0
    for side, _ in SIDES:
        helpers = ((side + "ForeArm_rot", side + "Hand", wrist),
                   (side + "Arm1_rot", side + "Arm", -0.5),
                   (side + "Arm2_rot", side + "Arm", -0.25))
        for helper, source, share in helpers:
            if helper in pbs and source in pbs:
                _twist(pbs[helper], rig, source, share)
                n += 1
        sh, arm = pbs.get(side + "Shoulder_rot"), pbs.get(side + "Arm")
        if sh is not None and arm is not None:
            c = sh.constraints.get("TKB Follow") or sh.constraints.new("COPY_ROTATION")
            c.name = "TKB Follow"
            c.target, c.subtarget = rig, arm.name
            c.owner_space, c.target_space = "LOCAL", "LOCAL_OWNER_ORIENT"
            c.mix_mode = "REPLACE"
            c.influence = shoulder
            n += 1
    return n


# ---------------------------------------------------------------------------
# 5. Weights
# ---------------------------------------------------------------------------

def clean_weights(rig, limit=4):
    deform = {b.name for b in rig.data.bones if b.use_deform}
    used, trimmed = set(), 0
    for obj in skinned_meshes(rig):
        groups = obj.vertex_groups
        names = {g.index: g.name for g in groups}
        for v in obj.data.vertices:
            ws = sorted(((g.weight, g.group) for g in v.groups
                         if g.weight > 0.0 and names.get(g.group) in deform), reverse=True)
            if len(ws) > limit:
                for _, gi in ws[limit:]:
                    groups[gi].remove([v.index])
                ws = ws[:limit]
                trimmed += 1
                total = sum(w for w, _ in ws)
                for w, gi in ws:
                    groups[gi].add([v.index], w / total, "REPLACE")
            used.update(names[gi] for _, gi in ws)
    silenced = []
    for b in rig.data.bones:
        if b.use_deform and b.name not in used:
            b.use_deform = False
            silenced.append(b.name)
    return trimmed, silenced


# ---------------------------------------------------------------------------
# 6. Names
# ---------------------------------------------------------------------------

def rename_to_mmd(rig):
    has_mmd = hasattr(bpy.types.PoseBone, "mmd_bone")
    n = 0
    for game, jp in MMD_NAMES.items():
        b = rig.data.bones.get(game)
        if b is None or jp in rig.data.bones:
            continue
        b["tkb_game_name"] = game
        b.name = jp          # vertex groups, constraints and F-curves follow
        n += 1
    if has_mmd:
        for pb in rig.pose.bones:
            pb.mmd_bone.name_j = pb.name
            pb.mmd_bone.name_e = pb.bone.get("tkb_game_name", pb.name)
    return n


def convert(context, rig, s):
    missing = [n for n in REQUIRED if n not in rig.data.bones]
    if missing:
        raise ValueError("Not a Song for Prism skeleton (missing " + ", ".join(missing) + ")")
    if CENTER in rig.data.bones:
        raise ValueError("This rig is already converted")
    report = {}
    _set_mode(context, rig, "EDIT")
    report["axes"] = fix_bone_axes(rig)
    _set_mode(context, rig, "OBJECT")
    if s.a_pose:
        report["a_pose"], report["moved_verts"] = apply_a_pose(context, rig, s.arm_angle)
    _set_mode(context, rig, "EDIT")
    add_controls(rig)
    _set_mode(context, rig, "POSE")
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
    if s.leg_ik:
        add_leg_ik(rig)
    if s.twist:
        report["twist"] = add_twist(rig, s.wrist_share, s.shoulder_share)
    _set_mode(context, rig, "OBJECT")
    if s.clean_weights:
        report["trimmed"], report["silenced"] = clean_weights(rig)
    if s.rename:
        report["renamed"] = rename_to_mmd(rig)
    rig["tkb_mmd"] = True
    return report


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class TKB_MMDSettings(bpy.types.PropertyGroup):
    a_pose: bpy.props.BoolProperty(
        name="A-pose rest", default=True,
        description="Re-rest the arms at the MMD A-pose so VMD arm keys rotate about the right axes")
    arm_angle: bpy.props.FloatProperty(
        name="Arm angle", default=35.0, min=0.0, max=60.0,
        description="Upper arm angle below horizontal, in degrees (MMD standard ~35)")
    leg_ik: bpy.props.BoolProperty(name="Leg IK", default=True)
    twist: bpy.props.BoolProperty(
        name="Twist helpers", default=True,
        description="Drive the game's *_rot bones so wrist and shoulder twist spreads along the limb")
    wrist_share: bpy.props.FloatProperty(name="Wrist twist", default=0.5, min=0.0, max=1.0)
    shoulder_share: bpy.props.FloatProperty(name="Shoulder follow", default=0.5, min=0.0, max=1.0)
    clean_weights: bpy.props.BoolProperty(
        name="PMX weights", default=True,
        description="At most 4 normalised weights per vertex; no deform on bones that weight nothing")
    rename: bpy.props.BoolProperty(name="MMD bone names", default=True)


class TKB_OT_mmd_convert(bpy.types.Operator):
    bl_idname = "tenkabana.mmd_convert"
    bl_label = "Convert to MMD"
    bl_description = "Make the selected Song for Prism armature MMD-ready (in place)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, message="Converts the rig in place. Existing actions will no longer match.")

    def execute(self, context):
        rig = find_rig(context)
        try:
            r = convert(context, rig, context.scene.tkb_mmd)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        parts = [f"{r['axes']} bone axes"]
        if "a_pose" in r:
            parts.append(f"arms {r['a_pose']} deg, {r['moved_verts']} verts re-rested")
        if "twist" in r:
            parts.append(f"{r['twist']} twist helpers")
        if "trimmed" in r:
            parts.append(f"{r['trimmed']} verts trimmed to 4, {len(r['silenced'])} bones non-deform")
        if "renamed" in r:
            parts.append(f"{r['renamed']} renamed")
        self.report({"INFO"}, "MMD: " + "; ".join(parts))
        return {"FINISHED"}


class TKB_PT_mmd(bpy.types.Panel):
    bl_label = "Tenkabana MMD"
    bl_idname = "TKB_PT_mmd"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tenkabana"

    def draw(self, context):
        s = context.scene.tkb_mmd
        rig = find_rig(context)
        col = self.layout.column(align=True)
        if rig is None:
            col.label(text="Select the character's armature", icon="INFO")
        elif rig.get("tkb_mmd"):
            col.label(text=f"{rig.name}: MMD-ready", icon="CHECKMARK")
        else:
            col.label(text=rig.name, icon="ARMATURE_DATA")
        row = col.row(align=True)
        row.prop(s, "a_pose")
        sub = row.row(align=True)
        sub.active = s.a_pose
        sub.prop(s, "arm_angle", text="")
        col.prop(s, "leg_ik")
        col.prop(s, "twist")
        if s.twist:
            row = col.row(align=True)
            row.prop(s, "wrist_share")
            row.prop(s, "shoulder_share")
        col.prop(s, "clean_weights")
        col.prop(s, "rename")
        col.separator()
        col.operator(TKB_OT_mmd_convert.bl_idname, icon="ARMATURE_DATA")
        if not hasattr(bpy.types.PoseBone, "mmd_bone"):
            col.label(text="Install mmd_tools to import VMD", icon="INFO")


_classes = (TKB_MMDSettings, TKB_OT_mmd_convert, TKB_PT_mmd)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.tkb_mmd = bpy.props.PointerProperty(type=TKB_MMDSettings)


def unregister():
    del bpy.types.Scene.tkb_mmd
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
