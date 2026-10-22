"""Blender properties: per-material settings, plus one generated PropertyGroup per
parameter layout (schema) holding the Material Maker parameters with their real
ranges, steps and enum items.

Each material gets its own generated class, attached to bpy.types.Material as
`mmb_<uid>` (core.schema.schema_attr). Property keys are stable per (node, parameter), so
values stored in the .blend survive re-registration after loading a file or editing the graph.
"""

import math

import bpy

from .core import schema as S


def _on_setting_changed(self, context):
    from . import session

    session.on_settings_changed(self.id_data)


_SIZES = [(str(e), str(2 ** e), "%d x %d pixels" % (2 ** e, 2 ** e)) for e in range(7, 14)]
RESOLUTION_ITEMS = [("0", "From File", "Use the resolution set in the Material Maker file")] + _SIZES


class MMB_ImageSlot(bpy.types.PropertyGroup):
    channel: bpy.props.StringProperty()
    image: bpy.props.PointerProperty(type=bpy.types.Image)


class MMB_MaterialSettings(bpy.types.PropertyGroup):
    is_mm: bpy.props.BoolProperty(default=False, options={"HIDDEN"})
    uid: bpy.props.StringProperty(options={"HIDDEN"})
    source: bpy.props.StringProperty(description="The imported .ptex file", options={"HIDDEN"})
    filepath: bpy.props.StringProperty(name="File", subtype="FILE_PATH", description="Imported .ptex file")
    schema_attr: bpy.props.StringProperty(options={"HIDDEN"})
    resolution: bpy.props.EnumProperty(
        name="Resolution", items=RESOLUTION_ITEMS, default="0", update=_on_setting_changed,
        description="Texture resolution")
    preview_resolution: bpy.props.EnumProperty(
        name="Preview", items=_SIZES, default="9",
        description="Resolution used while a value is being dragged; full resolution follows when it settles")
    live_update: bpy.props.BoolProperty(
        name="Live Update", default=True, update=_on_setting_changed,
        description="Re-render the textures while parameters change")
    images: bpy.props.CollectionProperty(type=MMB_ImageSlot)
    graph_tree: bpy.props.PointerProperty(type=bpy.types.NodeTree, options={"HIDDEN"},
                                          description="Material Maker graph editor tree of this material")


def _decimals(step):
    if not step or step <= 0:
        return 3
    return max(0, min(6, int(math.ceil(-math.log10(step) - 1e-9))))


def _update_callback(key):
    def update(self, context):
        from . import session

        session.on_param_changed(self.id_data, key)
    return update


def _make_property(spec):
    common = dict(name=spec.label, description=spec.description[:1000], update=_update_callback(spec.key))
    k = spec.kind
    if k == S.STRING:
        return bpy.props.StringProperty(default=str(spec.value), **common)
    if k == S.FILE:
        return bpy.props.StringProperty(default=str(spec.value), subtype="FILE_PATH", **common)
    if k == S.FLOAT:
        kw = dict(default=float(spec.value), precision=_decimals(spec.step))
        if spec.step:
            kw["step"] = max(1, min(100, int(round(spec.step * 100))))
        if spec.soft_min is not None and spec.soft_max is not None and spec.soft_min < spec.soft_max:
            kw.update(soft_min=float(spec.soft_min), soft_max=float(spec.soft_max))
        return bpy.props.FloatProperty(**kw, **common)
    if k == S.SEED:
        return bpy.props.FloatProperty(default=float(spec.value), precision=4, step=1, **common)
    if k == S.EXPRESSION:
        return bpy.props.StringProperty(default=str(spec.value), **common)
    if k in (S.ENUM, S.SIZE):
        items = [(ident, label, "") for ident, label in spec.items]
        return bpy.props.EnumProperty(items=items, default=str(spec.value), **common)
    if k == S.BOOL:
        return bpy.props.BoolProperty(default=bool(spec.value), **common)
    if k == S.COLOR:
        # Material Maker colors are sRGB-encoded values, like a color picker's display values.
        return bpy.props.FloatVectorProperty(size=4, subtype="COLOR_GAMMA", default=tuple(spec.value),
                                             soft_min=0.0, soft_max=1.0, **common)
    return None


_schema_classes = {}  # attr -> (class, signature)


def ensure_schema_class(attr, sections):
    """Register the PropertyGroup for a parameter layout and attach it to Material as `attr`.

    When the layout of `attr` changed (graph edited), the class is replaced; values stored
    under keys that still exist are kept by Blender, since keys are stable.
    """
    signature = S.schema_signature(sections)
    registered = _schema_classes.get(attr)
    if registered is not None:
        if registered[1] == signature:
            return registered[0]
        _unregister_schema_class(attr)
    annotations = {}
    for section in sections:
        for spec in section.specs:
            prop = _make_property(spec)
            if prop is not None:
                annotations[spec.key] = prop
    cls = type("MMB_PG_" + attr, (bpy.types.PropertyGroup,), {"__annotations__": annotations})
    bpy.utils.register_class(cls)
    setattr(bpy.types.Material, attr, bpy.props.PointerProperty(type=cls))
    _schema_classes[attr] = (cls, signature)
    return cls


def _unregister_schema_class(attr):
    cls, _signature = _schema_classes.pop(attr)
    if hasattr(bpy.types.Material, attr):
        delattr(bpy.types.Material, attr)
    bpy.utils.unregister_class(cls)


def unregister_schema_classes():
    for attr in list(_schema_classes):
        _unregister_schema_class(attr)


classes = (MMB_ImageSlot, MMB_MaterialSettings)


def register_settings():
    bpy.types.Material.mmb = bpy.props.PointerProperty(type=MMB_MaterialSettings)


def unregister_settings():
    unregister_schema_classes()
    del bpy.types.Material.mmb
