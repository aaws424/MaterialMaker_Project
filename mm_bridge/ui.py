import os

import bpy

from . import helpers, session
from .core import schema as S


def _draw_helper(layout, material, spec):
    node = helpers.find(material, spec.key)
    box = layout.box()
    box.label(text=spec.label)
    if node is None:
        box.label(text="(widget not created yet)", icon="TIME")
    elif spec.kind == S.GRADIENT:
        box.template_color_ramp(node, "color_ramp", expand=True)
    else:
        box.template_curve_mapping(node, "mapping", type="NONE")


def _draw_spec(layout, params, spec):
    if spec.kind == S.UNSUPPORTED:
        row = layout.row()
        row.enabled = False
        row.label(text="%s: %s" % (spec.label, spec.note), icon="INFO")
    elif spec.kind == S.SEED:
        row = layout.row(align=True)
        row.prop(params, spec.key)
        row.operator("mm_bridge.randomize_seed", text="", icon="FILE_REFRESH").key = spec.key
    elif spec.kind == S.EXPRESSION:
        layout.prop(params, spec.key, icon="SCRIPTPLUGINS")
    else:
        layout.prop(params, spec.key)


def _draw_specs(layout, material, params, specs):
    for spec in specs:
        if spec.kind in (S.GRADIENT, S.CURVE):
            _draw_helper(layout, material, spec)
        else:
            _draw_spec(layout, params, spec)


def draw_material(layout, material):
    settings = material.mmb
    s = session._sessions.get(settings.uid)
    if isinstance(s, session.Session) and s.check_helpers(material):
        # Widget edits redraw this panel; not every one reaches the depsgraph handler.
        session._ensure_timer()
    header = layout.column(align=True)
    row = header.row(align=True)
    row.label(text=os.path.basename(settings.filepath) or material.name, icon="NODE_MATERIAL")
    row.operator("mm_bridge.rerender", text="", icon="FILE_REFRESH")
    row.operator("mm_bridge.reload", text="", icon="FILE_TICK")
    row = header.row(align=True)
    row.operator("mm_bridge.edit_graph", icon="NODETREE")
    row.operator("mm_bridge.save_ptex", text="Save .ptex", icon="EXPORT")
    row = header.row(align=True)
    row.prop(settings, "resolution", text="")
    row.prop(settings, "preview_resolution", text="Drag")
    row.prop(settings, "live_update", text="", icon="PLAY" if settings.live_update else "PAUSE")

    if s is None:
        layout.label(text="Preparing...", icon="TIME")
        session._ensure_timer()
        return
    if s.error:
        box = layout.box()
        box.alert = True
        for line in s.error.splitlines()[:6]:
            box.label(text=line[:120], icon="ERROR")
    if s.last_render:
        layout.label(text="Last render: %d px, %d passes, %.0f ms" % (
            s.last_render["size"], s.last_render["passes"], s.last_render["seconds"] * 1000), icon="INFO")

    params = getattr(material, settings.schema_attr, None) if settings.schema_attr else None
    if params is None:
        layout.label(text="Parameters not loaded yet", icon="TIME")
        return
    for section in s.sections:
        if not section.specs and section.kind != "node":
            continue
        idname = "mmb_%s_%s" % (settings.schema_attr, section.key)
        default_closed = section.kind not in ("controls",)
        panel_header, body = layout.panel(idname, default_closed=default_closed)
        prefix = "    " * section.depth
        icon = {"controls": "OPTIONS", "material": "MATERIAL"}.get(section.kind, "NODE")
        panel_header.label(text=prefix + section.label, icon=icon)
        if body is None:
            continue
        if not section.specs:
            body.label(text="Group: its nodes follow below")
            continue
        _draw_specs(body.column(), material, params, section.specs)


class MMBRIDGE_PT_material(bpy.types.Panel):
    bl_label = "Material Maker"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        return context.material is not None and context.material.mmb.is_mm

    def draw(self, context):
        draw_material(self.layout, context.material)


class _SidebarPanel:
    bl_label = "Material Maker"
    bl_region_type = "UI"
    bl_category = "Material Maker"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.active_material is not None and obj.active_material.mmb.is_mm

    def draw(self, context):
        draw_material(self.layout, context.object.active_material)


class MMBRIDGE_PT_view3d(_SidebarPanel, bpy.types.Panel):
    bl_idname = "MMBRIDGE_PT_view3d"
    bl_space_type = "VIEW_3D"


class MMBRIDGE_PT_node_editor(_SidebarPanel, bpy.types.Panel):
    bl_idname = "MMBRIDGE_PT_node_editor"
    bl_space_type = "NODE_EDITOR"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == "ShaderNodeTree" and super().poll(context)


classes = (MMBRIDGE_PT_material, MMBRIDGE_PT_view3d, MMBRIDGE_PT_node_editor)
