# SPDX-License-Identifier: GPL-3.0-or-later

"""File operators and menus; format and mesh logic live in separate modules."""

import logging

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..format import FormatError, load, save
from . import scene_adapter

logger = logging.getLogger(__name__)


class IMPORT_MESH_OT_zpm(bpy.types.Operator, ImportHelper):
    """Import objects, meshes, materials, lights and armatures"""

    bl_idname = "import_mesh.zpm"
    bl_label = "Import ZPM"
    bl_options = {"UNDO"}

    filename_ext = ".zpm"
    filter_glob: StringProperty(default="*.zpm", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and context.collection is not None

    def execute(self, context):
        try:
            objects = scene_adapter.build_scene(load(self.filepath), context)
        except (OSError, FormatError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception:
            logger.exception("ZPM import failed")
            self.report({"ERROR"}, "ZPM import failed; see Blender's console")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {len(objects):,} objects")
        return {"FINISHED"}


class EXPORT_MESH_OT_zpm(bpy.types.Operator, ExportHelper):
    """Export the selected objects, or the whole scene when none is selected"""

    bl_idname = "export_mesh.zpm"
    bl_label = "Export ZPM"

    filename_ext = ".zpm"
    filter_glob: StringProperty(default="*.zpm", options={"HIDDEN"})
    embed_textures: BoolProperty(
        name="Embed Textures",
        description="Store image files inside the .zpm instead of linking to them",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        self.layout.prop(self, "embed_textures")
        box = self.layout.box()
        box.label(text="Selected objects, or all if none selected.", icon="SCENE_DATA")
        box.label(text="Base meshes with all surface data, materials, images.")
        box.label(text="Lights, armatures, poses, collections, parenting, units.")
        box.label(text="Curves, lattices, point clouds, hair and volumes too.")

    def execute(self, context):
        try:
            objects = context.selected_objects or list(context.scene.objects)
            scene = scene_adapter.capture_scene(
                context.scene, objects, embed_textures=self.embed_textures
            )
            save(self.filepath, scene)
        except (OSError, FormatError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception:
            logger.exception("ZPM export failed")
            self.report({"ERROR"}, "ZPM export failed; see Blender's console")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(objects):,} objects")
        return {"FINISHED"}


def _import_menu(self, context):
    self.layout.operator(IMPORT_MESH_OT_zpm.bl_idname, text="ZPM (.zpm)")


def _export_menu(self, context):
    self.layout.operator(EXPORT_MESH_OT_zpm.bl_idname, text="ZPM (.zpm)")


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
