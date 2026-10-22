"""Pure-Python port of Material Maker's shader generator.

Nothing in this package imports bpy, so it can be unit-tested with a plain
Python interpreter. Large parts are a direct port of Material Maker 1.3
(https://github.com/RodZill4/material-maker, MIT License,
Copyright (c) 2018-2023 Rodolphe Suescun and contributors).
"""

from .library import NodeLibrary
from .loader import Project, load_ptex
from .compiler import compile_material, compile_output, MaterialProgram
from .glsl import to_blender_glsl

__all__ = [
    "NodeLibrary",
    "Project",
    "load_ptex",
    "compile_material",
    "compile_output",
    "MaterialProgram",
    "to_blender_glsl",
]
