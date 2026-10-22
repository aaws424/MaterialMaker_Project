"""One NodeLibrary per session, loaded from the folder set in the preferences."""

import os

import bpy

from .core import NodeLibrary

_library = None
_library_dir = None


def clear():
    global _library, _library_dir
    _library = None
    _library_dir = None


def get_library(context=None, reload=False):
    global _library, _library_dir
    from .preferences import find_mm_installs, get_prefs, is_mm_install

    try:
        mm_dir = bpy.path.abspath(get_prefs(context).mm_install_dir)
    except KeyError:
        # Registered without an add-on entry (development): fall back to auto-detection.
        installs = find_mm_installs()
        mm_dir = installs[0] if installs else ""
    if not is_mm_install(mm_dir):
        raise RuntimeError("Material Maker folder is not set or invalid (see the add-on preferences)")
    if reload or _library is None or _library_dir != mm_dir:
        _library = NodeLibrary.from_install(mm_dir)
        _library_dir = mm_dir
    return _library


def get_install_dir():
    return _library_dir or ""


def status():
    if _library is None:
        return ""
    materials = len(_library.get_material_nodes())
    return "%d nodes (%d material types) from %s" % (
        len(_library.generators), materials, os.path.join(_library_dir, "nodes"))
