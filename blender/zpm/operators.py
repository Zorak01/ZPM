# SPDX-License-Identifier: GPL-3.0-or-later

"""File operators and menus; format and mesh logic live in separate modules."""

import logging
from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import mesh_io
from .format import FormatError, Profile, load, save

logger = logging.getLogger(__name__)


class IMPORT_MESH_OT_zpm(bpy.types.Operator, ImportHelper):
    """Import exact mesh geometry; this version contains no scene attributes"""

    bl_idname = "import_mesh.zpm"
    bl_label = "Import ZPM Mesh"
    bl_options = {"UNDO"}

    filename_ext = ".zpm"
    filter_glob: StringProperty(default="*.zpm;*.zmc", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and context.collection is not None

    def execute(self, context):
        mesh = None
        obj = None
        try:
            geometry = load(self.filepath)
            mesh = mesh_io.create(geometry, name=Path(self.filepath).stem)
            obj = bpy.data.objects.new(mesh.name, mesh)
            context.collection.objects.link(obj)
        except (OSError, FormatError, RuntimeError) as error:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
            logger.exception("ZPM import failed")
            self.report({"ERROR"}, "ZPM import failed; see Blender's console")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {geometry.vertex_count:,} vertices")
        return {"FINISHED"}


class EXPORT_MESH_OT_zpm(bpy.types.Operator, ExportHelper):
    """Export the active object's authored positions, faces and edges only"""

    bl_idname = "export_mesh.zpm"
    bl_label = "Export ZPM Mesh"

    filename_ext = ".zpm"
    filter_glob: StringProperty(default="*.zpm", options={"HIDDEN"})
    profile: EnumProperty(
        name="Storage",
        description="Both profiles preserve Float32 position bits and topology",
        items=(
            ("compact", "Compact", "16-bit indices where possible; exact positions"),
            ("raw32", "Raw 32-bit", "Uncompressed reference layout"),
        ),
        default="compact",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == "OBJECT" and obj is not None and obj.type == "MESH"

    def draw(self, context):
        self.layout.prop(self, "profile")
        box = self.layout.box()
        box.label(text="Mesh core only", icon="MESH_DATA")
        box.label(text="Active object, local coordinates, base mesh.")
        box.label(text="Positions, edges, triangles, quads and n-gons.")
        box.label(text="UVs, colors, materials and rig data are not exported.")

    def execute(self, context):
        try:
            geometry = mesh_io.capture(context.active_object.data)
            save(self.filepath, geometry, profile=Profile(self.profile))
        except (OSError, FormatError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception:
            logger.exception("ZPM export failed")
            self.report({"ERROR"}, "ZPM export failed; see Blender's console")
            return {"CANCELLED"}
        self.report(
            {"INFO"}, f"Exported {geometry.vertex_count:,} vertices (mesh core)"
        )
        return {"FINISHED"}


def _import_menu(self, context):
    self.layout.operator(IMPORT_MESH_OT_zpm.bl_idname, text="ZPM Mesh Core (.zpm)")


def _export_menu(self, context):
    self.layout.operator(EXPORT_MESH_OT_zpm.bl_idname, text="ZPM Mesh Core (.zpm)")


CLASSES = (IMPORT_MESH_OT_zpm, EXPORT_MESH_OT_zpm)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_import_menu)
    bpy.types.TOPBAR_MT_file_export.append(_export_menu)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_export_menu)
    bpy.types.TOPBAR_MT_file_import.remove(_import_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
