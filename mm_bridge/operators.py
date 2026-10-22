import os
import random

import bpy
from bpy_extras.io_utils import ImportHelper

from . import helpers, library_cache, props, session
from .core import compile_material, load_ptex
from .core import schema as S


def _read_ptex(filepath):
    with open(filepath, encoding="utf-8") as fh:
        return fh.read()


def _load_checked(context, filepath, text):
    """Load and compile once up front so problems are reported before anything is created."""
    library = library_cache.get_library(context)
    project = load_ptex(text, library, library_cache.get_install_dir(), project_dir=os.path.dirname(filepath))
    program = compile_material(project)
    return project, program


def reset_parameters(material):
    """Back to the source's values: unset every stored parameter (their defaults are the source's)
    and drop the gradient/curve helper nodes. Blender keeps add-on properties in system storage,
    so material["..."] cannot delete them."""
    params = getattr(material, material.mmb.schema_attr, None) if material.mmb.schema_attr else None
    if params is not None:
        for key in params.bl_rna.properties.keys():
            if key not in ("rna_type", "name"):
                params.property_unset(key)
    helpers.remove_all(material)


def _assign(context, material):
    count = 0
    for obj in context.selected_objects:
        if not hasattr(obj.data, "materials"):
            continue
        if obj.material_slots and obj.active_material_index < len(obj.material_slots):
            obj.material_slots[obj.active_material_index].material = material
        else:
            obj.data.materials.append(material)
        count += 1
    return count


class MMBRIDGE_OT_import_ptex(bpy.types.Operator, ImportHelper):
    """Import a Material Maker material; its parameters stay editable in Blender"""

    bl_idname = "mm_bridge.import_ptex"
    bl_label = "Import Material Maker Material"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".ptex"
    filter_glob: bpy.props.StringProperty(default="*.ptex", options={"HIDDEN"})
    resolution: bpy.props.EnumProperty(name="Resolution", items=props.RESOLUTION_ITEMS, default="0")
    assign: bpy.props.BoolProperty(name="Assign to Selected", default=True,
                                   description="Put the material on the selected objects' active slot")

    def execute(self, context):
        try:
            text = _read_ptex(self.filepath)
            project, program = _load_checked(context, self.filepath, text)
        except Exception as e:
            self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
            return {"CANCELLED"}

        name = os.path.splitext(os.path.basename(self.filepath))[0]
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        for node in [n for n in material.node_tree.nodes if n.type != "OUTPUT_MATERIAL"]:
            material.node_tree.nodes.remove(node)  # the default Principled BSDF; ours replaces it
        settings = material.mmb
        settings.is_mm = True
        settings.uid = session.new_uid()
        settings.source = text
        settings.filepath = self.filepath
        settings.resolution = self.resolution
        settings.schema_attr = S.schema_attr(settings.uid)
        try:
            props.ensure_schema_class(settings.schema_attr, S.build_schema(project))
            s = session.render_now(material)
        except Exception as e:
            bpy.data.materials.remove(material)
            self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
            return {"CANCELLED"}

        assigned = _assign(context, material) if self.assign else 0
        for w in program.warnings:
            print("MM Bridge warning (%s): %s" % (name, w))
        msg = "Imported %s: %d textures at %d px in %.2f s" % (
            name, len(program.channels), s.last_render.get("size", 0), s.last_render.get("seconds", 0.0))
        if assigned:
            msg += ", assigned to %d object%s" % (assigned, "s" if assigned > 1 else "")
        self.report({"WARNING"} if program.warnings else {"INFO"},
                    msg + ("; %d warnings (see console)" % len(program.warnings) if program.warnings else ""))
        return {"FINISHED"}


class _MaterialOperator:
    @classmethod
    def poll(cls, context):
        material = getattr(context, "material", None) or (
            context.object.active_material if context.object else None)
        return material is not None and material.mmb.is_mm

    @staticmethod
    def get_material(context):
        return getattr(context, "material", None) or context.object.active_material


class MMBRIDGE_OT_rerender(_MaterialOperator, bpy.types.Operator):
    """Render the material's textures again at full resolution"""

    bl_idname = "mm_bridge.rerender"
    bl_label = "Re-render"

    def execute(self, context):
        try:
            session.request_render(self.get_material(context), force=True)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class MMBRIDGE_OT_reload(_MaterialOperator, bpy.types.Operator):
    """Read the .ptex file again (parameters go back to the file's values)"""

    bl_idname = "mm_bridge.reload"
    bl_label = "Reload from File"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = self.get_material(context)
        settings = material.mmb
        path = bpy.path.abspath(settings.filepath)
        try:
            text = _read_ptex(path)
            _load_checked(context, path, text)
        except Exception as e:
            self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
            return {"CANCELLED"}
        reset_parameters(material)
        settings.source = text
        settings.schema_attr = S.schema_attr(settings.uid)
        session.drop_session(settings.uid)
        session.render_now(material)
        return {"FINISHED"}


class MMBRIDGE_OT_randomize_seed(_MaterialOperator, bpy.types.Operator):
    """Pick a new random seed"""

    bl_idname = "mm_bridge.randomize_seed"
    bl_label = "Randomize Seed"
    bl_options = {"REGISTER", "UNDO"}

    key: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        material = self.get_material(context)
        params = getattr(material, material.mmb.schema_attr, None)
        if params is None or not hasattr(params, self.key):
            return {"CANCELLED"}
        setattr(params, self.key, random.random())
        return {"FINISHED"}


class MMBRIDGE_OT_compile_ptex(bpy.types.Operator, ImportHelper):
    """Compile a .ptex file and write the generated shaders to Text datablocks (development tool)"""

    bl_idname = "mm_bridge.compile_ptex"
    bl_label = "Compile Material Maker File (Debug)"
    bl_options = {"REGISTER"}

    filename_ext = ".ptex"
    filter_glob: bpy.props.StringProperty(default="*.ptex", options={"HIDDEN"})
    gpu_check: bpy.props.BoolProperty(
        name="Check on GPU",
        description="Also compile every generated shader with Blender's GPU module",
        default=True,
    )

    def execute(self, context):
        try:
            _project, program = _load_checked(context, self.filepath, _read_ptex(self.filepath))
        except Exception as e:
            self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
            return {"CANCELLED"}

        base = os.path.splitext(os.path.basename(self.filepath))[0]
        gpu_errors = []
        for p in program.passes():
            text_name = "mm_%s_%s.glsl" % (base, p.name)
            text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
            text.from_string(p.shader.fragment_source)
            if self.gpu_check:
                from .gpu_render.shader import create_shader
                try:
                    create_shader(p.shader)
                except Exception as e:
                    gpu_errors.append(p.name)
                    print("MM Bridge: GPU compile error in %s:\n%s" % (text_name, e))

        summary = "%s: %d channels (%s), %d buffers" % (
            base, len(program.channels), ", ".join(program.channels), len(program.buffers))
        for w in program.warnings:
            print("MM Bridge warning:", w)
        if gpu_errors:
            self.report({"ERROR"}, summary + "; GPU errors in " + ", ".join(gpu_errors) + " (see console)")
        elif program.warnings:
            self.report({"WARNING"}, summary + "; %d warnings (see console)" % len(program.warnings))
        else:
            self.report({"INFO"}, summary)
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(MMBRIDGE_OT_import_ptex.bl_idname, text="Material Maker (.ptex)")


classes = (
    MMBRIDGE_OT_import_ptex,
    MMBRIDGE_OT_rerender,
    MMBRIDGE_OT_reload,
    MMBRIDGE_OT_randomize_seed,
    MMBRIDGE_OT_compile_ptex,
)
