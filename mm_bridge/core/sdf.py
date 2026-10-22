"""EasySDF: port of Material Maker 1.3's SDF builder (addons/material_maker/sdf_builder).

A scene is a list of items ({type, index, parameters, children, hidden, parmexprs});
the builder turns it into GLSL code that MMGenSDF wraps into a shader model.
Class structure and output strings follow the GDScript items one to one.
"""

from .mmtypes import Color, deserialize_value

COMMON_2D = [
    {"label": "Position.x", "name": "position_x", "type": "float", "min": -1.0, "max": 1.0, "step": 0.01, "default": 0.0},
    {"label": "Position.y", "name": "position_y", "type": "float", "min": -1.0, "max": 1.0, "step": 0.01, "default": 0.0},
    {"label": "Rotation", "name": "angle", "type": "float", "min": -180.0, "max": 180.0, "step": 0.01, "default": 0.0},
    {"label": "Scale", "name": "scale", "type": "float", "min": -1.0, "max": 1.0, "step": 0.01, "default": 1.0},
]
COMMON_3D = [
    {"label": "Position.x", "name": "position_x", "type": "float", "min": -1.0, "max": 1.0, "step": 0.01, "default": 0.0},
    {"label": "Position.y", "name": "position_y", "type": "float", "min": -1.0, "max": 1.0, "step": 0.01, "default": 0.0},
    {"label": "Position.z", "name": "position_z", "type": "float", "min": -1.0, "max": 1.0, "step": 0.01, "default": 0.0},
    {"label": "Rotation.x", "name": "angle_x", "type": "float", "min": -180.0, "max": 180.0, "step": 0.01, "default": 0.0},
    {"label": "Rotation.y", "name": "angle_y", "type": "float", "min": -180.0, "max": 180.0, "step": 0.01, "default": 0.0},
    {"label": "Rotation.z", "name": "angle_z", "type": "float", "min": -180.0, "max": 180.0, "step": 0.01, "default": 0.0},
    {"label": "Scale", "name": "scale", "type": "float", "min": -1.0, "max": 1.0, "step": 0.01, "default": 1.0},
]


def _f(name, default, lo=0.0, hi=1.0, step=0.01, label=None):
    return {"label": label or name, "name": name, "type": "float", "min": lo, "max": hi, "step": step, "default": default}


def _b(name, default, label=None):
    return {"label": label or name, "name": name, "type": "boolean", "default": default}


def _enum(name, values, default=0, label=None):
    return {"label": label or name, "name": name, "type": "enum", "default": default,
            "values": [{"name": n, "value": v} for n, v in values]}


def _color(name, default, label=None):
    return {"label": label or name, "name": name, "type": "color", "default": default}


def _params(scene):
    return scene.get("parameters", {}) or {}


# --------------------------------------------------------------------------------------
# Base and 2D items
# --------------------------------------------------------------------------------------

class Item:
    """base.gd"""

    def __init__(self, item_type, category, **exports):
        self.item_type = item_type
        self.category = category
        for k, v in exports.items():
            setattr(self, k, v)

    def get_parameter_defs(self):
        return []

    def get_includes(self):
        return []

    def scene_to_shader_model(self, b, scene, uv, editor):
        data = {"parameters": [], "outputs": []}
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, uv, editor)
            data["parameters"] += data2.get("parameters", [])
        return data

    def get_color_code(self, b, scene, ctxt, editor):
        return {}

    @staticmethod
    def get_color_code_smooth_union(b, scene, ctxt, editor):
        index = scene["index"]
        ctxt2 = dict(ctxt, target="tmp_%d" % index, check=False)
        color_code = ""
        for s in scene.get("children", []):
            child = b.get_color_code(s, ctxt2, editor)
            if "distance" in child:
                if "color" in child:
                    color_code += child["color"]
                else:
                    color_code += "\ntmp_%d = current_color_%d;" % (index, index)
                color_code += "\ncoef_%d = 1.0/pow(1.0+max(%s, 0.0), 10000.0*pow(0.8-0.3*clamp($color_k, 0.0, 1.0), 10.0));" % (
                    index, child["distance"])
                color_code += "\nsum_%d += tmp_%d*coef_%d;" % (index, index, index)
                color_code += "\ncoefsum_%d += coef_%d;" % (index, index)
            elif "color" in child:
                color_code += child["color"]
                color_code += "\ncurrent_color_%d = tmp_%d;" % (index, index)
        if color_code == "":
            return {"distance": "_n%d" % index}
        t = ctxt["glsl_type"]
        color_code = ("%s sum_%d;\n%s tmp_%d;\n%s current_color_%d;\nfloat coef_%d;\nfloat coefsum_%d = 0.0;\n" % (
            t, index, t, index, t, index, index, index)) + color_code + ("%s = sum_%d/coefsum_%d;" % (
                ctxt["target"], index, index))
        return {"color": color_code, "distance": "_n%d" % index}


class Union2D(Item):
    """sdf2d/union.gd"""

    def get_parameter_defs(self):
        return COMMON_2D

    def get_includes(self):
        return ["rotate"]

    def shape_code_pre(self, scene, uv):
        return ""

    def shape_code(self, scene, uv):
        return ""

    def shape_and_children_code(self, b, scene, data, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        assigned = False
        data["code"] += "float %s;\n" % output_name
        init_value = self.shape_code(scene, uv)
        if init_value != "":
            data["code"] += self.shape_code_pre(scene, uv)
            data["code"] += "%s = %s;\n" % (output_name, init_value)
            assigned = True
            if editor:
                data["code"] += "if (index == -%d) return %s*$scale;\n" % (scene["index"], output_name)
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, uv, editor)
            data["parameters"] += data2.get("parameters", [])
            if "outputs" in data2:
                data["code"] += data2.get("code", "")
                if assigned:
                    data["code"] += "%s = min(%s, %s);\n" % (output_name, output_name, data2["outputs"][0]["sdf2d"])
                else:
                    data["code"] += "%s = %s;\n" % (output_name, data2["outputs"][0]["sdf2d"])
                    assigned = True

    def mod_uv_code(self, scene, output_name):
        return ""

    def mod_code(self, scene, output_name, editor):
        return ""

    def scene_to_shader_model(self, b, scene, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data = {"parameters": [], "outputs": [{"sdf2d": output_name, "type": "sdf2d"}]}
        data["code"] = "vec2 %s_p = %s - vec2($position_x, $position_y);\n" % (output_name, uv)
        data["code"] += "%s_p = rotate(%s_p, radians($angle))/$scale;\n" % (output_name, output_name)
        data["code"] += self.mod_uv_code(scene, output_name)
        self.shape_and_children_code(b, scene, data, "%s_p" % output_name, editor)
        data["code"] += self.mod_code(scene, output_name, editor)
        data["code"] += "%s *= $scale;\n" % output_name
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], output_name)
        return data

    def get_color_code(self, b, scene, ctxt, editor):
        color_code = ""
        ctxt2 = dict(ctxt, local_uv="$(name_uv)_n%d_p" % scene["index"])
        else_color = ""
        first = True
        children = scene.get("children", [])
        for s in reversed(children):
            if b.category_of(s) == "TEX":
                continue
            child = b.get_color_code(s, ctxt2, editor)
            if "color" not in child:
                continue
            if "distance" not in child:
                if else_color == "":
                    else_color = child["color"]
                continue
            if first:
                first = False
            else:
                color_code += " else "
            color_code += "if (%s < 0.0) {\n%s\n}\n" % (child["distance"], child["color"])
        if else_color != "":
            if first:
                color_code += "%s\n" % else_color
            else:
                color_code += " else {\n%s\n}" % else_color
        elif first:
            return {"distance": "_n%d" % scene["index"]}
        return {"color": color_code, "distance": "_n%d" % scene["index"]}


class Circle2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("radius", 0.5, label="Radius")]

    def shape_code(self, scene, uv):
        return "length(%s)-$radius" % uv


class Box2D(Union2D):
    """sdf2d/box.gd (export shape: Box, Rhombus, IsocelesTriangle, Tunnel, Ellipse)"""
    INCLUDES = [["rotate", "sdbox"], ["rotate", "sdrhombus"], ["rotate", "sdisoscelestriangle"],
                ["rotate", "sdtunnel"], ["rotate", "sdellipse"], ["rotate", "sdstairs"]]
    FUNCTIONS = ["sd_box", "sdRhombus", "sd_isosceles_triangle", "sdTunnel", "sd_ellipse", "sdStairs"]
    shape = 0

    def get_parameter_defs(self):
        return COMMON_2D + [_f("size_x", 0.5, label="Size.x"), _f("size_y", 0.5, label="Size.y")]

    def get_includes(self):
        return self.INCLUDES[self.shape]

    def shape_code(self, scene, uv):
        return "%s(%s, vec2($size_x, $size_y))" % (self.FUNCTIONS[self.shape], uv)


class NGon2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("edgecount", 5, 3.0, 32.0, 1.0, "N"), _f("radius", 0.5, label="Radius")]

    def get_includes(self):
        return ["rotate", "sdcirclerepeat", "sdngon"]

    def shape_code(self, scene, uv):
        return "sdNgon(%s, $radius, $edgecount)" % uv


class Polygon2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("radius", 0.5, label="Radius"),
                            {"label": "Points", "name": "polygon", "type": "polygon", "default": {
                                "points": [{"x": 0.2, "y": 0.2}, {"x": 0.4, "y": 0.7}, {"x": 0.7, "y": 0.4}],
                                "type": "Polygon"}}]

    def shape_code_pre(self, scene, uv):
        code = ""
        code += "vec2 <prefix>uv = %s+vec2(0.5);" % uv
        code += "vec2 <prefix>v[] = $polygon;"
        code += "int <prefix>l = <prefix>v.length();"
        code += "float <prefix>d = dot(<prefix>uv-<prefix>v[0], <prefix>uv-<prefix>v[0]);"
        code += "float <prefix>s = 1.0;"
        code += "int <prefix>j = <prefix>l-1;"
        code += "for (int <prefix>i=0; <prefix>i<<prefix>l; <prefix>i++) {"
        code += "    vec2 <prefix>e = <prefix>v[<prefix>j] - <prefix>v[<prefix>i];"
        code += "    vec2 <prefix>w = <prefix>uv - <prefix>v[<prefix>i];"
        code += "    vec2 <prefix>b = <prefix>w - <prefix>e*clamp(dot(<prefix>w,<prefix>e)/dot(<prefix>e,<prefix>e), 0.0, 1.0 );"
        code += "    <prefix>d = min(<prefix>d, dot(<prefix>b, <prefix>b));"
        code += "    bvec3 <prefix>c = bvec3(<prefix>uv.y>=<prefix>v[<prefix>i].y,<prefix>uv.y<<prefix>v[<prefix>j].y,<prefix>e.x*<prefix>w.y><prefix>e.y*<prefix>w.x);"
        code += "    if (all(<prefix>c) || all(not(<prefix>c))) <prefix>s*=-1.0;"
        code += "    <prefix>j = <prefix>i;"
        code += "}"
        return code.replace("<prefix>", "$(name_uv)_n%d_" % scene["index"])

    def shape_code(self, scene, uv):
        return "<prefix>s*sqrt(<prefix>d)".replace("<prefix>", "$(name_uv)_n%d_" % scene["index"])


class Star2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("branchcount", 5, 3.0, 32.0, 1, "N"), _f("radius", 0.5, label="Radius"),
                            _f("pointiness", 0.5, label="Pointiness")]

    def get_includes(self):
        return ["rotate", "sdstar"]

    def shape_code(self, scene, uv):
        return "sdStar(%s, $radius, int($branchcount),  2.0+($branchcount-2.0)*(1.0-$pointiness))" % uv


class Line2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("ax", -0.25, -0.5, 0.5, label="A.x"), _f("ay", -0.25, -0.5, 0.5, label="A.y"),
                            _f("bx", 0.25, -0.5, 0.5, label="B.x"), _f("by", 0.25, -0.5, 0.5, label="B.y"),
                            _f("r", 0.25, -0.5, 0.5, label="Radius")]

    def get_includes(self):
        return ["rotate", "sdline2"]

    def shape_code(self, scene, uv):
        return "sdLine(%s, vec2($ax, $ay), vec2($bx, $by)).x-$r" % uv


class Stairs2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("size_x", 0.5, label="Size.x"), _f("size_y", 0.5, label="Size.y"),
                            _f("steps", 3, 1.0, 16.0, 1.0, "Steps")]

    def get_includes(self):
        return ["rotate", "sdstairs"]

    def shape_code(self, scene, uv):
        return ("sdStairs(vec2(1.0, -1.0)*%s+vec2($size_x, $size_y), 2.0*vec2($size_x, $size_y)/$steps, $steps)"
                % uv)


class Intersection2D(Union2D):
    def shape_and_children_code(self, b, scene, data, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data["code"] += "float %s = 42.0;" % output_name
        first = True
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            if data2:
                data["parameters"] += data2.get("parameters", [])
                data["code"] += data2.get("code", "")
                if first:
                    data["code"] += "%s = %s;\n" % (output_name, data2["outputs"][0]["sdf2d"])
                    first = False
                else:
                    data["code"] += "%s = max(%s, %s);\n" % (output_name, output_name, data2["outputs"][0]["sdf2d"])
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], output_name)


class Difference2D(Union2D):
    def scene_to_shader_model(self, b, scene, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data = {"parameters": [], "outputs": [{"sdf2d": output_name, "type": "sdf2d"}]}
        data["code"] = "vec2 %s_p = %s - vec2($position_x, $position_y);\n" % (output_name, uv)
        data["code"] += "%s_p = rotate(%s_p, radians($angle))/$scale;\n" % (output_name, output_name)
        data["code"] += "float %s = 0.0;" % output_name
        first = True
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            data["parameters"] += data2.get("parameters", [])
            if "code" in data2:
                data["code"] += data2["code"]
                if first:
                    data["code"] += "%s = %s;\n" % (output_name, data2["outputs"][0]["sdf2d"])
                    first = False
                else:
                    data["code"] += "%s = max(%s, -(%s));\n" % (output_name, output_name, data2["outputs"][0]["sdf2d"])
        data["code"] += "%s *= $scale;\n" % output_name
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], output_name)
        return data


class SmoothUnion2D(Union2D):
    op_sign = "-"

    def get_parameter_defs(self):
        return COMMON_2D + [_f("k", 1.0, 0.0, 5.0, label="K"), _f("color_k", 0.5, label="ColorK")]

    def shape_and_children_code(self, b, scene, data, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data["code"] += "float %s = 0.0;" % output_name
        data["code"] += "float $(name_uv)_n%d_kk = 10.0/$k;" % scene["index"]
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            data["parameters"] += data2.get("parameters", [])
            if "code" in data2:
                data["code"] += data2["code"]
                data["code"] += "%s += exp2(%s$(name_uv)_n%d_kk*%s);\n" % (
                    output_name, self.op_sign, scene["index"], data2["outputs"][0]["sdf2d"])
        data["code"] += "%s = %slog2(%s)/$(name_uv)_n%d_kk;\n" % (output_name, self.op_sign, output_name, scene["index"])

    def get_color_code(self, b, scene, ctxt, editor):
        return self.get_color_code_smooth_union(b, scene, ctxt, editor)


class Flip2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_b("h_flip", True, "H"), _b("v_flip", True, "V")]

    def mod_uv_code(self, scene, output_name):
        p = _params(scene)
        code = ""
        if p.get("h_flip", True):
            code += "%s_p.x = -%s_p.x;\n" % (output_name, output_name)
        if p.get("v_flip", True):
            code += "%s_p.y = -%s_p.y;\n" % (output_name, output_name)
        return code


class Mirror2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_b("h_mirror", True, "H"), _b("v_mirror", True, "V")]

    def mod_uv_code(self, scene, output_name):
        p = _params(scene)
        code = ""
        if p.get("h_mirror", True):
            code += "%s_p.x = abs(%s_p.x);\n" % (output_name, output_name)
        if p.get("v_mirror", True):
            code += "%s_p.y = abs(%s_p.y);\n" % (output_name, output_name)
        return code


class Elongate2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("size_x", 0.1, 0.0, 0.5, label="Size.x"), _f("size_y", 0.1, 0.0, 0.5, label="Size.y")]

    def mod_uv_code(self, scene, o):
        return "vec2 %s_s = vec2($size_x, $size_y);\n%s_p = %s_p - clamp(%s_p, -%s_s, %s_s);\n" % (o, o, o, o, o, o)


class Bend2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("amount", 0.5, -1.0, 1.0, label="Amount")]

    def get_includes(self):
        return ["rotate", "sdbend"]

    def mod_uv_code(self, scene, o):
        return "%s_p = sdBend(%s_p, $amount);\n" % (o, o)


class Annular2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("width", 0.1, 0.0, 0.5, label="Width")]

    def mod_code(self, scene, o, editor):
        return "%s = abs(%s)-0.5*$width;\n" % (o, o)


class Morph2D(Union2D):
    def get_parameter_defs(self):
        return COMMON_2D + [_f("k", 0.5, label="K")]

    def shape_and_children_code(self, b, scene, data, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data["code"] += "float %s = 42.0;" % output_name
        count = 0
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            data["parameters"] += data2.get("parameters", [])
            if "code" in data2:
                data["code"] += data2["code"]
                if count == 0:
                    data["code"] += "%s = %s;\n" % (output_name, data2["outputs"][0][self.FIELD])
                elif count == 1:
                    data["code"] += "%s *= $k;\n" % output_name
                    data["code"] += "%s += %s*(1.0-$k);\n" % (output_name, data2["outputs"][0][self.FIELD])
                count += 1
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], output_name)

    FIELD = "sdf2d"


class Color2D(Union2D):
    """sdf2d/color.gd (exports channel_name, type)"""
    channel_name = ""
    geometry = "sdf2d"

    def get_parameter_defs(self):
        return [_b("local", False, "Local")]

    def get_includes(self):
        return []

    def scene_to_shader_model(self, b, scene, uv, editor):
        data = {"parameters": []}
        for s in scene.get("children", []):
            data["parameters"] += b.scene_to_shader_model(s, uv, editor).get("parameters", [])
        return data

    def get_color_code(self, b, scene, ctxt, editor):
        if self.channel_name != ctxt.get("channel"):
            return {}
        ctxt2 = dict(ctxt, geometry=self.geometry)
        if _params(scene).get("local"):
            ctxt2["uv"] = ctxt["local_uv"]
        for s in scene.get("children", []):
            color_code = b.get_color_code(s, ctxt2, editor)
            if color_code:
                return {"color": ctxt["target"] + " = " + color_code["color"] + ";"}
        return {}


# --------------------------------------------------------------------------------------
# 3D items
# --------------------------------------------------------------------------------------

def _transform_3d_code(b, scene, output_name, uv, editor):
    code = "vec3 %s_p = %s;\n" % (output_name, uv)
    if editor or any(b.check_non_zero_param(scene, n) for n in ("position_x", "position_y", "position_z")):
        code += "%s_p -= vec3($position_x, $position_y, $position_z);\n" % output_name
    code += b.generate_rotate_3d("%s_p" % output_name, scene, editor)
    if editor or b.check_non_zero_param(scene, "scale", 1.0):
        code += "%s_p /= $scale;\n" % output_name
    return code


class Union3D(Item):
    """sdf3d/union.gd"""
    FIELD = "sdf3d"

    def get_parameter_defs(self):
        return COMMON_3D

    def get_includes(self):
        return ["rotate"]

    def shape_code(self, scene, uv):
        return ""

    def shape_and_children_code(self, b, scene, data, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        assigned = False
        data["code"] += "float %s;\n" % output_name
        init_value = self.shape_code(scene, uv)
        if init_value != "":
            data["code"] += "%s = %s;\n" % (output_name, init_value)
            assigned = True
        if editor:
            data["code"] += "if (index == -%d) return %s*$scale;\n" % (scene["index"], output_name)
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            data["parameters"] += data2.get("parameters", [])
            if "outputs" in data2:
                data["code"] += data2.get("code", "")
                if assigned:
                    data["code"] += "%s = min(%s, %s);\n" % (output_name, output_name, data2["outputs"][0]["sdf3d"])
                else:
                    data["code"] += "%s = %s;\n" % (output_name, data2["outputs"][0]["sdf3d"])
                    assigned = True
        if not assigned:
            data["code"] += "%s = 1000000.0;\n" % output_name

    def mod_uv_code(self, scene, output_name):
        return ""

    def mod_code(self, b, scene, output_name, editor):
        return ""

    def scene_to_shader_model(self, b, scene, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data = {"parameters": [], "outputs": [{"sdf3d": output_name, "type": "sdf3d"}]}
        data["code"] = _transform_3d_code(b, scene, output_name, uv, editor)
        data["code"] += self.mod_uv_code(scene, output_name)
        self.shape_and_children_code(b, scene, data, "%s_p" % output_name, editor)
        data["code"] += self.mod_code(b, scene, output_name, editor)
        if editor or b.check_non_zero_param(scene, "scale", 1.0):
            data["code"] += "%s *= $scale;\n" % output_name
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], output_name)
        return data

    def get_color_code(self, b, scene, ctxt, editor):
        index = scene["index"]
        color_code = ""
        ctxt2 = dict(ctxt, local_uv="$(name_uv)_n%d_p" % index)
        colored = []
        color_count = 0
        distance_count = 0
        for s in scene.get("children", []):
            if b.category_of(s) == "TEX":
                continue
            child = b.get_color_code(s, ctxt2, editor)
            colored.append(child)
            if "color" in child:
                color_count += 1
                if "distance" in child:
                    distance_count += 1
        if color_count == 0:
            return {"distance": "_n%d" % index}
        if distance_count == 0:
            for c in colored:
                if "color" in c:
                    return {"color": c["color"], "distance": "_n%d" % index}
        first = True
        for i, c in enumerate(colored):
            if "distance" not in c:
                continue
            if first:
                first = False
                color_code += "int $(name_uv)_n%d_id = %d;\nfloat $(name_uv)_n%d_best_d = %s;\n" % (
                    index, i, index, c["distance"])
            else:
                color_code += ("if ($(name_uv)_n%d_best_d > %s) { $(name_uv)_n%d_best_d = %s; $(name_uv)_n%d_id = %d; }\n"
                               % (index, c["distance"], index, c["distance"], index, i))
        first = True
        else_color = ""
        for i, c in enumerate(colored):
            if "color" not in c:
                continue
            if "distance" not in c:
                if else_color == "":
                    else_color = c["color"]
                continue
            if first:
                first = False
            else:
                color_code += " else "
            color_code += "if ($(name_uv)_n%d_id == %d) {\n%s\n}\n" % (index, i, c["color"])
        if else_color != "":
            color_code += " else {\n%s\n}" % else_color
        return {"color": color_code, "distance": "_n%d" % index}


class Sphere3D(Union3D):
    INCLUDES = [["rotate"], ["rotate", "sdf3d_octahedron"]]
    FUNCTIONS = ["length(%s)-$radius", "sdOctahedron(%s, $radius)"]
    shape = 0

    def get_parameter_defs(self):
        return COMMON_3D + [_f("radius", 0.5, label="Radius")]

    def get_includes(self):
        return self.INCLUDES[self.shape]

    def shape_code(self, scene, uv):
        return self.FUNCTIONS[self.shape] % uv


class Box3D(Union3D):
    INCLUDES = [["rotate", "sdf3d_box"], ["rotate", "sdf3d_ellipsoid"]]
    FUNCTIONS = ["box3d", "sdEllipsoid"]
    shape = 0

    def get_parameter_defs(self):
        return COMMON_3D + [_f("size_x", 0.5, label="Size.x"), _f("size_y", 0.5, label="Size.y"),
                            _f("size_z", 0.5, label="Size.z")]

    def get_includes(self):
        return self.INCLUDES[self.shape]

    def shape_code(self, scene, uv):
        return "%s(%s, vec3($size_x, $size_y, $size_z))" % (self.FUNCTIONS[self.shape], uv)


class Cylinder3D(Union3D):
    INCLUDES = [["rotate", "sdf3d_cylinder"], ["rotate", "sdf3d_capsule2"], ["rotate", "sdf3d_pyramid"],
                ["rotate", "sdf3d_cone2"]]
    FUNCTIONS = ["sdCylinder(%s, $height, $radius)", "sdCapsule(%s, $height, $radius)",
                 "sdPyramid(0.5*%s/$radius, $height/$radius)*$radius*2.0",
                 "sdCone(vec3(0.0, $height, 0.0)-%s, vec2($radius, 2.0*$height))"]
    shape = 0

    def get_parameter_defs(self):
        return COMMON_3D + [_enum("axis", [("X", "xyz"), ("Y", "yzx"), ("Z", "zxy")], 0, "Axis"),
                            _f("height", 0.5, label="Height"), _f("radius", 0.5, label="Radius")]

    def get_includes(self):
        return self.INCLUDES[self.shape]

    def shape_code(self, scene, uv):
        return self.FUNCTIONS[self.shape] % (uv + ".$axis")


class Torus3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_enum("axis", [("X", "zxy"), ("Y", "xyz"), ("Z", "yzx")], 0, "Axis"),
                            _f("R", 0.5, label="Radius1"), _f("r", 0.1, label="Radius2")]

    def get_includes(self):
        return ["rotate", "sdf3d_torus"]

    def shape_code(self, scene, uv):
        return "sdTorus(%s.$axis, vec2($R, $r))" % uv


class Intersection3D(Union3D):
    def shape_and_children_code(self, b, scene, data, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data["code"] += "float %s = 42.0;" % output_name
        first = True
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            if data2:
                data["parameters"] += data2.get("parameters", [])
                data["code"] += data2.get("code", "")
                if first:
                    data["code"] += "%s = %s;\n" % (output_name, data2["outputs"][0]["sdf3d"])
                    first = False
                else:
                    data["code"] += "%s = max(%s, %s);\n" % (output_name, output_name, data2["outputs"][0]["sdf3d"])
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], output_name)


class Difference3D(Union3D):
    def scene_to_shader_model(self, b, scene, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data = {"parameters": [], "outputs": [{"sdf3d": output_name, "type": "sdf3d"}]}
        data["code"] = _transform_3d_code(b, scene, output_name, uv, editor)
        data["code"] += "float %s = 0.0;" % output_name
        first = True
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            if data2:
                data["parameters"] += data2.get("parameters", [])
                data["code"] += data2.get("code", "")
                if "outputs" in data2:
                    if first:
                        data["code"] += "%s = %s;\n" % (output_name, data2["outputs"][0]["sdf3d"])
                        first = False
                    else:
                        data["code"] += "%s = max(%s, -(%s));\n" % (
                            output_name, output_name, data2["outputs"][0]["sdf3d"])
        if editor or b.check_non_zero_param(scene, "scale", 1.0):
            data["code"] += "%s *= $scale;\n" % output_name
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], output_name)
        return data


class SmoothUnion3D(Union3D):
    op_sign = "-"

    def get_parameter_defs(self):
        return COMMON_3D + [_f("k", 1.0, 0.0, 5.0, label="K"), _f("color_k", 0.5, label="ColorK")]

    def shape_and_children_code(self, b, scene, data, uv, editor):
        output_name = "$(name_uv)_n%d" % scene["index"]
        data["code"] += "float %s = 0.0;" % output_name
        data["code"] += "float $(name_uv)_n%d_kk = 10.0/$k;" % scene["index"]
        for s in scene.get("children", []):
            data2 = b.scene_to_shader_model(s, "%s_p" % output_name, editor)
            data["parameters"] += data2.get("parameters", [])
            if "code" in data2:
                data["code"] += data2["code"]
                data["code"] += "%s += exp2(%s$(name_uv)_n%d_kk*%s);\n" % (
                    output_name, self.op_sign, scene["index"], data2["outputs"][0]["sdf3d"])
        data["code"] += "%s = %slog2(%s)/$(name_uv)_n%d_kk;\n" % (output_name, self.op_sign, output_name, scene["index"])

    def get_color_code(self, b, scene, ctxt, editor):
        return self.get_color_code_smooth_union(b, scene, ctxt, editor)


class Flip3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_b("x_flip", True, "X"), _b("y_flip", True, "Y"), _b("z_flip", True, "Z")]

    def mod_uv_code(self, scene, o):
        p = _params(scene)
        return "".join("%s_p.%s = -%s_p.%s;\n" % (o, a, o, a) for a in "xyz" if p.get(a + "_flip", True))


class Mirror3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_b("x_mirror", True, "X"), _b("y_mirror", True, "Y"), _b("z_mirror", True, "Z")]

    def mod_uv_code(self, scene, o):
        p = _params(scene)
        return "".join("%s_p.%s = abs(%s_p.%s);\n" % (o, a, o, a) for a in "xyz" if p.get(a + "_mirror", True))


class Elongate3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_f("size_x", 0.1, 0.0, 0.5, label="Size.x"), _f("size_y", 0.1, 0.0, 0.5, label="Size.y"),
                            _f("size_z", 0.1, 0.0, 0.5, label="Size.z")]

    def mod_uv_code(self, scene, o):
        return "vec3 %s_s = vec3($size_x, $size_y, $size_z);\n%s_p = %s_p - clamp(%s_p, -%s_s, %s_s);\n" % (
            o, o, o, o, o, o)


class Round3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_f("width", 0.1, 0.0, 0.5, label="Width")]

    def mod_code(self, b, scene, o, editor):
        return "%s -= $width;\n" % o


class Bend3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_f("amount", 0.5, -1.0, 1.0, label="Amount")]

    def get_includes(self):
        return ["rotate", "sdf3d_bend"]

    def mod_uv_code(self, scene, o):
        return "%s_p = Bend3d(%s_p, $amount);\n" % (o, o)


class Annular3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_f("width", 0.1, 0.0, 0.5, label="Width")]

    def mod_code(self, b, scene, o, editor):
        return "%s = abs(%s)-0.5*$width;\n" % (o, o)


class Morph3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_f("k", 0.5, label="K")]

    shape_and_children_code = Morph2D.shape_and_children_code


class Twist3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_enum("axis", [("X", "yz"), ("Y", "zx"), ("Z", "xy")], 0, "Axis"),
                            _f("angle", 0.0, -180.0, 180.0, 0.1, "Angle")]

    def mod_uv_code(self, scene, o):
        axis = int(_params(scene).get("axis", 0))
        if axis == 1:
            return "%s_p.zx=rotate(%s_p.zx, %s_p.y*$angle*0.01745329251);\n" % (o, o, o)
        if axis == 2:
            return "%s_p.xy=rotate(%s_p.xy, %s_p.z*$angle*0.01745329251);\n" % (o, o, o)
        return "%s_p.yz=rotate(%s_p.yz, %s_p.x*$angle*0.01745329251);\n" % (o, o, o)


class Distort3D(Union3D):
    def get_parameter_defs(self):
        return COMMON_3D + [_f("amount", 0.1, label="Amount")]

    def mod_code(self, b, scene, o, editor):
        for s in scene.get("children", []):
            if b.category_of(s) != "TEX":
                continue
            ctxt = {"uv": o + "_p", "geometry": "sdf3d", "type": "float", "glsl_type": "float"}
            distort_map = b.get_color_code(s, ctxt, editor)
            if "color" in distort_map:
                return "%s += $amount*(%s-0.5);\n" % (o, distort_map["color"])
        return ""


class Color3D(Color2D):
    geometry = "sdf3d"


class _Generate3D(Union2D):
    """Common part of extrusion.gd and revolution.gd (2D children, 3D output)."""

    def get_parameter_defs(self):
        return COMMON_3D + self.EXTRA

    def get_includes(self):
        return ["sdf3d_rotate"]

    def shape_code(self, scene, uv):
        return "1000000.0"

    def get_color_code(self, b, scene, ctxt, editor):
        local = self.LOCAL % scene["index"]
        ctxt2 = dict(ctxt, local_uv=local, uv=local, check=False)
        color_code = Union2D.get_color_code(self, b, scene, ctxt2, editor)
        if "color" in color_code:
            return {"color": color_code["color"], "distance": "_n%d" % scene["index"]}
        return {"distance": "_n%d" % scene["index"]}


class Extrusion3D(_Generate3D):
    EXTRA = [_f("length", 0.5, label="Length")]
    LOCAL = "$(name_uv)_n%d_p.xy"

    def scene_to_shader_model(self, b, scene, uv, editor):
        o = "$(name_uv)_n%d" % scene["index"]
        data = {"parameters": [], "outputs": [{"sdf3d": o, "type": "sdf3d"}]}
        data["code"] = _transform_3d_code(b, scene, o, uv, editor)
        self.shape_and_children_code(b, scene, data, "%s_p.xy" % o, editor)
        data["code"] += "vec2 %s_w = vec2(%s, abs(%s_p.z) - $length);\n" % (o, o, o)
        data["code"] += "%s = min(max(%s_w.x, %s_w.y),0.0) + length(max(%s_w,0.0));\n" % (o, o, o, o)
        if editor or b.check_non_zero_param(scene, "scale", 1.0):
            data["code"] += "%s *= $scale;\n" % o
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], o)
        return data


class Revolution3D(_Generate3D):
    EXTRA = [_f("offset", 0.5, label="Offset")]
    LOCAL = "$(name_uv)_n%d_q"

    def scene_to_shader_model(self, b, scene, uv, editor):
        o = "$(name_uv)_n%d" % scene["index"]
        data = {"parameters": [], "outputs": [{"sdf3d": o, "type": "sdf3d"}]}
        data["code"] = _transform_3d_code(b, scene, o, uv, editor)
        data["code"] += "vec2 %s_q = vec2(length(%s_p.xz)-$offset, %s_p.y);\n" % (o, o, o)
        self.shape_and_children_code(b, scene, data, "%s_q" % o, editor)
        if editor or b.check_non_zero_param(scene, "scale", 1.0):
            data["code"] += "%s *= $scale;\n" % o
        if editor:
            data["code"] += "if (index == %d) return %s;\n" % (scene["index"], o)
        return data


# --------------------------------------------------------------------------------------
# Texture items
# --------------------------------------------------------------------------------------

class TexItem(Item):
    def scene_to_shader_model(self, b, scene, uv, editor):
        data = {"parameters": []}
        for s in scene.get("children", []):
            data["parameters"] += b.scene_to_shader_model(s, uv, editor).get("parameters", [])
        return data


class UniformTex(TexItem):
    def get_parameter_defs(self):
        return [_color("color", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}, "Color")]

    def scene_to_shader_model(self, b, scene, uv, editor):
        o = "$(name_uv)_n%d" % scene["index"]
        return {"parameters": [], "outputs": [{"sdf2d": o, "type": "sdf2d"}], "code": ""}

    def get_color_code(self, b, scene, ctxt, editor):
        tex = "$color"
        if ctxt.get("type") == "color":
            tex = "($color).xyz"
        elif ctxt.get("type") == "float":
            tex = "dot(($color).xyz, vec3(1.0))/3.0"
        return {"color": tex}


class FbmTex(TexItem):
    def get_parameter_defs(self):
        return [_enum("noise", [("Value", "value"), ("Perlin", "perlin"), ("Cellular", "cellular")], 0, "Noise"),
                _f("scale_x", 4, 1, 32, 1, "Scale X"), _f("scale_y", 4, 1, 32, 1, "Scale Y"),
                _f("scale_z", 4, 1, 32, 1, "Scale Z"), _f("folds", 0, 0, 5, 1, "Folds"),
                _f("iterations", 3, 1, 10, 1, "Iterations"), _f("persistence", 0.5, label="Persistence"),
                _f("offset", 0, label="Offset")]

    def get_includes(self):
        return ["fbm2", "tex3d_fbm"]

    def get_color_code_gs(self, ctxt):
        if ctxt.get("geometry") == "sdf3d":
            return "fbm3d_$noise(" + ctxt["uv"] + ", vec3($scale_x, $scale_y, $scale_z), int($iterations), $persistence, 0.0)"
        return ("fbm_2d_$noise(" + ctxt["uv"] + ", vec2($scale_x, $scale_y), int($folds), int($iterations), "
                "$persistence, $offset, 0.0)")

    def get_color_code(self, b, scene, ctxt, editor):
        tex = self.get_color_code_gs(ctxt)
        if ctxt.get("type") == "rgba":
            tex = "vec4(vec3(" + tex + "), 1.0)"
        elif ctxt.get("type") == "color":
            tex = "vec3(" + tex + ")"
        return {"color": tex}


_WAVES = [("Sine", "sine"), ("Triangle", "triangle"), ("Square", "square"), ("Sawtooth", "sawtooth"),
          ("Constant", "constant"), ("Bounce", "bounce")]


class PatternTex(FbmTex):
    def get_parameter_defs(self):
        return [_enum("mix", [("Multiply", "mul"), ("Add", "add"), ("Max", "max"), ("Min", "min"), ("Xor", "xor"),
                              ("Pow", "pow")], 0, "Combiner"),
                _enum("x_wave", _WAVES, 0, "X"), _f("x_scale", 4, 0, 32, 1, "2:"),
                _enum("y_wave", _WAVES, 0, "Y"), _f("y_scale", 4, 0, 32, 1, "3:"),
                _enum("z_wave", _WAVES, 0, "Z"), _f("z_scale", 4, 0, 32, 1, "4:")]

    def get_includes(self):
        return ["pattern", "tex3d_pattern"]

    def get_color_code_gs(self, ctxt):
        uv = ctxt["uv"]
        if ctxt.get("geometry") == "sdf3d":
            return ("mix3d_$mix(wave_$x_wave($x_scale*" + uv + ".x), wave_$y_wave($y_scale*" + uv + ".y), "
                    "wave_$z_wave($z_scale*" + uv + ".z))")
        return "mix_$mix(wave_$x_wave($x_scale*" + uv + ".x), wave_$y_wave($y_scale*" + uv + ".y))"


class UniformGsTex(FbmTex):
    def get_parameter_defs(self):
        return [_f("value", 1.0, label="Value")]

    def get_includes(self):
        return []

    def get_color_code_gs(self, ctxt):
        return "$value"


class BrightnessContrastTex(TexItem):
    def get_parameter_defs(self):
        return [_f("brightness", 0.0, -1, 1, label="Brightness"), _f("contrast", 1.0, 0, 2, label="Contrast"),
                _b("clamp", True, "Clamp")]

    def get_color_code(self, b, scene, ctxt, editor):
        t = ctxt.get("type")
        child = {"rgba": "vec4(vec3(0.5), 1.0)", "color": "vec3(0.5)"}.get(t, "0.5")
        if scene.get("children"):
            c = b.get_color_code(scene["children"][0], ctxt, editor)
            if "color" in c:
                child = c["color"]
        type_ = {"rgba": "vec4", "color": "vec3"}.get(t, "")
        if t == "rgba":
            tex = "%s*vec4(vec3($contrast), 1.0)+vec4(vec3($brightness+0.5-$contrast*0.5), 0.0)" % child
        else:
            tex = "%s*$contrast+%s($brightness+0.5-$contrast*0.5)" % (child, type_)
        if _params(scene).get("clamp", True):
            tex = "clamp(%s, %s(0.0), %s(1.0))" % (tex, type_, type_)
        return {"color": tex}


class StepTex(TexItem):
    def get_parameter_defs(self):
        return [_f("value", 0.5, label="Value"), _f("width", 0.1, 0, 0.5, label="Width"), _b("invert", False, "Invert")]

    def get_color_code(self, b, scene, ctxt, editor):
        if not scene.get("children"):
            child = "0.0"
        else:
            child = b.get_color_code(scene["children"][0], dict(ctxt, type="float"), editor)["color"]
        tex = "clamp((" + child + "-$value)/max(0.0001, $width)+0.5, 0.0, 1.0)"
        if _params(scene).get("invert"):
            tex = "(1.0-" + tex + ")"
        if ctxt.get("type") == "rgba":
            tex = "vec4(vec3(" + tex + "), 1.0)"
        elif ctxt.get("type") == "color":
            tex = "vec3(" + tex + ")"
        return {"color": tex}


class SimpleGradientTex(TexItem):
    def get_parameter_defs(self):
        return [_color("color1", {"r": 0.0, "g": 0.0, "b": 0.0, "a": 1.0}, "Color 1"),
                _color("color2", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}, "Color 2")]

    def get_color_code(self, b, scene, ctxt, editor):
        if not scene.get("children"):
            tex = "$color1"
        else:
            tex = "mix($color1, $color2, " + b.get_color_code(scene["children"][0], dict(ctxt, type="float"),
                                                               editor)["color"] + ")"
        if ctxt.get("type") == "color":
            tex = "(" + tex + ").xyz"
        elif ctxt.get("type") == "float":
            tex = "dot((" + tex + ").xyz, vec3(1.0))/3.0"
        return {"color": tex}


class BlendTex(TexItem):
    def get_color_code(self, b, scene, ctxt, editor):
        tex = "vec4(1.0)"
        children = scene.get("children", [])
        ctxt2 = dict(ctxt, type="rgba")
        if children:
            tex = b.get_color_code(children[0], ctxt2, editor)["color"]
            for i in range(1, len(children), 2):
                ctxt2["type"] = "rgba"
                tex2 = b.get_color_code(children[i], ctxt2, editor)["color"]
                mask = "0.5"
                if len(children) > i + 1:
                    ctxt2["type"] = "float"
                    mask = b.get_color_code(children[i + 1], ctxt2, editor)["color"]
                tex = "mix(" + tex + ", " + tex2 + ", " + mask + ")"
        if ctxt.get("type") == "color":
            tex = "(" + tex + ").xyz"
        elif ctxt.get("type") == "float":
            tex = "dot((" + tex + ").xyz, vec3(1.0))/3.0"
        return {"color": tex}


class DeformTex(TexItem):
    def get_parameter_defs(self):
        return [_f("amount", 0.0, -1, 1, label="Amount")]

    def get_color_code(self, b, scene, ctxt, editor):
        children = scene.get("children", [])
        tex = ""
        if not children:
            tex = "1.0"
            if ctxt.get("type") == "rgba":
                tex = "vec4(vec3(" + tex + "), 1.0)"
            elif ctxt.get("type") == "color":
                tex = "vec3(" + tex + ")"
        else:
            deform = "vec3(0.0)"
            if len(children) > 1:
                c = b.get_color_code(children[1], dict(ctxt, type="color"), editor)
                if "color" in c:
                    deform = c["color"]
            ctxt2 = dict(ctxt)
            if ctxt.get("geometry") == "sdf3d":
                ctxt2["uv"] = "(%s+$amount*(%s.xyz-vec3(0.5)))" % (ctxt2["uv"], deform)
            else:
                ctxt2["uv"] = "(%s+$amount*(%s.xy-vec2(0.5)))" % (ctxt2["uv"], deform)
            c = b.get_color_code(children[0], ctxt2, editor)
            if "color" in c:
                tex = c["color"]
        return {"color": tex}


# --------------------------------------------------------------------------------------
# Builder (sdf_builder.gd) and its registry (sdf_builder.tscn)
# --------------------------------------------------------------------------------------

def _make_items():
    def item(cls, item_type, category, **exports):
        return cls(item_type, category, **exports)

    items = [
        item(Circle2D, "Circle", "SDF2D"), item(Box2D, "Box", "SDF2D"),
        item(Box2D, "Ellipse", "SDF2D", shape=4), item(NGon2D, "NGon", "SDF2D"),
        item(Polygon2D, "Polygon", "SDF2D"), item(Star2D, "Star", "SDF2D"),
        item(Box2D, "IsoTriangle", "SDF2D", shape=2), item(Box2D, "Rhombus", "SDF2D", shape=1),
        item(Line2D, "Line", "SDF2D"), item(Box2D, "Tunnel", "SDF2D", shape=3), item(Stairs2D, "Stairs", "SDF2D"),
        item(Union2D, "Union", "SDF2D"), item(Intersection2D, "Intersection", "SDF2D"),
        item(Difference2D, "Difference", "SDF2D"), item(SmoothUnion2D, "SmoothUnion", "SDF2D"),
        item(SmoothUnion2D, "SmoothIntersection", "SDF2D", op_sign=""),
        item(Flip2D, "Flip", "SDF2D"), item(Mirror2D, "Mirror", "SDF2D"), item(Elongate2D, "Elongate", "SDF2D"),
        item(Bend2D, "Bend", "SDF2D"), item(Annular2D, "Annular", "SDF2D"), item(Morph2D, "Morph", "SDF2D"),
        item(Color2D, "Albedo2D", "SDF2D_COLOR", channel_name="albedo", type=2),
        item(Color2D, "Metallic2D", "SDF2D_COLOR", channel_name="metallic"),
        item(Color2D, "Roughness2D", "SDF2D_COLOR", channel_name="roughness"),
        item(Color2D, "Emission2D", "SDF2D_COLOR", channel_name="emission", type=1),
        item(Sphere3D, "Sphere", "SDF3D"), item(Box3D, "Box3D", "SDF3D"), item(Cylinder3D, "Cylinder", "SDF3D"),
        item(Cylinder3D, "Capsule", "SDF3D", shape=1), item(Cylinder3D, "Cone", "SDF3D", shape=3),
        item(Torus3D, "Torus", "SDF3D"), item(Cylinder3D, "Pyramid", "SDF3D", shape=2),
        item(Box3D, "Ellipsoid", "SDF3D", shape=1), item(Sphere3D, "Octahedron", "SDF3D", shape=1),
        item(Extrusion3D, "Extrusion", "SDF3D"), item(Revolution3D, "Revolution", "SDF3D"),
        item(Union3D, "Union3D", "SDF3D"), item(Intersection3D, "Intersection3D", "SDF3D"),
        item(Difference3D, "Difference3D", "SDF3D"), item(SmoothUnion3D, "SmoothUnion3D", "SDF3D"),
        item(SmoothUnion3D, "SmoothIntersection3D", "SDF3D", op_sign=""),
        item(Flip3D, "Flip3D", "SDF3D"), item(Mirror3D, "Mirror3D", "SDF3D"),
        item(Elongate3D, "Elongate3D", "SDF3D"), item(Round3D, "Round3D", "SDF3D"),
        item(Bend3D, "Bend3D", "SDF3D"), item(Annular3D, "Annular3D", "SDF3D"), item(Morph3D, "Morph3D", "SDF3D"),
        item(Twist3D, "Twist", "SDF3D"), item(Distort3D, "Distort3D", "SDF3D"),
        item(Color3D, "Albedo3D", "SDF3D_COLOR", channel_name="albedo", type=2),
        item(Color3D, "Metallic3D", "SDF3D_COLOR", channel_name="metallic"),
        item(Color3D, "Roughness3D", "SDF3D_COLOR", channel_name="roughness"),
        item(Color3D, "Emission3D", "SDF3D_COLOR", channel_name="emission", type=1),
        item(UniformTex, "Uniform", "TEX"), item(UniformGsTex, "GreyscaleUniform", "TEX"),
        item(PatternTex, "Pattern", "TEX"), item(FbmTex, "FBM", "TEX"),
        item(BrightnessContrastTex, "BrightnessContrast", "TEX"), item(StepTex, "Step", "TEX"),
        item(SimpleGradientTex, "SimpleGradient", "TEX"), item(BlendTex, "Blend", "TEX"),
        item(DeformTex, "Deform", "TEX"),
    ]
    return {i.item_type: i for i in items}


class SDFBuilder:
    def __init__(self):
        self.items = _make_items()

    def item(self, scene):
        try:
            return self.items[scene["type"]]
        except KeyError:
            raise ValueError("Unknown EasySDF item type %r" % scene.get("type"))

    def category_of(self, scene):
        return self.item(scene).category

    def get_includes(self, scene):
        if scene.get("hidden"):
            return []
        includes = []
        for i in self.item(scene).get_includes():
            if i not in includes:
                includes.append(i)
        for c in scene.get("children", []):
            for i in self.get_includes(c):
                if i not in includes:
                    includes.append(i)
        return includes

    def replace_parameter_values(self, scene, string):
        node = self.item(scene)
        params = _params(scene)
        exprs = scene.get("parmexprs", {}) or {}
        for p in node.get_parameter_defs():
            value = params.get(p["name"], p.get("default"))
            t = p["type"]
            if t == "boolean":
                string = string.replace("$" + p["name"], "true" if value else "false")
            elif t == "enum":
                string = string.replace("$" + p["name"], p["values"][int(value)]["value"])
            elif t == "float":
                if p["name"] in exprs:
                    string = string.replace("$" + p["name"], exprs[p["name"]])
                else:
                    string = string.replace("$" + p["name"], "%.09f" % float(value))
            elif t == "color":
                c = value if isinstance(value, Color) else deserialize_value(value)
                string = string.replace("$" + p["name"], "vec4(%.09f, %.09f, %.09f, %.09f)" % (c.r, c.g, c.b, c.a))
            elif t == "polygon":
                points = value.points if hasattr(value, "points") else value["points"]
                coords = [(pt["x"], pt["y"]) if isinstance(pt, dict) else pt for pt in points]
                string = string.replace("$" + p["name"], "{" + ",".join("vec2(%.09f,%.09f)" % c for c in coords) + "}")
        return string

    def scene_to_shader_model(self, scene, uv, editor=False):
        if scene.get("hidden"):
            return {}
        shader_model = self.item(scene).scene_to_shader_model(self, scene, uv, editor)
        if "code" in shader_model:
            shader_model["code"] = self.replace_parameter_values(scene, shader_model["code"])
        if shader_model:
            shader_model["includes"] = self.get_includes(scene)
        return shader_model

    def get_color_code(self, scene, ctxt, editor=False):
        if scene.get("hidden"):
            return {}
        rv = self.item(scene).get_color_code(self, scene, ctxt, editor)
        if "color" in rv:
            rv["color"] = self.replace_parameter_values(scene, rv["color"])
        return rv

    @staticmethod
    def check_non_zero_param(scene, name, zero=0.0):
        p = _params(scene)
        exprs = scene.get("parmexprs", {}) or {}
        return name in exprs or (name in p and abs(float(p[name]) - zero) > 0.0001)

    def generate_rotate_3d(self, variable, scene, editor=True):
        rv = ""
        if editor or self.check_non_zero_param(scene, "angle_y"):
            rv += "%s.zx = rotate(%s.zx, radians($angle_y));\n" % (variable, variable)
        if editor or self.check_non_zero_param(scene, "angle_x"):
            rv += "%s.yz = rotate(%s.yz, radians($angle_x));\n" % (variable, variable)
        if editor or self.check_non_zero_param(scene, "angle_z"):
            rv += "%s.xy = rotate(%s.xy, radians($angle_z));\n" % (variable, variable)
        return rv

    def deserialize_scene(self, s):
        # Like sdf_builder.gd: only the top level's parameters are deserialized.
        rv = []
        for i in s:
            new_i = dict(i)
            if "parameters" in i:
                new_i["parameters"] = {k: deserialize_value(v) for k, v in i["parameters"].items()}
            rv.append(new_i)
        return rv


BUILDER = SDFBuilder()


def build_shader_model(scene, node_parameters):
    """Port of MMGenSDF.set_sdf_scene (editor and expressions off): the node's shader model."""
    b = BUILDER
    scene_type = b.category_of(scene[0]) if scene else ""
    shader_model = {"includes": [], "parameters": []}
    if scene_type == "SDF3D":
        uv = "$uv"
        distance_function = "float $(name)_d(vec3 uv"
        color_function = "float $(name)_c(vec3 uv"
        default_albedo = "vec4(1.0, 1.0, 1.0, 1.0)"
    else:
        uv = "$uv-vec2(0.5)"
        distance_function = "float $(name)_d(vec2 uv"
        color_function = "float $(name)_c(vec2 uv"
        default_albedo = "vec4(0.0, 0.0, 0.0, 1.0)"
    distance_function += ") {\n"
    color_function += ", out vec4 albedo, out float metallic, out float roughness, out vec3 emission) {\n"
    color_function += "albedo = " + default_albedo + ";\n"
    color_function += "metallic = 0.0;\n"
    color_function += "roughness = 1.0;\n"
    color_function += "emission = vec3(0.0);\n"
    first = True
    parameter_defs = [dict(p, label=p.get("shortdesc", "")) for p in node_parameters]
    for i in scene:
        if i.get("hidden"):
            continue
        item_model = b.scene_to_shader_model(i, "uv", False)
        shader_model["includes"] += item_model.get("includes", [])
        parameter_defs += item_model.get("parameters", [])
        if "code" in item_model:
            code = item_model["code"].replace("$(name_uv)", "")
            distance_function += code
            color_function += code
        if "outputs" in item_model:
            field = "sdf2d" if scene_type == "SDF2D" else "sdf3d"
            output = item_model["outputs"][0]
            if field in output:
                name = output[field].replace("$(name_uv)", "")
                if first:
                    distance_function += "float return_value = %s;\n" % name
                    color_function += "float return_value = %s;\n" % name
                    first = False
                else:
                    distance_function += "return_value = min(return_value, %s);\n" % name
                    color_function += "return_value = min(return_value, %s);\n" % name
    fake_scene = {"index": 0, "type": "Union" if scene_type == "SDF2D" else "Union3D", "children": scene,
                  "parameters": {}}
    for channel, target, type_, glsl_type, title in (
            ("albedo", "albedo", "rgba", "vec4", "Albedo"), ("metallic", "metallic", "float", "float", "Metallic"),
            ("roughness", "roughness", "float", "float", "Roughness"),
            ("emission", "emission", "color", "vec3", "Emission")):
        color_code = b.get_color_code(fake_scene, {"uv": "uv", "channel": channel, "target": target, "type": type_,
                                                   "glsl_type": glsl_type})
        if "color" in color_code:
            color_function += "\n// %s\n{\n%s}\n" % (title, color_code["color"])
    color_function += "\n"
    if first:
        distance_function += "return 1.0;"
        color_function += "return 1.0;"
    else:
        distance_function += "return return_value;"
        color_function += "return return_value;"
    distance_function += "}\n"
    color_function += "}\n"
    color_function = color_function.replace("$(name_uv)", "")
    shader_model["instance"] = distance_function + color_function
    code = "vec4 $(name_uv)_albedo;\n"
    code += "float $(name_uv)_metallic;\n"
    code += "float $(name_uv)_roughness;\n"
    code += "vec3 $(name_uv)_emission;\n"
    if scene_type == "SDF3D":
        code += ("$(name)_c(%s.xyz*vec3(1.0, -1.0, -1.0), $(name_uv)_albedo, $(name_uv)_metallic, "
                 "$(name_uv)_roughness, $(name_uv)_emission);\n" % uv)
        shader_model["outputs"] = [
            {"sdf3d": "@NOCODE $(name)_d(%s*vec3(1.0, -1.0, -1.0))" % uv, "type": "sdf3d"},
            {"tex3d": "$(name_uv)_albedo.rgb", "type": "tex3d", "shortdesc": "Albedo"},
            {"tex3d_gs": "clamp($(name_uv)_metallic, 0.0, 1.0)", "type": "tex3d_gs", "shortdesc": "Metallic"},
            {"tex3d_gs": "clamp($(name_uv)_roughness, 0.0, 1.0)", "type": "tex3d_gs", "shortdesc": "Roughness"},
            {"tex3d": "$(name_uv)_emission", "type": "tex3d", "shortdesc": "Emission"},
        ]
    else:
        code += ("$(name)_c(%s, $(name_uv)_albedo, $(name_uv)_metallic, $(name_uv)_roughness, "
                 "$(name_uv)_emission);\n" % uv)
        shader_model["outputs"] = [
            {"sdf2d": "@NOCODE $(name)_d(%s)" % uv, "type": "sdf2d", "shortdesc": "SDF"},
            {"rgba": "$(name_uv)_albedo", "type": "rgba", "shortdesc": "Albedo"},
            {"f": "$(name_uv)_metallic", "type": "f", "shortdesc": "Metallic"},
            {"f": "$(name_uv)_roughness", "type": "f", "shortdesc": "Roughness"},
            {"rgb": "$(name_uv)_emission", "type": "rgb", "shortdesc": "Emission"},
        ]
    shader_model["code"] = code
    shader_model["parameters"] = parameter_defs
    return shader_model
