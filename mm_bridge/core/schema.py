"""Editable parameters of a project, laid out like Material Maker's node inspector.

A schema is a list of sections (one per node, plus exposed controls first), each a
list of ParamSpec. The Blender side turns specs into properties; specs also convert
values between the Blender property and Material Maker's representation.
No bpy here.
"""

import hashlib
import re
from dataclasses import dataclass, field

from .generators import (
    GenComment,
    GenGraph,
    GenIOs,
    GenMaterial,
    GenRemote,
    GenShader,
    GenUnsupported,
)
from .mmtypes import Color, Curve, Gradient, deserialize_value, is_float

# Kinds of ParamSpec. "unsupported" ones are listed so the UI can say so.
# GRADIENT and CURVE have no Blender property: their editable state is a helper node
# (Color Ramp / Float Curve) in the material, see session.py.
FLOAT, EXPRESSION, ENUM, BOOL, SIZE, COLOR, GRADIENT, CURVE, STRING, FILE, SEED, UNSUPPORTED = (
    "float", "expression", "enum", "bool", "size", "color", "gradient", "curve", "string", "file", "seed",
    "unsupported")

SCHEMA_VERSION = 2


@dataclass
class ParamSpec:
    key: str                 # property identifier, unique in the schema ("p12")
    gen_path: str            # generator path from the root graph ("normal_map_0", "graph_3/perlin_0")
    name: str                # Material Maker parameter name ("" for seeds)
    label: str
    kind: str
    value: object            # current value, in the Blender property's representation
    description: str = ""
    soft_min: float = None
    soft_max: float = None
    step: float = None
    items: list = field(default_factory=list)   # enum/size: [(identifier, label)]
    note: str = ""           # why a parameter is unsupported


@dataclass
class Section:
    key: str
    label: str
    gen_path: str
    kind: str                # "controls", "material", "node"
    depth: int = 0
    specs: list = field(default_factory=list)


def _clean_label(p):
    label = p.get("label", "") or ""
    if not label or re.fullmatch(r"\s*\d+:\s*", label):
        label = p.get("shortdesc", "") or p.get("name", "")
    label = re.sub(r"^\s*\d+:\s*", "", label).strip() or p.get("name", "")
    if label == p.get("name"):
        label = label.replace("_", " ").capitalize()  # nodes without labels (text, iterate_buffer, ...)
    return label


def node_label(gen):
    if isinstance(gen, GenShader) and gen.shader_model.get("name"):
        title = gen.shader_model["name"]
    elif isinstance(gen, GenGraph):
        title = gen.label if gen.model is None else (gen.label or gen.model)
    else:
        title = gen.get_type().capitalize()
    return title if title == gen.name else "%s (%s)" % (title, gen.name)


def _param_specs(gen, path, next_key):
    specs = []
    for p in gen.get_parameter_defs():
        if "name" not in p or "type" not in p:
            continue
        t = p["type"]
        name = p["name"]
        value = gen.get_parameter(name)
        common = dict(gen_path=path, name=name, label=_clean_label(p), description=p.get("longdesc", "") or "")
        if t == "float":
            if is_float(value):
                specs.append(ParamSpec(next_key(), kind=FLOAT, value=float(value), soft_min=p.get("min"),
                                       soft_max=p.get("max"), step=p.get("step"), **common))
            else:
                specs.append(ParamSpec(next_key(), kind=EXPRESSION, value=str(value), **common))
        elif t == "enum":
            values = p.get("values", [])
            if not values:
                continue
            index = int(value) if is_float(value) and 0 <= value < len(values) else 0
            specs.append(ParamSpec(next_key(), kind=ENUM, value=str(index),
                                   items=[(str(i), v["name"]) for i, v in enumerate(values)], **common))
        elif t == "boolean":
            specs.append(ParamSpec(next_key(), kind=BOOL, value=bool(value), **common))
        elif t == "size":
            first, last = int(p.get("first", 4)), int(p.get("last", 13))
            exponent = int(value) if is_float(value) else first
            specs.append(ParamSpec(next_key(), kind=SIZE, value=str(min(max(exponent, first), last)),
                                   items=[(str(e), str(2 ** e)) for e in range(first, last + 1)], **common))
        elif t == "color":
            c = value if isinstance(value, Color) else deserialize_value(value)
            specs.append(ParamSpec(next_key(), kind=COLOR, value=(c.r, c.g, c.b, c.a), **common))
        elif t == "gradient":
            g = value if isinstance(value, Gradient) else Gradient.deserialize(value)
            g.sort()
            specs.append(ParamSpec(next_key(), kind=GRADIENT, value=g, **common))
        elif t == "curve":
            c = value if isinstance(value, Curve) else Curve.deserialize(value)
            specs.append(ParamSpec(next_key(), kind=CURVE, value=c, **common))
        elif t == "string":
            specs.append(ParamSpec(next_key(), kind=STRING, value=str(value or ""), **common))
        elif t in ("file", "image_path"):
            specs.append(ParamSpec(next_key(), kind=FILE, value=str(value or ""), **common))
        else:
            specs.append(ParamSpec(next_key(), kind=UNSUPPORTED, value="", note="%s parameters are not editable yet" % t,
                                   **common))
    return specs


def _seed_spec(gen, path, next_key):
    if gen.has_randomness():
        return [ParamSpec(next_key(), gen_path=path, name="", label="Seed", kind=SEED, value=float(gen.seed_value),
                          description="Random seed of this node")]
    return []


def _skip(gen):
    return isinstance(gen, (GenIOs, GenComment, GenUnsupported)) or (
        isinstance(gen, GenRemote) and gen.name == "gen_parameters")


def stable_key(gen_path, name, kind):
    """Property identifier that survives graph edits elsewhere (values, keyframes, helper nodes)."""
    return "k" + hashlib.sha1(("%s|%s|%s" % (gen_path, name, kind)).encode("utf-8")).hexdigest()[:12]


def build_schema(project):
    """Sections of editable parameters, in display order."""
    def next_key():
        return None  # replaced by stable keys below

    sections = []

    def add_graph(graph, prefix, depth):
        remotes = [c for c in graph.children.values() if isinstance(c, GenRemote) and c.name != "gen_parameters"]
        for remote in remotes:
            path = prefix + remote.name
            specs = _param_specs(remote, path, next_key)
            if specs:
                sections.append(Section("s%d" % len(sections), "Controls" if depth == 0 else node_label(remote),
                                        path, "controls", depth, specs))
        for child in graph.children.values():
            if _skip(child) or child in remotes:
                continue
            path = prefix + child.name
            if isinstance(child, GenMaterial):
                if depth == 0:
                    sections.append(Section("s%d" % len(sections), "Material", path, "material", depth,
                                            _param_specs(child, path, next_key)))
                continue
            specs = _param_specs(child, path, next_key) + _seed_spec(child, path, next_key)
            is_user_graph = isinstance(child, GenGraph) and child.model is None
            if specs or is_user_graph:
                sections.append(Section("s%d" % len(sections), node_label(child), path, "node", depth, specs))
            if is_user_graph:
                add_graph(child, path + "/", depth + 1)

    add_graph(project.root, "", 0)
    seen = set()
    for section in sections:
        for spec in section.specs:
            spec.key = stable_key(spec.gen_path, spec.name, spec.kind)
            if spec.key in seen:  # practically impossible; keep keys unique regardless
                spec.key += "_%d" % len(seen)
            seen.add(spec.key)
    return sections


def resolve(project, gen_path):
    gen = project.root
    for part in gen_path.split("/"):
        gen = gen.get_node(part) if isinstance(gen, GenGraph) else None
        if gen is None:
            return None
    return gen


def to_mm_value(spec, value):
    """Blender property value -> Material Maker parameter value (gradients handled separately)."""
    if spec.kind == FLOAT:
        return float(value)
    if spec.kind in (ENUM, SIZE):
        return float(int(value))
    if spec.kind == BOOL:
        return bool(value)
    if spec.kind == COLOR:
        return Color(*[float(v) for v in value])
    if spec.kind in (STRING, FILE):
        return str(value)
    if spec.kind == EXPRESSION:
        s = str(value).strip()
        try:
            return float(s)
        except ValueError:
            return s
    raise ValueError("No direct conversion for %s" % spec.kind)


def schema_attr(uid):
    """One property group per material; its layout is re-registered when the graph changes."""
    return "mmb_" + uid[:12]


def schema_signature(sections):
    """Changes whenever the generated property layout would change."""
    parts = [str(SCHEMA_VERSION)]
    for s in sections:
        for spec in s.specs:
            parts.append("%s|%s|%s|%s|%s" % (spec.key, spec.kind, spec.gen_path, spec.name, spec.items))
            if spec.kind in (GRADIENT, CURVE):
                parts.append(repr(spec.value.serialize()))  # helper nodes are initialized from it
    return "\n".join(parts)
