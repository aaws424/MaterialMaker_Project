"""Material Maker Bridge: open Material Maker (.ptex) materials in Blender.

Blender-dependent modules are imported inside register() so that
`mm_bridge.core` stays importable from plain Python (tests, tools).
"""

import importlib
import sys

_BLENDER_MODULES = (
    "gpu_render.shader", "gpu_render.renderer", "gpu_render.images",
    "library_cache", "props", "helpers", "material_builder", "session", "preferences", "operators", "ui",
    "graph_editor",
)
_classes = ()


def _import_blender_modules():
    modules = {}
    for name in _BLENDER_MODULES:
        full_name = "%s.%s" % (__name__, name)
        if full_name in sys.modules:
            modules[name] = importlib.reload(sys.modules[full_name])
        else:
            modules[name] = importlib.import_module(full_name)
    return modules


def register():
    global _classes
    import bpy

    m = _import_blender_modules()
    _classes = (m["props"].classes + m["preferences"].classes + m["operators"].classes + m["ui"].classes
                + m["graph_editor"].classes)
    for cls in _classes:
        bpy.utils.register_class(cls)
    m["props"].register_settings()
    bpy.types.TOPBAR_MT_file_import.append(m["operators"].menu_func_import)
    m["session"].register()
    m["graph_editor"].register()


def unregister():
    import bpy

    from . import graph_editor, library_cache, operators, props, session

    graph_editor.unregister()
    session.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(operators.menu_func_import)
    props.unregister_settings()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    library_cache.clear()
