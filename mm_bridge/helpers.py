"""Helper nodes that give gradient and curve parameters Blender's own editors.

Blender only draws a color ramp or curve widget for data owned by a datablock, so each
Material Maker gradient gets a Color Ramp node and each curve a Float Curve node inside
the material's node tree (in a frame away from the shading nodes). These nodes are the
saved, undoable state of those parameters; session.py watches them for edits.

Material Maker colors are sRGB-encoded values while Color Ramp colors are scene linear,
so colors are converted both ways.
"""

from .core.mmtypes import Color, Curve, Gradient

RAMP_PREFIX = "MMB_RAMP "
CURVE_PREFIX = "MMB_CURVE "
FRAME_NAME = "MMB_PARAMS"
WRITTEN_SIGNATURE = "mmb_written"   # node property: signature of what the add-on last wrote

INTERPOLATION_TO_BLENDER = {0: "CONSTANT", 1: "LINEAR", 2: "EASE", 3: "CARDINAL"}
INTERPOLATION_FROM_BLENDER = {"CONSTANT": 0, "LINEAR": 1, "EASE": 2, "CARDINAL": 3, "B_SPLINE": 3}


def srgb_to_linear(c):
    return c / 12.92 if c < 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c):
    if c < 0.0031308:
        return max(c, 0.0) * 12.92
    return 1.055 * c ** (1.0 / 2.4) - 0.055


def _frame(tree):
    frame = tree.nodes.get(FRAME_NAME)
    if frame is None:
        frame = tree.nodes.new("NodeFrame")
        frame.name = FRAME_NAME
        frame.label = "Material Maker parameters (edit them in the Material Maker panel)"
        frame.location = (-1300, -900)
    return frame


def find(material, key):
    tree = material.node_tree
    if tree is None:
        return None
    return tree.nodes.get(RAMP_PREFIX + key) or tree.nodes.get(CURVE_PREFIX + key)


def create(material, key, kind, label):
    tree = material.node_tree
    frame = _frame(tree)
    count = sum(1 for n in tree.nodes if n.parent == frame)
    if kind == "gradient":
        node = tree.nodes.new("ShaderNodeValToRGB")
        node.name = RAMP_PREFIX + key
    else:
        node = tree.nodes.new("ShaderNodeFloatCurve")
        node.name = CURVE_PREFIX + key
    node.label = label
    node.parent = frame
    node.location = (frame.location.x + 300 * (count % 4), frame.location.y - 300 * (count // 4))
    node.hide = True
    return node


def remove_all(material):
    tree = material.node_tree
    if tree is None:
        return
    for n in [n for n in tree.nodes if n.name.startswith((RAMP_PREFIX, CURVE_PREFIX)) or n.name == FRAME_NAME]:
        tree.nodes.remove(n)


# Gradients <-> Color Ramp

def write_gradient(node, gradient):
    ramp = node.color_ramp
    gradient.sort()
    ramp.color_mode = "RGB"
    ramp.interpolation = INTERPOLATION_TO_BLENDER.get(gradient.interpolation, "LINEAR")
    elements = ramp.elements
    while len(elements) > 1:
        elements.remove(elements[-1])

    def color(p):
        return (srgb_to_linear(p.c.r), srgb_to_linear(p.c.g), srgb_to_linear(p.c.b), p.c.a)

    first = gradient.points[0] if gradient.points else None
    if first is not None:
        elements[0].position = min(max(first.v, 0.0), 1.0)
        elements[0].color = color(first)
    for p in gradient.points[1:]:
        e = elements.new(min(max(p.v, 0.0), 1.0))
        e.color = color(p)
    node[WRITTEN_SIGNATURE] = repr(gradient_signature(node))


def read_gradient(node):
    ramp = node.color_ramp
    g = Gradient(points=[])
    g.interpolation = INTERPOLATION_FROM_BLENDER.get(ramp.interpolation, 1)
    for e in sorted(ramp.elements, key=lambda e: e.position):
        r, gr, b, a = e.color
        g.add_point(e.position, Color(linear_to_srgb(r), linear_to_srgb(gr), linear_to_srgb(b), a))
    g.sort()
    return g


def gradient_signature(node):
    ramp = node.color_ramp
    return (ramp.interpolation,) + tuple(
        (round(e.position, 6),) + tuple(round(c, 6) for c in e.color) for e in ramp.elements)


# Curves <-> Float Curve

def _curve(node):
    return node.mapping.curves[0]


def write_curve(node, curve):
    mapping = node.mapping
    points = _curve(node).points
    target = curve.points
    while len(points) > max(2, len(target)):
        points.remove(points[-1])
    while len(points) < len(target):
        points.new(0.5, 0.5)
    for point, p in zip(points, target):
        point.location = (p.x, p.y)
        point.handle_type = "AUTO"
    mapping.update()
    node[WRITTEN_SIGNATURE] = repr(curve_signature(node))


def read_curve(node):
    """Points from the widget; Material Maker tangents (ls/rs) from the curve's slope at each point."""
    mapping = node.mapping
    mapping.initialize()
    curve = _curve(node)
    pts = sorted(((p.location[0], p.location[1]) for p in curve.points), key=lambda p: p[0])
    rv = Curve(points=[])
    h = 1e-3
    for i, (x, y) in enumerate(pts):
        x0 = max(x - h, pts[0][0]) if i > 0 else x
        x1 = min(x + h, pts[-1][0]) if i < len(pts) - 1 else x
        slope = 0.0
        if x1 > x0:
            slope = (mapping.evaluate(curve, x1) - mapping.evaluate(curve, x0)) / (x1 - x0)
        rv.add_point(x, y, slope, slope)
    return rv


def curve_signature(node):
    return tuple((round(p.location[0], 6), round(p.location[1], 6), p.handle_type) for p in _curve(node).points)


def signature(node):
    return gradient_signature(node) if node.bl_idname == "ShaderNodeValToRGB" else curve_signature(node)


def is_unchanged_since_written(node):
    return node.get(WRITTEN_SIGNATURE) == repr(signature(node))


def read(node):
    return read_gradient(node) if node.bl_idname == "ShaderNodeValToRGB" else read_curve(node)


def write(node, value):
    if isinstance(value, Gradient):
        write_gradient(node, value)
    else:
        write_curve(node, value)
