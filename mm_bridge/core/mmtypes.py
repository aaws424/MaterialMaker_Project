"""Parameter value types (port of addons/material_maker/types/*.gd)."""

import math
from dataclasses import dataclass, field


@dataclass
class Color:
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    a: float = 1.0

    def serialize(self):
        return {"type": "Color", "r": self.r, "g": self.g, "b": self.b, "a": self.a}


@dataclass
class GradientPoint:
    v: float
    c: Color


@dataclass
class Gradient:
    points: list = field(default_factory=list)
    interpolation: int = 1
    sorted: bool = True

    def add_point(self, v, c):
        self.points.append(GradientPoint(float(v), c))
        self.sorted = False

    def sort(self):
        if not self.sorted:
            self.points.sort(key=lambda p: p.v)
            for i in range(len(self.points) - 1):
                if self.points[i].v + 0.0000005 >= self.points[i + 1].v:
                    self.points[i + 1].v = self.points[i].v + 0.000001
            self.sorted = True

    def get_shader_params(self, name, attribute="uniform"):
        self.sort()
        rv = ""
        for i, p in enumerate(self.points):
            rv += "%s float p_%s_%d_pos = %.09f;\n" % (attribute, name, i, p.v)
            rv += "%s vec4 p_%s_%d_col = vec4(%.09f, %.09f, %.09f, %.09f);\n" % (
                attribute, name, i, p.c.r, p.c.g, p.c.b, p.c.a)
        return rv

    @staticmethod
    def _pv(name, i):
        return "p_%s_%d_pos" % (name, i)

    @staticmethod
    def _pc(name, i):
        return "p_%s_%d_col" % (name, i)

    def get_shader(self, name):
        self.sort()
        pv, pc = self._pv, self._pc
        points = self.points
        shader = "vec4 " + name + "_gradient_fct(float x) {\n"
        if not points:
            shader += "  return vec4(0.0, 0.0, 0.0, 1.0);\n"
        elif self.interpolation == 0:
            s = len(points) - 1
            if s >= 1:
                shader += "  if (x < 0.5*(%s+%s)) {\n" % (pv(name, 0), pv(name, 1))
            else:
                # MM indexes point 1 unconditionally here; keep valid GLSL for 1-point gradients.
                shader += "  if (x < %s) {\n" % pv(name, 0)
            shader += "    return " + pc(name, 0) + ";\n"
            for i in range(1, s):
                shader += "  } else if (x < 0.5*(%s+%s)) {\n" % (pv(name, i), pv(name, i + 1))
                shader += "    return " + pc(name, i) + ";\n"
            shader += "  }\n"
            shader += "  return " + pc(name, s) + ";\n"
        elif self.interpolation in (1, 2):
            shader += "  if (x < %s) {\n" % pv(name, 0)
            shader += "    return " + pc(name, 0) + ";\n"
            s = len(points) - 1
            function = "(" if self.interpolation == 1 else "0.5-0.5*cos(3.14159265359*"
            for i in range(s):
                shader += "  } else if (x < %s) {\n" % pv(name, i + 1)
                shader += "    return mix(%s, %s, %s(x-%s)/(%s-%s)));\n" % (
                    pc(name, i), pc(name, i + 1), function, pv(name, i), pv(name, i + 1), pv(name, i))
            shader += "  }\n"
            shader += "  return " + pc(name, s) + ";\n"
        elif self.interpolation == 3:
            shader += "  if (x < %s) {\n" % pv(name, 0)
            shader += "    return " + pc(name, 0) + ";\n"
            s = len(points) - 1
            for i in range(s):
                shader += "  } else if (x < %s) {\n" % pv(name, i + 1)
                dx = "(x-%s)/(%s-%s)" % (pv(name, i), pv(name, i + 1), pv(name, i))
                b = "mix(%s, %s, %s)" % (pc(name, i), pc(name, i + 1), dx)
                if i > 0:
                    a = "mix(%s, %s, (x-%s)/(%s-%s))" % (
                        pc(name, i - 1), pc(name, i), pv(name, i - 1), pv(name, i), pv(name, i - 1))
                    if i < s - 1:
                        c = "mix(%s, %s, (x-%s)/(%s-%s))" % (
                            pc(name, i + 1), pc(name, i + 2), pv(name, i + 1), pv(name, i + 2), pv(name, i + 1))
                        ac = "mix(" + a + ", " + c + ", 0.5-0.5*cos(3.14159265359*" + dx + "))"
                        shader += "    return 0.5*(" + b + " + " + ac + ");\n"
                    else:
                        shader += "    return mix(" + a + ", " + b + ", 0.5+0.5*" + dx + ");\n"
                elif i < s - 1:
                    c = "mix(%s, %s, (x-%s)/(%s-%s))" % (
                        pc(name, i + 1), pc(name, i + 2), pv(name, i + 1), pv(name, i + 2), pv(name, i + 1))
                    shader += "    return mix(" + c + ", " + b + ", 1.0-0.5*" + dx + ");\n"
                else:
                    shader += "    return " + b + ";\n"
            shader += "  }\n"
            shader += "  return " + pc(name, s) + ";\n"
        else:
            shader += "  return vec4(0.0, 0.0, 0.0, 1.0);\n"
        shader += "}\n"
        return shader

    def serialize(self):
        self.sort()
        return {
            "type": "Gradient",
            "interpolation": self.interpolation,
            "points": [{"pos": p.v, "r": p.c.r, "g": p.c.g, "b": p.c.b, "a": p.c.a} for p in self.points],
        }

    @classmethod
    def deserialize(cls, v):
        g = cls(points=[])
        if isinstance(v, list):
            points, g.interpolation = v, 1
        elif isinstance(v, dict) and v.get("type") == "Gradient":
            points = v.get("points", [])
            g.interpolation = int(v.get("interpolation", 1))
        elif isinstance(v, Gradient):
            points = v.serialize()["points"]
            g.interpolation = v.interpolation
        else:
            raise ValueError("Cannot deserialize gradient %r" % (v,))
        for p in points:
            g.add_point(p["pos"], Color(p["r"], p["g"], p["b"], p.get("a", 1.0)))
        return g


@dataclass
class CurvePoint:
    x: float
    y: float
    ls: float
    rs: float


@dataclass
class Curve:
    points: list = field(default_factory=list)

    def add_point(self, x, y, ls=math.inf, rs=math.inf):
        for i, p in enumerate(self.points):
            if x < p.x:
                if ls == math.inf:
                    ls = 0.0
                if rs == math.inf:
                    rs = 0.0
                self.points.insert(i, CurvePoint(x, y, ls, rs))
                return
        self.points.append(CurvePoint(x, y, ls, rs))

    def get_shader_params(self, name):
        rv = {}
        for i, p in enumerate(self.points):
            rv["p_%s_%d_x" % (name, i)] = p.x
            rv["p_%s_%d_y" % (name, i)] = p.y
            rv["p_%s_%d_ls" % (name, i)] = p.ls
            rv["p_%s_%d_rs" % (name, i)] = p.rs
        return rv

    def get_shader(self, name):
        shader = "float " + name + "_curve_fct(float x) {\n"
        n = len(self.points)
        for i in range(n - 1):
            pi = "p_%s_%d" % (name, i)
            pn = "p_%s_%d" % (name, i + 1)
            if i < n - 2:
                shader += "if (x <= " + pn + "_x) "
            shader += "{\n"
            shader += "float dx = x - " + pi + "_x;\n"
            shader += "float d = " + pn + "_x - " + pi + "_x;\n"
            shader += "float t = dx/d;\n"
            shader += "float omt = (1.0 - t);\n"
            shader += "float omt2 = omt * omt;\n"
            shader += "float omt3 = omt2 * omt;\n"
            shader += "float t2 = t * t;\n"
            shader += "float t3 = t2 * t;\n"
            shader += "d /= 3.0;\n"
            shader += "float y1 = " + pi + "_y;\n"
            shader += "float yac = " + pi + "_y + d*" + pi + "_rs;\n"
            shader += "float ybc = " + pn + "_y - d*" + pn + "_ls;\n"
            shader += "float y2 = " + pn + "_y;\n"
            shader += "return y1*omt3 + yac*omt2*t*3.0 + ybc*omt*t2*3.0 + y2*t3;\n"
            shader += "}\n"
        # GLSL requires a return on every path (Godot is lenient).
        shader += "return 0.0;\n" if n < 2 else ""
        shader += "}\n"
        return shader

    def serialize(self):
        return {"type": "Curve", "points": [{"x": p.x, "y": p.y, "ls": p.ls, "rs": p.rs} for p in self.points]}

    @classmethod
    def deserialize(cls, v):
        c = cls(points=[])
        if isinstance(v, Curve):
            v = v.serialize()
        if not (isinstance(v, dict) and v.get("type") == "Curve"):
            raise ValueError("Cannot deserialize curve %r" % (v,))
        for p in v["points"]:
            c.add_point(p["x"], p["y"], p["ls"], p["rs"])
        return c


@dataclass
class Polygon:
    points: list = field(default_factory=lambda: [(0.2, 0.2), (0.7, 0.4), (0.4, 0.7)])

    def get_shader(self):
        return "{" + ", ".join("vec2(%.9f, %.9f)" % p for p in self.points) + "}"

    def serialize(self):
        return {"type": "Polygon", "points": [{"x": x, "y": y} for x, y in self.points]}

    @classmethod
    def deserialize(cls, v):
        if isinstance(v, Polygon):
            return cls(points=list(v.points))
        if isinstance(v, dict) and v.get("type") == "Polygon":
            return cls(points=[(p["x"], p["y"]) for p in v["points"]])
        raise ValueError("Cannot deserialize polygon %r" % (v,))


def serialize_value(value):
    if hasattr(value, "serialize"):
        return value.serialize()
    return value


def deserialize_value(data):
    """Port of MMType.deserialize_value."""
    if isinstance(data, dict):
        t = data.get("type")
        if t is not None:
            if t == "Color":
                return Color(data["r"], data["g"], data["b"], data["a"])
            if t == "Gradient":
                return Gradient.deserialize(data)
            if t == "Curve":
                return Curve.deserialize(data)
        elif all(k in data for k in "rgba"):
            return Color(data["r"], data["g"], data["b"], data["a"])
    elif isinstance(data, list):
        return Gradient.deserialize(data)
    return data


def is_float(v):
    """Godot parses every JSON number as float; booleans are not floats."""
    return isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool))
