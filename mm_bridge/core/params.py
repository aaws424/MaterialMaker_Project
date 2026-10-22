"""Live parameter values: which uniforms a generator tree currently feeds its shaders,
and which parameter changes need the shaders regenerated.

The uniform names are the ones generate_parameter_declarations() emits, so these
values can be handed straight to BlenderShader.pack_params().
"""

from .generators import GenBuffer, GenGraph, GenIterateBuffer, GenRemote, GenShader, GenText
from .mmtypes import Color, Curve, Gradient, deserialize_value, is_float


def generator_uniforms(gen):
    """{uniform name: value tuple} for one generator."""
    rv = {}
    genname = gen.genname
    if isinstance(gen, GenShader):
        if gen.has_randomness():
            rv["seed_" + genname] = (float(gen.get_seed()),)
        for p in gen.get_parameter_defs():
            t = p.get("type")
            value = gen.parameters.get(p.get("name"))
            if t == "float" and is_float(value):
                rv["p_%s_%s" % (genname, p["name"])] = (float(value),)
            elif t == "color":
                c = value if isinstance(value, Color) else deserialize_value(value)
                rv["p_%s_%s" % (genname, p["name"])] = (c.r, c.g, c.b, c.a)
            elif t == "gradient":
                g = value if isinstance(value, Gradient) else Gradient.deserialize(value)
                g.sort()
                for i, point in enumerate(g.points):
                    rv["p_%s_%s_%d_pos" % (genname, p["name"], i)] = (point.v,)
                    rv["p_%s_%s_%d_col" % (genname, p["name"], i)] = (point.c.r, point.c.g, point.c.b, point.c.a)
            elif t == "curve":
                c = value if isinstance(value, Curve) else Curve.deserialize(value)
                for k, v in c.get_shader_params(genname + "_" + p["name"]).items():
                    rv[k] = (float(v),)
    elif isinstance(gen, GenRemote):
        for w in gen.widgets:
            if w.get("type") == "named_parameter":
                rv["o%d_%s" % (gen.uid, w["name"])] = (float(gen.get_parameter(w["name"])),)
    elif isinstance(gen, GenIterateBuffer):
        rv["o%d_tex_size" % gen.uid] = (float(gen.get_size()),)
        rv["o%d_iteration" % gen.uid] = (0.0,)  # the renderer sets it per iteration
    elif isinstance(gen, GenBuffer):
        rv["o%d_tex_size" % gen.uid] = (float(gen.get_size()),)
        if gen.version == GenBuffer.VERSION_OLD:
            rv["p_o%d_lod" % gen.uid] = (float(gen.get_parameter("lod")),)
    return rv


def uniform_values(project):
    """Current value of every uniform of every generator in the project."""
    rv = {}
    for gen in project.iter_generators():
        rv.update(generator_uniforms(gen))
    return rv


def needs_recompile(gen, name, old, new):
    """Port of the fast paths in MMGenBase.set_parameter: False when only uniforms change."""
    if old == new and type(old) is type(new):
        return False
    if isinstance(gen, (GenGraph, GenRemote)):
        # They only store the value; the change reaches the real target as its own record.
        return False
    if isinstance(gen, GenBuffer):
        return name != "lod"
    if isinstance(gen, GenText):
        return False  # only the text texture changes; the renderer redraws it from text_settings()
    if isinstance(gen, GenIterateBuffer):
        # Iteration count, shrink and autostop only change how the renderer loops.
        return name not in ("iterations", "shrink", "autostop")
    pdef = gen.get_parameter_def(name)
    t = pdef.get("type")
    if t == "float":
        return not (is_float(old) and is_float(new))
    if t == "color":
        return False
    if t == "gradient":
        return not (isinstance(old, Gradient) and isinstance(new, Gradient)
                    and old.interpolation == new.interpolation and len(old.points) == len(new.points))
    if t == "curve":
        return not (isinstance(old, Curve) and isinstance(new, Curve) and len(old.points) == len(new.points))
    return True
