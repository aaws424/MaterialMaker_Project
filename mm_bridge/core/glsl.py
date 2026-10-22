"""Turn Material Maker generated code (Godot shading language flavour) into plain
GLSL fragment code for Blender's `gpu` module.

Every `uniform` with a numeric type becomes a slot in a single `vec4 mm_p[N]`
push-constant array, and its original name is kept with a `#define`, so
parameter changes only need new push-constant values, never a recompile.
Samplers become create-info sampler bindings.

Inputs the Blender side must provide:
    push constant  vec4 mm_p[param_slots]   parameter values (see `BlenderShader.pack_params`)
    push constant  vec4 mm_frame            x: time, y: seed variation, z: chunk size,
                                            w: 1 to flip V (passes read back into Blender images,
                                               whose first row is the bottom), 0 for buffers that
                                               other passes sample with Material Maker UVs
    push constant  vec2 mm_chunk_offset
    stage input    vec2 mm_uv               Material Maker UV (0,0 = top left of the texture)
    sampler2D      one per name in `samplers`
    fragment out   vec4 mm_out
"""

import re
from dataclasses import dataclass, field

from .textutil import find_matching_parenthesis

_UNIFORM_RE = re.compile(
    r"\buniform\s+(?:(?:lowp|mediump|highp)\s+)?(\w+)\s+(\w+)\s*(\[\s*\d*\s*\])?"
    r"\s*(?::\s*\w+(?:\s*\([^)]*\))?(?:\s*,\s*\w+(?:\s*\([^)]*\))?)*)?"
    r"\s*(?:=\s*([^;]*))?;")
_VARYING_RE = re.compile(r"\bvarying\s+[^;]*;")
_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")

_COMPONENTS = {"float": 1, "int": 1, "bool": 1, "vec2": 2, "vec3": 3, "vec4": 4}
_SWIZZLE = "xyzw"

VERTEX_SOURCE = """void main() {
    mm_uv = vec2(pos.x*0.5+0.5, 0.5+pos.y*0.5*(1.0-2.0*mm_frame.w));
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""


@dataclass
class Param:
    name: str
    glsl_type: str
    slot: int
    component: int
    default: tuple

    @property
    def size(self):
        return _COMPONENTS[self.glsl_type]


@dataclass
class BlenderShader:
    fragment_source: str
    params: list
    param_slots: int
    samplers: list
    output_type: str
    constants: list = field(default_factory=list)
    mip_atlases: dict = field(default_factory=dict)  # buffer sampler -> its mip atlas sampler
    vertex_source: str = VERTEX_SOURCE

    def param_index(self):
        return {p.name: p for p in self.params}

    def pack_params(self, overrides=None):
        """Flat list of 4*param_slots floats; `overrides` maps uniform name -> value(s)."""
        values = [0.0] * (4 * self.param_slots)
        overrides = overrides or {}
        for p in self.params:
            v = overrides.get(p.name, p.default)
            if isinstance(v, (int, float)):
                v = (float(v),)
            for i in range(p.size):
                values[4 * p.slot + p.component + i] = float(v[i]) if i < len(v) else 0.0
        return values


def _parse_default(glsl_type, expr, warnings, name):
    n = _COMPONENTS[glsl_type]
    if not expr:
        return (0.0,) * n
    if glsl_type == "bool":
        return (1.0 if expr.strip() == "true" else 0.0,)
    numbers = [float(x) for x in _NUMBER_RE.findall(expr)]
    if len(numbers) == 1 and n > 1:
        numbers = numbers * n
    if len(numbers) != n:
        warnings.append("Cannot parse default value %r of uniform %s" % (expr, name))
        numbers = (numbers + [0.0] * n)[:n]
    return tuple(numbers)


def _allocate(params, glsl_type, scalar_state):
    """Pack scalars four to a slot; vectors take a slot of their own."""
    n = _COMPONENTS[glsl_type]
    if n == 1:
        if scalar_state["slot"] is None or scalar_state["used"] == 4:
            scalar_state["slot"] = scalar_state["next"]
            scalar_state["next"] += 1
            scalar_state["used"] = 0
        slot, component = scalar_state["slot"], scalar_state["used"]
        scalar_state["used"] += 1
        return slot, component
    slot = scalar_state["next"]
    scalar_state["next"] += 1
    return slot, 0


def _param_macro(p):
    ref = "mm_p[%d]" % p.slot
    if p.size == 4:
        expr = ref
    else:
        expr = ref + "." + _SWIZZLE[p.component:p.component + p.size]
    if p.glsl_type == "int":
        expr = "int(%s)" % expr
    elif p.glsl_type == "bool":
        expr = "(%s != 0.0)" % expr
    return "#define %s %s" % (p.name, expr)


def _split_arguments(args):
    parts, depth, start = [], 0, 0
    for i, c in enumerate(args):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(args[start:i])
            start = i + 1
    parts.append(args[start:])
    return parts


def _is_zero_literal(expr):
    return re.fullmatch(r"\s*\(?\s*0*\.?0*\s*\)?\s*", expr) is not None and re.search(r"\d", expr) is not None


def _rewrite_lod_lookups(text, sampler):
    """Replace textureLod(sampler, uv, lod) with a mip-atlas lookup when lod is not literally 0.

    Returns None when no call needs the atlas.
    """
    pattern = re.compile(r"\btextureLod\s*\(\s*%s\s*," % re.escape(sampler))
    out, pos, changed = [], 0, False
    while True:
        m = pattern.search(text, pos)
        if m is None:
            out.append(text[pos:])
            break
        open_paren = text.index("(", m.start())
        close_paren = find_matching_parenthesis(text, open_paren)
        if close_paren < 0:
            out.append(text[pos:])
            break
        args = _split_arguments(text[open_paren + 1:close_paren])
        out.append(text[pos:m.start()])
        if len(args) == 3 and not _is_zero_literal(args[2]):
            out.append("mm_mip_%s(%s, %s)" % (sampler, args[1].strip(), args[2].strip()))
            changed = True
        else:
            out.append(text[m.start():close_paren + 1])
        pos = close_paren + 1
    return "".join(out) if changed else None


def _mip_lookup_functions(sampler, mip_filter=True):
    """Trilinear (or nearest) lookup with REPEAT wrapping in a mip atlas.

    Atlas layout for a base size N: level 0 at (0, 0), level k >= 1 at (N, N - N/2^(k-1)),
    so the atlas is 1.5N x N. Rows follow the base texture (row 0 = Material Maker uv.y 0).
    """
    s = sampler
    level = (
        "vec4 mm_mip_level_%(s)s(vec2 p, int level) {\n"
        "\tint n = int(%(s)s_size);\n"
        "\tint size = max(n >> level, 1);\n"
        "\tivec2 origin = level == 0 ? ivec2(0) : ivec2(n, n - (n >> (level-1)));\n"
        "\tvec2 t = p*float(size) - 0.5;\n"
        "\tivec2 i0 = ivec2(floor(t));\n"
        "\tvec2 f = t - floor(t);\n"
        "\tivec2 a = ((i0 %% size) + size) %% size;\n"
        "\tivec2 b = ((i0 + 1) %% size + size) %% size;\n"
    ) % {"s": s}
    if mip_filter:
        level += (
            "\tvec4 c00 = texelFetch(%(m)s, origin + ivec2(a.x, a.y), 0);\n"
            "\tvec4 c10 = texelFetch(%(m)s, origin + ivec2(b.x, a.y), 0);\n"
            "\tvec4 c01 = texelFetch(%(m)s, origin + ivec2(a.x, b.y), 0);\n"
            "\tvec4 c11 = texelFetch(%(m)s, origin + ivec2(b.x, b.y), 0);\n"
            "\treturn mix(mix(c00, c10, f.x), mix(c01, c11, f.x), f.y);\n}\n"
        ) % {"m": s + "_mips"}
    else:
        level += "\tivec2 i = ((ivec2(floor(p*float(size))) %% size) + size) %% size;\n" \
                 "\treturn texelFetch(%s, origin + i, 0);\n}\n" % (s + "_mips")
    lookup = (
        "vec4 mm_mip_%(s)s(vec2 p, float lod) {\n"
        "\tfloat max_level = log2(%(s)s_size);\n"
        "\tlod = clamp(lod, 0.0, max_level);\n"
        "\tint l0 = int(floor(lod));\n"
        "\tint l1 = min(l0 + 1, int(max_level));\n"
        "\treturn mix(mm_mip_level_%(s)s(p, l0), mm_mip_level_%(s)s(p, l1), lod - floor(lod));\n}\n"
    ) % {"s": s}
    return "\n" + level + lookup


def to_blender_glsl(source, common_glsl, mip_samplers=None, output_transform=None):
    """Build a BlenderShader from a compiled `ShaderSource` (see compiler.py).

    mip_samplers: {sampler name: use linear filtering} for mipmapped buffers; textureLod
        calls on them with a non-zero lod are redirected to a mip atlas named "<sampler>_mips".
    output_transform: None, or "srgb_to_linear" for color channels (albedo, emission).
    """
    mip_samplers = mip_samplers or {}
    warnings = []
    declarations = "\n".join(source.globals) + "\n" + source.defs
    params, samplers, constants, seen = [], [], [], set()
    scalar_state = {"slot": None, "used": 0, "next": 0}

    def replace_uniform(m):
        glsl_type, name, array, default = m.group(1), m.group(2), m.group(3), m.group(4)
        if name in seen:
            return ""
        seen.add(name)
        if glsl_type == "sampler2D" and not array:
            samplers.append(name)
            return ""
        if glsl_type in _COMPONENTS and not array:
            slot, component = _allocate(params, glsl_type, scalar_state)
            params.append(Param(name, glsl_type, slot, component,
                                _parse_default(glsl_type, default, warnings, name)))
            return ""
        # Anything else is frozen to its default value.
        constants.append(name)
        warnings.append("Uniform %s %s%s is not tweakable; using it as a constant" % (glsl_type, name, array or ""))
        return "const %s %s%s = %s;" % (glsl_type, name, array or "", default or "%s(0)" % glsl_type)

    declarations = _UNIFORM_RE.sub(replace_uniform, declarations)
    if _VARYING_RE.search(declarations):
        warnings.append("Removed Godot varying declarations")
        declarations = _VARYING_RE.sub("", declarations)
    code = _UNIFORM_RE.sub(replace_uniform, source.code)

    mip_atlases = {}
    mip_functions = ""  # placed before all node code, which may call them from its own functions
    for name in [s for s in samplers if s in mip_samplers]:
        rewritten = _rewrite_lod_lookups(declarations + "\n@@MM_SPLIT@@\n" + code, name)
        if rewritten is not None:
            declarations, code = rewritten.split("\n@@MM_SPLIT@@\n")
            mip_atlases[name] = name + "_mips"
            mip_functions += _mip_lookup_functions(name, mip_filter=mip_samplers[name])
    samplers += list(mip_atlases.values())

    macros = ["#define elapsed_time mm_frame.x"]
    if re.search(r"\bTIME\b", declarations + code + source.rgba):
        macros.append("#define TIME mm_frame.x")
    macros += [_param_macro(p) for p in params]

    result = source.rgba or "vec4(1.0, 0.0, 0.0, 1.0)"
    if output_transform == "srgb_to_linear":
        main_end = ["vec4 mm_c = %s;" % result,
                    "mm_out = vec4(mix(pow((mm_c.rgb+vec3(0.055))*(1.0/1.055), vec3(2.4)), mm_c.rgb*(1.0/12.92),"
                    " lessThan(mm_c.rgb, vec3(0.04045))), mm_c.a);"]
    elif output_transform is None:
        main_end = ["mm_out = %s;" % result]
    else:
        raise ValueError("Unknown output transform %r" % output_transform)

    fragment = "\n".join([
        "// Generated by MM Bridge from a Material Maker graph",
        "\n".join(macros),
        common_glsl,
        mip_functions,
        declarations,
        "void main() {",
        "float _seed_variation_ = mm_frame.y;",
        "vec2 uv = mm_chunk_offset+mm_frame.z*mm_uv;",
        code,
    ] + main_end + ["}", ""])
    shader = BlenderShader(
        fragment_source=fragment,
        params=params,
        param_slots=max(1, scalar_state["next"]),
        samplers=samplers,
        output_type=source.output_type,
        constants=constants,
        mip_atlases=mip_atlases,
    )
    source.warnings.extend(warnings)
    return shader
