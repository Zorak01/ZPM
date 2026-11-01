# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender scenes to and from the format's Scene. No UI or filesystem access."""

from array import array

import bpy
from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix

from ..format import (
    Armature,
    Bone,
    Camera,
    Collection,
    Curve,
    FormatError,
    Lattice,
    Light,
    PoseChannel,
    Reference,
    Scene,
    SceneObject,
    SceneSpace,
    SpaceAxis,
    Spline,
)
from ..format.container.layout import (
    NO_INDEX,
    AreaShape,
    HandleType,
    InheritScale,
    LightType,
    LightUnit,
    ObjectFlags,
    ObjectType,
    ParentType,
    ResourceKind,
    RotationMode,
    SkinFlags,
    SplineFlags,
    SplineType,
)
from . import animation_adapter, material_adapter, mesh_adapter, rna_values

_AXIS_NAMES = {
    SpaceAxis.X: "X",
    SpaceAxis.Y: "Y",
    SpaceAxis.Z: "Z",
    SpaceAxis.NEG_X: "-X",
    SpaceAxis.NEG_Y: "-Y",
    SpaceAxis.NEG_Z: "-Z",
}
_OBJECT_TYPES = {
    "MESH": ObjectType.MESH,
    "EMPTY": ObjectType.EMPTY,
    "ARMATURE": ObjectType.ARMATURE,
    "LIGHT": ObjectType.LIGHT,
    "CURVE": ObjectType.CURVE,
    "LATTICE": ObjectType.LATTICE,
    "CAMERA": ObjectType.CAMERA,
}
_SPLINE_TYPES = {kind.name: kind for kind in SplineType}
_HANDLE_TYPES = {kind.name: kind for kind in HandleType}
_POSE_FIELDS = {
    "location",
    "scale",
    "rotation_mode",
    "rotation_quaternion",
    "rotation_euler",
    "rotation_axis_angle",
}
_LIGHT_FIELDS = {
    "color",
    "energy",
    "use_shadow",
    "shadow_soft_size",
    "spot_size",
    "spot_blend",
    "angle",
    "shape",
    "size",
    "size_y",
}
_BONE_FIELDS = {
    "head",
    "tail",
    "head_local",
    "tail_local",
    "use_connect",
    "use_deform",
    "use_inherit_rotation",
    "inherit_scale",
    "envelope_distance",
    "head_radius",
    "tail_radius",
}
_SHAPE_TRANSFORM = "custom_shape_transform"
_INHERIT_SCALES = {mode.name: mode for mode in InheritScale}
_PARENT_TYPES = {kind.name: kind for kind in ParentType}
_OBJECT_FIELDS = {
    "data",
    "location",
    "scale",
    "rotation_mode",
    "rotation_quaternion",
    "rotation_euler",
    "rotation_axis_angle",
    "parent",
    "parent_type",
    "parent_bone",
    "parent_vertices",
    "matrix_parent_inverse",
    "matrix_world",
    "matrix_local",
    "matrix_basis",
    "hide_viewport",
    "hide_render",
    "hide_select",
    "instance_collection",
    "instance_type",
    "active_material_index",
    "active_shape_key_index",
}
_OBJECT_SLOT_LINK = "OBJECT"
_CURVE_POINT_FIELDS = 4
_LATTICE_POINT_AXES = 3
_ROTATION_MODES = {mode.name: mode for mode in RotationMode}
_LIGHT_TYPES = {kind.name: kind for kind in LightType}
_AREA_SHAPES = {shape.name: shape for shape in AreaShape}
_ARMATURE_MODIFIER = "ARMATURE"
_BONE_PARENT = "BONE"
_SKIN_MODIFIER_NAME = "Armature"
_IDENTITY_QUATERNION = (1.0, 0.0, 0.0, 0.0)


def _meters_per_unit(scene: bpy.types.Scene) -> float:
    settings = scene.unit_settings
    return 1.0 if settings.system == "NONE" else settings.scale_length


def _rotation_of(item) -> tuple:
    """Return the four rotation values of an object or pose bone, by its mode."""
    if item.rotation_mode == "QUATERNION":
        return tuple(item.rotation_quaternion)
    if item.rotation_mode == "AXIS_ANGLE":
        return tuple(item.rotation_axis_angle)
    return (*item.rotation_euler, 0.0)


def _set_rotation(item, mode: RotationMode, rotation: tuple) -> None:
    item.rotation_mode = mode.name
    if mode == RotationMode.QUATERNION:
        item.rotation_quaternion = rotation
    elif mode == RotationMode.AXIS_ANGLE:
        item.rotation_axis_angle = rotation
    else:
        item.rotation_euler = rotation[:3]


def _capture_light(light: bpy.types.Light) -> Light:
    is_area = light.type == "AREA"
    return Light(
        light.name,
        _LIGHT_TYPES[light.type],
        tuple(light.color),
        light.energy,
        LightUnit.WATTS_PER_SQUARE_METER if light.type == "SUN" else LightUnit.WATTS,
        light.use_shadow,
        getattr(light, "shadow_soft_size", 0.0),
        getattr(light, "spot_size", 0.0),
        getattr(light, "spot_blend", 0.0),
        getattr(light, "angle", 0.0),
        _AREA_SHAPES[light.shape] if is_area else AreaShape.SQUARE,
        light.size if is_area else 0.0,
        light.size_y if is_area else 0.0,
        rna_values.capture_rna(light, {}, skip=_LIGHT_FIELDS),
        rna_values.capture_properties(light),
    )


def _capture_armature(armature: bpy.types.Armature) -> Armature:
    bones = list(armature.bones)
    index = {bone.name: at for at, bone in enumerate(bones)}
    return Armature(
        armature.name,
        [
            Bone(
                bone.name,
                index[bone.parent.name] if bone.parent else NO_INDEX,
                tuple(bone.head_local),
                tuple(bone.tail_local),
                bpy.types.Bone.AxisRollFromMatrix(bone.matrix_local.to_3x3())[1],
                bone.envelope_distance,
                bone.head_radius,
                bone.tail_radius,
                bone.use_connect,
                bone.use_deform,
                bone.use_inherit_rotation,
                _INHERIT_SCALES[bone.inherit_scale],
                rna_values.capture_rna(bone, {}, skip=_BONE_FIELDS),
                rna_values.capture_properties(bone),
            )
            for bone in bones
        ],
        rna_values.capture_rna(armature, {}),
        _capture_bone_collections(armature),
        rna_values.capture_properties(armature),
    )


def _capture_bone_collections(armature: bpy.types.Armature) -> list:
    return [
        {
            "name": collection.name,
            "parent": collection.parent.name if collection.parent else "",
            "is_visible": collection.is_visible,
            "bones": [bone.name for bone in collection.bones],
        }
        for collection in armature.collections_all
    ]


def _apply_bone_collections(armature: bpy.types.Armature, stored: list) -> None:
    made = {}
    for item in stored:
        parent = made.get(item["parent"])
        collection = armature.collections.new(item["name"], parent=parent)
        collection.is_visible = item["is_visible"]
        made[item["name"]] = collection
        for name in item["bones"]:
            collection.assign(armature.bones[name])


def _capture_stack(items, object_index: dict) -> list:
    """Return modifiers or constraints in stack order, with their settings and links."""
    return [
        {
            "name": item.name,
            "type": item.type,
            "settings": rna_values.capture_rna(item, object_index),
        }
        for item in items
    ]


def _apply_modifiers(obj, stack: list, objects: list) -> None:
    for stored in stack:
        modifier = obj.modifiers.new(stored["name"], stored["type"])
        rna_values.apply_rna(modifier, stored["settings"], objects)


def _apply_constraints(owner, stack: list, objects: list) -> None:
    for stored in stack:
        constraint = owner.constraints.new(stored["type"])
        constraint.name = stored["name"]
        rna_values.apply_rna(constraint, stored["settings"], objects)


def _capture_bone_settings(obj, bones: dict, object_index: dict) -> list:
    """Return control shapes, locks and constraints of bones that have any."""
    settings = []
    for pose_bone in obj.pose.bones:
        values = rna_values.capture_rna(pose_bone, object_index, skip=_POSE_FIELDS)
        if pose_bone.custom_shape_transform is not None:
            values[_SHAPE_TRANSFORM] = pose_bone.custom_shape_transform.name
        stack = _capture_stack(pose_bone.constraints, object_index)
        if values or stack:
            settings.append(
                {
                    "bone": bones[pose_bone.name],
                    "settings": values,
                    "constraints": stack,
                }
            )
    return settings


def _capture_spline(spline) -> Spline:
    is_bezier = spline.type == "BEZIER"
    points = spline.bezier_points if is_bezier else spline.points
    count = len(points)
    flags = SplineFlags.NONE
    if spline.use_cyclic_u:
        flags |= SplineFlags.CYCLIC
    if spline.use_endpoint_u:
        flags |= SplineFlags.ENDPOINT
    if spline.use_bezier_u:
        flags |= SplineFlags.BEZIER_KNOTS
    result = Spline(
        _SPLINE_TYPES[spline.type], flags, spline.order_u, spline.resolution_u
    )
    result.radii = _point_floats(points, "radius", count)
    result.tilts = _point_floats(points, "tilt", count)
    if is_bezier:
        result.positions = _point_floats(points, "co", count * 3)
        result.weights = [1.0] * count
        result.left_handles = _point_floats(points, "handle_left", count * 3)
        result.right_handles = _point_floats(points, "handle_right", count * 3)
        result.handle_types = [
            _HANDLE_TYPES[kind]
            for point in points
            for kind in (point.handle_left_type, point.handle_right_type)
        ]
    else:
        stored = _point_floats(points, "co", count * _CURVE_POINT_FIELDS)
        result.positions = [
            v for at in range(count) for v in stored[at * 4 : at * 4 + 3]
        ]
        result.weights = stored[3::4]
    return result


def _point_floats(points, name: str, length: int) -> list:
    buffer = array("f", [0.0]) * length
    if length:
        points.foreach_get(name, buffer)
    return list(buffer)


def _capture_curve(curve: bpy.types.Curve, object_index: dict) -> Curve:
    return Curve(
        curve.name,
        [_capture_spline(spline) for spline in curve.splines],
        rna_values.capture_rna(curve, object_index),
        rna_values.capture_properties(curve),
    )


def _capture_lattice(obj: bpy.types.Object) -> Lattice:
    lattice = obj.data
    points = lattice.points
    count = len(points)
    result = Lattice(
        lattice.name,
        lattice.points_u,
        lattice.points_v,
        lattice.points_w,
        _point_floats(points, "co", count * _LATTICE_POINT_AXES),
        _point_floats(points, "co_deform", count * _LATTICE_POINT_AXES),
        _point_floats(points, "weight_softbody", count),
        settings=rna_values.capture_rna(
            lattice, {}, skip={"points_u", "points_v", "points_w"}
        ),
        properties=rna_values.capture_properties(lattice),
    )
    result.vertex_groups = [group.name for group in obj.vertex_groups]
    if result.vertex_groups:
        offsets, groups, values = [0], [], []
        for point in points:
            for element in point.groups:
                groups.append(element.group)
                values.append(element.weight)
            offsets.append(len(values))
        result.weight_offsets = offsets
        result.weight_groups = groups
        result.weight_values = values
    return result


def _create_curve(source: Curve) -> bpy.types.Curve:
    curve = bpy.data.curves.new(source.name, "CURVE")
    for stored in source.splines:
        spline = curve.splines.new(stored.spline_type.name)
        is_bezier = stored.spline_type == SplineType.BEZIER
        points = spline.bezier_points if is_bezier else spline.points
        points.add(stored.point_count - len(points))
        if is_bezier:
            points.foreach_set("co", stored.positions)
            points.foreach_set("handle_left", stored.left_handles)
            points.foreach_set("handle_right", stored.right_handles)
            for at, point in enumerate(points):
                point.handle_left_type = HandleType(stored.handle_types[at * 2]).name
                point.handle_right_type = HandleType(
                    stored.handle_types[at * 2 + 1]
                ).name
        else:
            flat = [
                v
                for at in range(stored.point_count)
                for v in (*stored.positions[at * 3 : at * 3 + 3], stored.weights[at])
            ]
            points.foreach_set("co", flat)
        points.foreach_set("radius", stored.radii)
        points.foreach_set("tilt", stored.tilts)
        spline.use_cyclic_u = bool(stored.flags & SplineFlags.CYCLIC)
        spline.use_endpoint_u = bool(stored.flags & SplineFlags.ENDPOINT)
        spline.use_bezier_u = bool(stored.flags & SplineFlags.BEZIER_KNOTS)
        spline.order_u = stored.order
        spline.resolution_u = stored.resolution
    return curve


def _create_lattice(source: Lattice) -> bpy.types.Lattice:
    lattice = bpy.data.lattices.new(source.name)
    lattice.points_u = source.points_u
    lattice.points_v = source.points_v
    lattice.points_w = source.points_w
    lattice.points.foreach_set("co_deform", source.deformed)
    lattice.points.foreach_set("weight_softbody", source.softbody_weights)
    return lattice


def _apply_lattice_groups(source: Lattice, obj: bpy.types.Object) -> None:
    groups = [obj.vertex_groups.new(name=name) for name in source.vertex_groups]
    for point in range(len(source.weight_offsets) - 1):
        start, end = source.weight_offsets[point], source.weight_offsets[point + 1]
        for at in range(start, end):
            groups[source.weight_groups[at]].add(
                [point], source.weight_values[at], "REPLACE"
            )


def _capture_pose(obj: bpy.types.Object, bones: dict) -> list:
    channels = []
    for pose_bone in obj.pose.bones:
        rotation = _rotation_of(pose_bone)
        location, scale = tuple(pose_bone.location), tuple(pose_bone.scale)
        is_rest_rotation = (
            pose_bone.rotation_mode == "QUATERNION" and rotation == _IDENTITY_QUATERNION
        ) or (pose_bone.rotation_mode != "QUATERNION" and not any(rotation))
        if is_rest_rotation and not any(location) and scale == (1.0, 1.0, 1.0):
            continue
        channels.append(
            PoseChannel(
                bones[pose_bone.name],
                location,
                rotation,
                scale,
                _ROTATION_MODES[pose_bone.rotation_mode],
            )
        )
    return channels


class _SceneCapture:
    """Builds a Scene, sharing data blocks between objects."""

    def __init__(self, scene: bpy.types.Scene, objects: list, embed_textures: bool):
        self._objects = objects
        self._object_index = {obj.name: at for at, obj in enumerate(objects)}
        self._materials = material_adapter.MaterialCapture(embed_textures)
        self._meshes = {}
        self._lights = {}
        self._armatures = {}
        self._curves = {}
        self._lattices = {}
        self._cameras = {}
        self._collections = {}
        self._collection_list = []
        self._scene = scene

    def _shared(self, table: dict, key, make) -> int:
        if key not in table:
            table[key] = (make(), len(table))
        return table[key][1]

    def _data_index(self, obj: bpy.types.Object) -> int:
        if obj.type == "MESH":
            groups = tuple(group.name for group in obj.vertex_groups)
            self._materials.add_mesh_slots(obj.data)
            return self._shared(
                self._meshes,
                (obj.data.name, groups),
                lambda: self._capture_mesh(obj),
            )
        if obj.type == "LIGHT":
            return self._shared(
                self._lights, obj.data.name, lambda: _capture_light(obj.data)
            )
        if obj.type == "ARMATURE":
            return self._shared(
                self._armatures, obj.data.name, lambda: _capture_armature(obj.data)
            )
        if obj.type == "CURVE":
            return self._shared(
                self._curves,
                obj.data.name,
                lambda: _capture_curve(obj.data, self._object_index),
            )
        if obj.type == "LATTICE":
            return self._shared(
                self._lattices, obj.data.name, lambda: _capture_lattice(obj)
            )
        if obj.type == "CAMERA":
            return self._shared(
                self._cameras, obj.data.name, lambda: _capture_camera(obj.data)
            )
        return NO_INDEX

    def _capture_mesh(self, obj: bpy.types.Object):
        mesh = mesh_adapter.capture_object(obj)
        mesh.properties = rna_values.capture_properties(obj.data)
        if obj.data.shape_keys is not None:
            mesh.drivers = rna_values.capture_drivers(
                obj.data.shape_keys, self._object_index
            )
        return mesh

    def _capture_skin(self, obj: bpy.types.Object, result: SceneObject) -> None:
        for modifier in obj.modifiers:
            if modifier.type != _ARMATURE_MODIFIER or modifier.object is None:
                continue
            if modifier.object.name not in self._object_index:
                continue
            result.skin_armature = self._object_index[modifier.object.name]
            flags = SkinFlags.NONE
            if modifier.use_vertex_groups:
                flags |= SkinFlags.VERTEX_GROUPS
            if modifier.use_bone_envelopes:
                flags |= SkinFlags.BONE_ENVELOPES
            if modifier.use_deform_preserve_volume:
                flags |= SkinFlags.PRESERVE_VOLUME
            result.skin_flags = flags
            bones = {b.name: at for at, b in enumerate(modifier.object.data.bones)}
            result.skin_bones = [
                bones.get(group.name, NO_INDEX) for group in obj.vertex_groups
            ]
            return

    def _capture_object(self, obj: bpy.types.Object) -> SceneObject:
        flags = ObjectFlags.NONE
        if obj.hide_viewport:
            flags |= ObjectFlags.HIDDEN_IN_VIEWPORT
        if obj.hide_render:
            flags |= ObjectFlags.HIDDEN_IN_RENDER
        if obj.hide_select:
            flags |= ObjectFlags.NOT_SELECTABLE
        result = SceneObject(
            obj.name,
            _OBJECT_TYPES.get(obj.type, ObjectType.EMPTY),
            self._data_index(obj),
            self._object_index.get(obj.parent.name, NO_INDEX)
            if obj.parent
            else NO_INDEX,
            rotation_mode=_ROTATION_MODES[obj.rotation_mode],
            flags=flags,
            location=tuple(obj.location),
            rotation=_rotation_of(obj),
            scale=tuple(obj.scale),
            parent_inverse=tuple(v for row in obj.matrix_parent_inverse for v in row),
            parent_type=_PARENT_TYPES[obj.parent_type],
            parent_vertices=tuple(obj.parent_vertices),
            settings=rna_values.capture_rna(
                obj, self._object_index, skip=_OBJECT_FIELDS
            ),
            material_overrides=[
                {"slot": at, "material": self._materials.material_index(slot.material)}
                for at, slot in enumerate(obj.material_slots)
                if slot.link == _OBJECT_SLOT_LINK and slot.material is not None
            ],
        )
        if obj.parent_type == _BONE_PARENT and obj.parent is not None:
            bones = [bone.name for bone in obj.parent.data.bones]
            if obj.parent_bone in bones:
                result.parent_bone = bones.index(obj.parent_bone)
        if obj.instance_collection is not None:
            result.instance_collection = self._collection_index(obj.instance_collection)
        if obj.type == "ARMATURE":
            bones = {bone.name: at for at, bone in enumerate(obj.data.bones)}
            result.pose = _capture_pose(obj, bones)
            result.bone_settings = _capture_bone_settings(
                obj, bones, self._object_index
            )
        self._capture_skin(obj, result)
        result.properties = rna_values.capture_properties(obj)
        result.modifiers = _capture_stack(obj.modifiers, self._object_index)
        result.constraints = _capture_stack(obj.constraints, self._object_index)
        result.drivers = rna_values.capture_drivers(obj, self._object_index)
        return result

    def _capture_animation(self) -> list:
        fps = self._scene.render.fps / self._scene.render.fps_base
        capture = animation_adapter.ClipCapture(fps)
        for index, obj in enumerate(self._objects):
            capture.add(obj, ResourceKind.OBJECT, index)
        for (name, _), (_, index) in self._meshes.items():
            keys = bpy.data.meshes[name].shape_keys
            if keys is not None:
                capture.add(keys, ResourceKind.MESH, index)
        tables = (
            (self._lights, bpy.data.lights, ResourceKind.LIGHT),
            (self._cameras, bpy.data.cameras, ResourceKind.CAMERA),
            (self._armatures, bpy.data.armatures, ResourceKind.ARMATURE),
            (self._curves, bpy.data.curves, ResourceKind.CURVE),
            (self._lattices, bpy.data.lattices, ResourceKind.LATTICE),
        )
        for table, blocks, kind in tables:
            for name, (_, index) in table.items():
                capture.add(blocks[name], kind, index)
        for index, material in enumerate(self._materials.materials):
            block = bpy.data.materials[material.name]
            capture.add(block, ResourceKind.MATERIAL, index)
            if block.node_tree is not None:
                capture.add(
                    block.node_tree,
                    ResourceKind.MATERIAL,
                    index,
                    animation_adapter.NODE_TREE_PREFIX,
                )
        return capture.clips

    def _collection_index(self, collection, parent: int = NO_INDEX) -> int:
        if collection.name in self._collections:
            return self._collections[collection.name]
        index = len(self._collection_list)
        self._collections[collection.name] = index
        members = [
            self._object_index[item.name]
            for item in collection.objects
            if item.name in self._object_index
        ]
        self._collection_list.append(
            Collection(
                collection.name,
                parent,
                members,
                rna_values.capture_properties(collection),
            )
        )
        for child in collection.children:
            self._collection_index(child, index)
        return index

    def capture(self) -> Scene:
        for child in self._scene.collection.children:
            self._collection_index(child)
        objects = [self._capture_object(obj) for obj in self._objects]
        return Scene(
            SceneSpace(meters_per_unit=_meters_per_unit(self._scene)),
            [mesh for mesh, _ in self._meshes.values()],
            self._materials.materials,
            self._materials.samplers,
            self._materials.images,
            objects,
            self._collection_list,
            [light for light, _ in self._lights.values()],
            [armature for armature, _ in self._armatures.values()],
            [curve for curve, _ in self._curves.values()],
            [lattice for lattice, _ in self._lattices.values()],
            [camera for camera, _ in self._cameras.values()],
            self._capture_animation(),
            self._materials.extensions,
            rna_values.capture_properties(self._scene),
            self._capture_scene_settings(),
        )

    def _capture_scene_settings(self) -> dict:
        scene = self._scene
        settings = {
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "frame_current": scene.frame_current,
        }
        if scene.camera is not None and scene.camera.name in self._object_index:
            settings["camera"] = Reference(self._object_index[scene.camera.name])
        return settings


def capture_scene(
    scene: bpy.types.Scene, objects: list, *, embed_textures: bool = False
) -> Scene:
    """Copy these objects and what they use. Other object types become empties."""
    return _SceneCapture(scene, objects, embed_textures).capture()


def _capture_camera(camera: bpy.types.Camera) -> Camera:
    return Camera(
        camera.name,
        rna_values.capture_rna(camera, {}),
        rna_values.capture_properties(camera),
    )


def _create_light(source: Light) -> bpy.types.Light:
    light = bpy.data.lights.new(source.name, source.light_type.name)
    light.color = source.color
    light.energy = source.power
    light.use_shadow = source.casts_shadow
    if source.light_type == LightType.SUN:
        light.angle = source.sun_angle
    else:
        light.shadow_soft_size = source.radius
    if source.light_type == LightType.SPOT:
        light.spot_size = source.spot_angle
        light.spot_blend = source.spot_blend
    if source.light_type == LightType.AREA:
        light.shape = source.area_shape.name
        light.size = source.area_width
        light.size_y = source.area_height
    rna_values.apply_rna(light, source.settings, [])
    rna_values.apply_properties(light, source.properties)
    return light


def _create_armature_bones(armature: bpy.types.Armature, source: Armature, obj) -> None:
    """Add bones in edit mode, the only place Blender allows it."""
    active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edits = [armature.edit_bones.new(bone.name) for bone in source.bones]
        for edit, bone in zip(edits, source.bones, strict=True):
            edit.head = bone.head
            edit.tail = bone.tail
            edit.roll = bone.roll
            edit.envelope_distance = bone.envelope_distance
            edit.head_radius = bone.head_radius
            edit.tail_radius = bone.tail_radius
            edit.use_deform = bone.is_deform
            edit.use_inherit_rotation = bone.inherits_rotation
            edit.inherit_scale = bone.inherit_scale.name
            rna_values.apply_rna(edit, bone.settings, [])
            if bone.parent != NO_INDEX:
                edit.parent = edits[bone.parent]
                edit.use_connect = bone.is_connected
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = active
    rna_values.apply_rna(armature, source.settings, [obj])
    rna_values.apply_properties(armature, source.properties)
    _apply_bone_collections(armature, source.bone_collections)
    for bone in source.bones:
        rna_values.apply_properties(armature.bones[bone.name], bone.properties)


def _apply_space(source: Scene, roots: list, scene: bpy.types.Scene) -> None:
    default = SceneSpace(meters_per_unit=_meters_per_unit(scene))
    if source.space == default:
        return
    scale = source.space.meters_per_unit / default.meters_per_unit
    axes = axis_conversion(
        from_forward=_AXIS_NAMES[source.space.forward],
        from_up=_AXIS_NAMES[source.space.up],
    ).to_4x4()
    for root in roots:
        root.matrix_world = axes @ Matrix.Scale(scale, 4) @ root.matrix_world


class _SceneBuilder:
    """Creates a Scene's data blocks, and removes all of them if any step fails."""

    def __init__(self, source: Scene, context):
        self._source = source
        self._context = context
        self._created = []
        self._materials = []

    def _keep(self, block):
        self._created.append(block)
        return block

    def _object_data(self, item: SceneObject, tables: dict):
        if item.data == NO_INDEX:
            return None
        if item.object_type not in tables:
            raise FormatError(f"Object {item.name} has data of an unknown kind")
        return tables[item.object_type][item.data]

    def _place(self, item: SceneObject, obj: bpy.types.Object) -> None:
        obj.location = item.location
        _set_rotation(obj, item.rotation_mode, item.rotation)
        obj.scale = item.scale
        obj.hide_viewport = bool(item.flags & ObjectFlags.HIDDEN_IN_VIEWPORT)
        obj.hide_render = bool(item.flags & ObjectFlags.HIDDEN_IN_RENDER)
        obj.hide_select = bool(item.flags & ObjectFlags.NOT_SELECTABLE)

    def _create_meshes(self) -> list:
        source = self._source
        images = [self._keep(i) for i in material_adapter.create_images(source.images)]
        graphs = material_adapter.read_graphs(source.extensions)
        materials = {
            m.name: self._keep(
                material_adapter.create_material(m, images, source.samplers, graphs)
            )
            for m in source.materials
        }
        self._materials = list(materials.values())
        meshes = []
        for index, mesh in enumerate(source.meshes):
            names = [
                o.name
                for o in source.objects
                if o.data == index and o.object_type == ObjectType.MESH
            ]
            name = names[0] if names else f"Mesh {index}"
            created = self._keep(mesh_adapter.create_mesh(mesh, name=name))
            rna_values.apply_properties(created, mesh.properties)
            for at, slot in enumerate(mesh.material_slots):
                if slot in materials:
                    created.materials[at] = materials[slot]
            meshes.append(created)
        return meshes

    def _link_parents(self, objects: list) -> None:
        source = self._source
        for item, obj in zip(source.objects, objects, strict=True):
            if item.parent == NO_INDEX:
                continue
            obj.parent = objects[item.parent]
            obj.parent_type = item.parent_type.name
            if item.parent_type in (ParentType.VERTEX, ParentType.VERTEX_3):
                obj.parent_vertices = item.parent_vertices
            matrix = item.parent_inverse
            obj.matrix_parent_inverse = Matrix(
                [matrix[at : at + 4] for at in range(0, 16, 4)]
            )
            if item.parent_bone != NO_INDEX:
                parent = source.objects[item.parent]
                obj.parent_bone = (
                    source.armatures[parent.data].bones[item.parent_bone].name
                )

    def _apply_object_settings(self, objects: list) -> None:
        source = self._source
        for item, obj in zip(source.objects, objects, strict=True):
            rna_values.apply_properties(obj, item.properties)
            rna_values.apply_rna(obj, item.settings, objects)
            for override in item.material_overrides:
                slot = obj.material_slots[override["slot"]]
                slot.link = _OBJECT_SLOT_LINK
                slot.material = self._materials[override["material"]]
            _apply_modifiers(obj, item.modifiers, objects)
            _apply_constraints(obj, item.constraints, objects)
            if item.object_type == ObjectType.ARMATURE and item.data != NO_INDEX:
                bones = source.armatures[item.data].bones
                for stored in item.bone_settings:
                    pose_bone = obj.pose.bones[bones[stored["bone"]].name]
                    settings = dict(stored["settings"])
                    shape_bone = settings.pop(_SHAPE_TRANSFORM, "")
                    rna_values.apply_rna(pose_bone, settings, objects)
                    if shape_bone:
                        pose_bone.custom_shape_transform = obj.pose.bones[shape_bone]
                    _apply_constraints(pose_bone, stored["constraints"], objects)
            rna_values.apply_drivers(obj, item.drivers, objects)
        self._apply_shape_key_drivers(objects)

    def _apply_shape_key_drivers(self, objects: list) -> None:
        source = self._source
        for index, mesh in enumerate(source.meshes):
            owner = next(
                (
                    obj
                    for item, obj in zip(source.objects, objects, strict=True)
                    if item.object_type == ObjectType.MESH and item.data == index
                ),
                None,
            )
            if owner is not None and owner.data.shape_keys is not None:
                rna_values.apply_drivers(owner.data.shape_keys, mesh.drivers, objects)

    def _link_skins_and_poses(self, objects: list) -> None:
        source = self._source
        for item, obj in zip(source.objects, objects, strict=True):
            if item.skin_armature != NO_INDEX and not item.modifiers:
                modifier = obj.modifiers.new(_SKIN_MODIFIER_NAME, _ARMATURE_MODIFIER)
                modifier.object = objects[item.skin_armature]
                modifier.use_vertex_groups = bool(
                    item.skin_flags & SkinFlags.VERTEX_GROUPS
                )
                modifier.use_bone_envelopes = bool(
                    item.skin_flags & SkinFlags.BONE_ENVELOPES
                )
                modifier.use_deform_preserve_volume = bool(
                    item.skin_flags & SkinFlags.PRESERVE_VOLUME
                )
            if item.pose:
                bones = source.armatures[item.data].bones
                for channel in item.pose:
                    pose_bone = obj.pose.bones[bones[channel.bone].name]
                    pose_bone.location = channel.location
                    pose_bone.scale = channel.scale
                    _set_rotation(pose_bone, channel.rotation_mode, channel.rotation)

    def _link_collections(self, objects: list) -> None:
        source = self._source
        collections = [
            self._keep(bpy.data.collections.new(item.name))
            for item in source.collections
        ]
        linked = set()
        for item, collection in zip(source.collections, collections, strict=True):
            parent = (
                collections[item.parent]
                if item.parent != NO_INDEX
                else self._context.scene.collection
            )
            parent.children.link(collection)
            rna_values.apply_properties(collection, item.properties)
            for member in item.members:
                collection.objects.link(objects[member])
                linked.add(member)
        for index, obj in enumerate(objects):
            if index not in linked:
                self._context.collection.objects.link(obj)
        for item, obj in zip(source.objects, objects, strict=True):
            if item.instance_collection != NO_INDEX:
                obj.instance_type = "COLLECTION"
                obj.instance_collection = collections[item.instance_collection]

    def build(self) -> list:
        source = self._source
        meshes = self._create_meshes()
        lights = [self._keep(_create_light(light)) for light in source.lights]
        armatures = [
            self._keep(bpy.data.armatures.new(a.name)) for a in source.armatures
        ]
        curves = [self._keep(_create_curve(curve)) for curve in source.curves]
        lattices = [self._keep(_create_lattice(item)) for item in source.lattices]
        cameras = [self._keep(bpy.data.cameras.new(c.name)) for c in source.cameras]
        for curve, stored in zip(curves, source.curves, strict=True):
            rna_values.apply_properties(curve, stored.properties)
        for camera, stored in zip(cameras, source.cameras, strict=True):
            rna_values.apply_rna(camera, stored.settings, [])
            rna_values.apply_properties(camera, stored.properties)
        for lattice, stored in zip(lattices, source.lattices, strict=True):
            rna_values.apply_rna(lattice, stored.settings, [])
            rna_values.apply_properties(lattice, stored.properties)
        tables = {
            ObjectType.MESH: meshes,
            ObjectType.LIGHT: lights,
            ObjectType.ARMATURE: armatures,
            ObjectType.CURVE: curves,
            ObjectType.LATTICE: lattices,
            ObjectType.CAMERA: cameras,
        }
        objects = []
        for item in source.objects:
            data = self._object_data(item, tables)
            obj = self._keep(bpy.data.objects.new(item.name, data))
            self._place(item, obj)
            objects.append(obj)
        self._link_collections(objects)
        for index, armature in enumerate(source.armatures):
            owner = next(
                (
                    obj
                    for item, obj in zip(source.objects, objects, strict=True)
                    if item.object_type == ObjectType.ARMATURE and item.data == index
                ),
                None,
            )
            if owner is not None:
                _create_armature_bones(armatures[index], armature, owner)
        for item, obj in zip(source.objects, objects, strict=True):
            if item.object_type == ObjectType.MESH and item.data != NO_INDEX:
                mesh = source.meshes[item.data]
                mesh_adapter.apply_vertex_groups(mesh, obj)
                if obj.data.shape_keys is None:
                    mesh_adapter.apply_shape_keys(mesh, obj)
            if item.object_type == ObjectType.LATTICE and item.data != NO_INDEX:
                _apply_lattice_groups(source.lattices[item.data], obj)
        self._link_parents(objects)
        for curve, stored in zip(curves, source.curves, strict=True):
            rna_values.apply_rna(curve, stored.settings, objects)
        self._apply_object_settings(objects)
        self._link_skins_and_poses(objects)
        owners = {
            ResourceKind.OBJECT: objects,
            ResourceKind.MESH: [mesh.shape_keys for mesh in meshes],
            ResourceKind.MATERIAL: self._materials,
            ResourceKind.LIGHT: lights,
            ResourceKind.CAMERA: cameras,
            ResourceKind.ARMATURE: armatures,
            ResourceKind.CURVE: curves,
            ResourceKind.LATTICE: lattices,
        }
        animation_adapter.apply_clips(
            source.clips,
            lambda kind, owner: owners[kind][owner] if kind in owners else None,
            self._created,
        )
        rna_values.apply_properties(self._context.scene, source.properties)
        camera = source.settings.get("camera")
        if camera is not None and self._context.scene.camera is None:
            self._context.scene.camera = objects[camera.index]
        roots = [
            obj
            for item, obj in zip(source.objects, objects, strict=True)
            if item.parent == NO_INDEX
        ]
        _apply_space(source, roots, self._context.scene)
        return objects

    def remove_all(self) -> None:
        bpy.data.batch_remove(self._created)


def build_scene(source: Scene, context) -> list:
    """Create everything in a Scene and return its objects. A failure leaves nothing."""
    builder = _SceneBuilder(source, context)
    try:
        return builder.build()
    except Exception:
        builder.remove_all()
        raise
