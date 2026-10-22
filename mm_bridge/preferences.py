import glob
import os
import string

import bpy

from . import library_cache

# Where Material Maker is commonly unpacked on Windows; checked by "Detect".
_SEARCH_PATTERNS = [
    "{drive}:/Program Files/material_maker*",
    "{drive}:/Program Files (x86)/material_maker*",
    "{drive}:/material_maker*",
    os.path.expanduser("~/Downloads/material_maker*"),
    os.path.expanduser("~/Desktop/material_maker*"),
]


def is_mm_install(path):
    return bool(path) and os.path.isfile(os.path.join(path, "nodes", "io_types.mmt"))


def find_mm_installs():
    found = []
    drives = [d for d in string.ascii_uppercase if os.path.exists("%s:/" % d)] if os.name == "nt" else [""]
    for pattern in _SEARCH_PATTERNS:
        patterns = [pattern.format(drive=d) for d in drives] if "{drive}" in pattern else [pattern]
        for p in patterns:
            for candidate in sorted(glob.glob(p), reverse=True):
                if is_mm_install(candidate) and candidate not in found:
                    found.append(os.path.normpath(candidate))
    return found


def get_prefs(context=None):
    context = context or bpy.context
    return context.preferences.addons[__package__].preferences


def _on_install_dir_changed(self, context):
    library_cache.clear()


class MMBRIDGE_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    mm_install_dir: bpy.props.StringProperty(
        name="Material Maker Folder",
        description="Folder containing material_maker.exe and the nodes folder",
        subtype="DIR_PATH",
        update=_on_install_dir_changed,
    )

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.prop(self, "mm_install_dir")
        row.operator("mm_bridge.detect_install", text="", icon="VIEWZOOM")

        box = layout.box()
        if not self.mm_install_dir:
            box.label(text="Set the Material Maker folder (or click the search button)", icon="INFO")
        elif not is_mm_install(bpy.path.abspath(self.mm_install_dir)):
            box.label(text="No nodes/io_types.mmt in this folder", icon="ERROR")
        else:
            status = library_cache.status()
            if status:
                box.label(text=status, icon="CHECKMARK")
            box.operator("mm_bridge.check_library", icon="FILE_REFRESH")


class MMBRIDGE_OT_detect_install(bpy.types.Operator):
    bl_idname = "mm_bridge.detect_install"
    bl_label = "Detect Material Maker"
    bl_description = "Search common locations for a Material Maker installation"

    def execute(self, context):
        installs = find_mm_installs()
        if not installs:
            self.report({"WARNING"}, "No Material Maker installation found; set the folder manually")
            return {"CANCELLED"}
        get_prefs(context).mm_install_dir = installs[0]
        self.report({"INFO"}, "Using %s" % installs[0])
        return {"FINISHED"}


class MMBRIDGE_OT_check_library(bpy.types.Operator):
    bl_idname = "mm_bridge.check_library"
    bl_label = "Load Node Library"
    bl_description = "Load Material Maker's node definitions and report what was found"

    def execute(self, context):
        try:
            library = library_cache.get_library(context, reload=True)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if library.errors:
            self.report({"WARNING"}, "%d nodes failed to load: %s" % (
                len(library.errors), ", ".join(sorted(library.errors)[:5])))
        else:
            self.report({"INFO"}, library_cache.status())
        return {"FINISHED"}


classes = (
    MMBRIDGE_AddonPreferences,
    MMBRIDGE_OT_detect_install,
    MMBRIDGE_OT_check_library,
)
