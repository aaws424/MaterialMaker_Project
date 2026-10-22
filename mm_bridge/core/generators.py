"""Generator classes: port of Material Maker 1.3 engine/gen_*.gd.

Shader code is passed around as dicts shaped like MM's: keys `globals` (list of
strings), `defs`, `code`, `type`, plus one expression per io type (`f`, `rgb`,
`rgba`, ...).
"""

import math
import re

from .context import GenContext
from .mmtypes import Color, Curve, Gradient, Polygon, deserialize_value, is_float, serialize_value
from .textutil import (
    find_keyword_call,
    find_matching_parenthesis,
    is_identifier_char,
    is_valid_integer,
    replace_rnd,
    replace_rndi,
    replace_variables,
)

MAX_SEED = 4294967296


def default_generated_shader():
    return {"globals": [], "defs": "", "code": "", "textures": {}, "type": "f", "f": "0.0"}


def _godot_int(x):
    """int() of a Godot float, wrapped to a signed 64-bit integer."""
    v = int(x) & 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v >= (1 << 63) else v


def _godot_mod(a, b):
    """GDScript integer % (truncates toward zero, like C)."""
    return int(math.fmod(a, b))


class OutputPort:
    __slots__ = ("generator", "output_index")

    def __init__(self, generator, output_index):
        self.generator = generator
        self.output_index = int(output_index)


class GenBase:
    gen_type = "generic"

    def __init__(self, project):
        self.project = project
        self.uid = project.next_uid()
        self.name = ""
        self.parent = None
        self.position = (0.0, 0.0)
        self.model = None
        self.parameters = {}
        self.seed_locked = False
        self.seed_value = 0.0

    def __repr__(self):
        return "<%s %s o%d>" % (type(self).__name__, self.get_hier_name() or self.name, self.uid)

    # Identity

    @property
    def genname(self):
        return "o%d" % self.uid

    def get_hier_name(self):
        names = []
        node = self
        while node is not None and node.parent is not None:
            names.append(node.name)
            node = node.parent
        return "/".join(reversed(names))

    def get_type(self):
        return self.gen_type

    # Seeds

    def has_randomness(self):
        return False

    def get_seed_from_position(self, p):
        return _godot_mod((_godot_int(p[0]) * 0x1F1F1F1F) ^ _godot_int(p[1]), 65536)

    def get_seed(self):
        s = self.seed_value
        if not self.seed_locked and self.parent is not None and getattr(self.parent, "transmits_seed", False):
            s += self.parent.get_seed()
        return s

    # Parameters

    def get_parameter_defs(self):
        return []

    def get_parameter_def(self, name):
        for p in self.get_parameter_defs():
            if p.get("name") == name:
                return p
        return {}

    def init_parameters(self):
        for p in self.get_parameter_defs():
            if p.get("name") not in self.parameters and "default" in p:
                value = deserialize_value(p["default"])
                if p.get("type") == "size":
                    value -= p["first"]
                self.parameters[p["name"]] = value

    def get_parameter(self, n):
        if n in self.parameters:
            return self.parameters[n]
        return self.get_parameter_def(n).get("default")

    def set_parameter(self, n, v):
        old = self.parameters.get(n)
        self.parameters[n] = v
        self.project.record_change(self, n, old, v)

    # Ports

    def get_input_defs(self):
        return []

    def get_output_defs(self, show_hidden=False):
        return []

    def get_source(self, input_index):
        if self.parent is None:
            return None
        return self.parent.get_port_source(self.name, int(input_index))

    def get_output_attributes(self, output_index):
        return {}

    # Shader generation

    def get_shader_code(self, uv, output_index, context):
        rv = self._get_shader_code(uv, output_index, context)
        rv_type = rv.get("type")
        io_types = self.project.library.io_types
        if rv_type in io_types:
            for c in io_types[rv_type].get("convert", []):
                if c["type"] not in rv:
                    rv[c["type"]] = c["expr"].replace("$(value)", rv[rv_type])
        elif rv:
            self.project.warn("%r produced shader code without a type" % self)
        return rv

    def _get_shader_code(self, uv, output_index, context):
        return {}

    # Serialization

    def _deserialize(self, data):
        pass

    def _post_load(self):
        pass

    def deserialize(self, data):
        self._deserialize(data)
        if "name" in data:
            self.name = data["name"]
        if "node_position" in data:
            self.position = (float(data["node_position"]["x"]), float(data["node_position"]["y"]))
        if "parameters" in data:
            for p, v in data["parameters"].items():
                self.set_parameter(p, deserialize_value(v))
        else:
            for p in self.get_parameter_defs():
                if p["name"] in data and p["name"] != "type":
                    self.set_parameter(p["name"], deserialize_value(data[p["name"]]))
        self.seed_locked = bool(data.get("seed_locked")) if isinstance(data.get("seed_locked"), bool) else False
        if "seed_int" in data:
            self.seed_value = float(data["seed_int"]) / MAX_SEED
        elif "seed" in data:
            self.seed_value = data["seed"]
        elif "seed_value" in data:
            self.seed_locked = True
            self.seed_value = data["seed_value"]
        else:
            self.seed_locked = False
            self.seed_value = float(self.get_seed_from_position(self.position))
        self._post_load()

    def serialize(self):
        rv = {
            "name": self.name,
            "type": self.model if self.model is not None else self.get_type(),
            "parameters": {},
            "node_position": {"x": self.position[0], "y": self.position[1]},
        }
        for p in self.get_parameter_defs():
            if p["name"] in self.parameters:
                rv["parameters"][p["name"]] = serialize_value(self.parameters[p["name"]])
            elif "default" in p:
                rv["parameters"][p["name"]] = p["default"]
        if 0.0 <= self.seed_value <= 1.0:
            rv["seed_int"] = int(round(self.seed_value * MAX_SEED))
        else:
            rv["seed"] = self.seed_value
        if self.seed_locked:
            rv["seed_locked"] = True
        return rv


class GenUnsupported(GenBase):
    """Placeholder for generators this port does not implement yet."""

    def __init__(self, project, gen_type):
        super().__init__(project)
        self.gen_type = gen_type
        self.data = None

    def _deserialize(self, data):
        self.data = data

    def _get_shader_code(self, uv, output_index, context):
        self.project.warn("Unsupported generator type '%s' (%s), output replaced by 0" % (self.gen_type, self.name))
        return default_generated_shader()

    def serialize(self):
        # Saved back exactly as it was read, so Material Maker keeps it.
        rv = dict(self.data or {})
        rv.update(name=self.name, node_position={"x": self.position[0], "y": self.position[1]})
        return rv


class GenComment(GenBase):
    gen_type = "comment"

    def __init__(self, project):
        super().__init__(project)
        self.data = {}

    def _deserialize(self, data):
        self.data = data

    def serialize(self):
        rv = GenBase.serialize(self)
        for k in ("title", "color", "text", "size"):
            if k in self.data:
                rv[k] = self.data[k]
        return rv


class GenDebug(GenComment):
    gen_type = "debug"


# --------------------------------------------------------------------------------------
# Shader generator
# --------------------------------------------------------------------------------------

_INSTANCE_FUNCTION_RE = re.compile(r"(\w+)\s+(\w*\$(?:\(name\)\w*|name))\s*\((.*)\)\s*{")


class GenShader(GenBase):
    gen_type = "shader"

    def __init__(self, project):
        super().__init__(project)
        self.shader_model = {}
        self.preprocessed = {}
        self.model_uses_seed = False
        self.params_use_seed = False
        self.generic_size = 1

    def has_randomness(self):
        return self.model_uses_seed or self.params_use_seed

    def get_parameter_defs(self):
        return self.preprocessed.get("parameters", []) or []

    def get_input_defs(self):
        return self.preprocessed.get("inputs", []) or []

    def get_output_defs(self, show_hidden=False):
        return self.preprocessed.get("outputs", []) or []

    def get_preprocessed_output_def(self, output_index):
        outputs = self.preprocessed.get("outputs") or []
        if 0 <= output_index < len(outputs):
            return outputs[output_index]
        return None

    def set_parameter(self, n, v):
        super().set_parameter(n, v)
        self.params_use_seed = any(
            isinstance(pv, str) and ("$rnd(" in pv or "$rndi(" in pv) for pv in self.parameters.values())

    # Model preprocessing

    @staticmethod
    def find_instance_functions(code):
        functions = []
        for m in list(_INSTANCE_FUNCTION_RE.finditer(code)):
            if m.group(1) not in ("return",):
                functions.append(m.group(2))
                code = code.replace(m.group(0), "%s %s(%s, float _seed_variation_) {" % (m.group(1), m.group(2), m.group(3)))
        return code, functions

    @staticmethod
    def fix_instance_functions(code, instance_functions):
        variation_parameter = ", _seed_variation_"
        for f in instance_functions:
            f_lower = f.lower()
            location = 0
            while True:
                location = code.lower().find(f_lower, location)
                if location == -1:
                    break
                if location > 0 and is_identifier_char(code[location - 1]):
                    location += len(f)
                    continue
                p = location + len(f)
                while p < len(code):
                    c = code[p]
                    if c == "(":
                        p = find_matching_parenthesis(code, p)
                        if p < 0:
                            p = len(code)
                            break
                        replace = True
                        p2 = p
                        while p2 < len(code) - 1:
                            p2 += 1
                            c2 = code[p2]
                            if c2 in " \t\n\r":
                                continue
                            if c2 == "{":
                                replace = False
                            break
                        if replace:
                            code = code[:p] + variation_parameter + code[p:]
                        break
                    elif c in " \t\r\n":
                        p += 1
                    else:
                        break
                location = p
        return code

    def preprocess_shader_model(self, data):
        io_types = self.project.library.io_types
        pre = {}
        if data.get("instance", "") != "":
            instance, functions = self.find_instance_functions(data["instance"])
            pre["instance"] = self.fix_instance_functions(instance, functions)
            if "code" in data:
                pre["code"] = self.fix_instance_functions(data["code"], functions)
            if "parameters" in data:
                pre["parameters"] = []
                for p in data["parameters"]:
                    if p.get("type") == "enum":
                        values = []
                        replace = False
                        for v in p["values"]:
                            fixed = self.fix_instance_functions(v["value"], functions)
                            replace = replace or fixed != v["value"]
                            values.append({"name": v["name"], "value": fixed})
                        if replace:
                            p = dict(p, values=values)
                    pre["parameters"].append(p)
            if "outputs" in data:
                pre["outputs"] = []
                for o in data["outputs"]:
                    o = dict(o)
                    if "type" in o and o["type"] in o:
                        o[o["type"]] = self.fix_instance_functions(o[o["type"]], functions)
                    else:
                        for f in io_types:
                            if f in o:
                                o[f] = self.fix_instance_functions(o[f], functions)
                                o["type"] = f
                    pre["outputs"].append(o)
        else:
            for k in ("parameters", "code", "outputs"):
                if k in data:
                    pre[k] = data[k]
        for k in ("inputs", "includes", "global"):
            if k in data:
                pre[k] = data[k]
        return pre

    # Genericity (# in names expanded generic_size times)

    def is_generic(self):
        return any("#" in p.get("name", "") for p in self.shader_model.get("parameters", [])) or \
            any("#" in i.get("name", "") for i in self.shader_model.get("inputs", []))

    def set_generic_size(self, size):
        size = int(size)
        if self.generic_size == size or not self.is_generic():
            return
        self.generic_size = size
        self.set_shader_model(self.shader_model)

    def expand_generic_code(self, code, first_generic_value=1):
        rv = ""
        while code != "":
            for_position = code.find("#for")
            if for_position == -1:
                rv += code
                break
            rv += code[:for_position]
            code = code[for_position + 4:]
            end_position = code.find("#end")
            if end_position == -1:
                generic_code, code = code, ""
            else:
                generic_code, code = code[:end_position], code[end_position + 4:]
            for gi in range(self.generic_size):
                rv += generic_code.replace("#", str(gi + first_generic_value))
        return rv

    @staticmethod
    def get_generic_range(array, field, indirect=False):
        first, last = -1, -1
        for i, p in enumerate(array):
            string = p.get(p.get(field, ""), "") if indirect else p.get(field, "")
            if "#" in string:
                if first == -1:
                    first = i
                elif last != -1:
                    return -1, -1
            elif first != -1 and last == -1:
                last = i
        if first != -1 and last == -1:
            last = len(array)
        return first, last

    @staticmethod
    def _shift_label(parameter, condition, delta):
        label = parameter.get("label", "")
        colon = label.find(":")
        if colon != -1 and is_valid_integer(label[:colon]):
            position = int(label[:colon])
            if condition(position):
                return dict(parameter, label=str(position + delta) + label[colon:])
        return parameter

    def expand_generic(self):
        sm = self.shader_model
        pre = self.preprocessed
        first_in, last_in = self.get_generic_range(sm.get("inputs", []), "name")
        first_par, last_par = self.get_generic_range(sm.get("parameters", []), "name")
        first_out, last_out = self.get_generic_range(sm.get("outputs", []), "type", True)
        first_value = 1

        def expand_desc(d, gv):
            d = dict(d)
            for k in ("name", "label", "shortdesc", "longdesc"):
                if k in d and isinstance(d[k], str):
                    d[k] = d[k].replace("#", str(gv))
            return d

        if first_in != -1:
            inputs = list(sm["inputs"][:first_in])
            for gi in range(self.generic_size):
                for i in range(first_in, last_in):
                    inputs.append(expand_desc(sm["inputs"][i], first_value + gi))
            inputs += sm["inputs"][last_in:]
            pre["inputs"] = inputs

        params_in = sm.get("parameters", [])
        if first_par != -1:
            ranges = []
            if first_par > 0:
                ranges.append((0, first_par, False))
            ranges.append((first_par, last_par, True))
            if last_par < len(params_in):
                ranges.append((last_par, len(params_in), False))
        else:
            ranges = [(0, len(params_in), False)]
        n_generic_inputs = last_in - first_in
        parameters = []
        for begin, end, generic in ranges:
            if generic:
                for gi in range(self.generic_size):
                    for i in range(begin, end):
                        p = params_in[i]
                        label = p.get("label", "")
                        p = expand_desc(p, first_value + gi)
                        p["label"] = label
                        p = self._shift_label(
                            p, lambda pos: first_in < pos <= last_in, gi * n_generic_inputs)
                        parameters.append(p)
            else:
                for i in range(begin, end):
                    parameters.append(self._shift_label(
                        params_in[i], lambda pos: pos >= last_in, (self.generic_size - 1) * n_generic_inputs))
        pre["parameters"] = parameters

        if first_out != -1:
            outs = pre["outputs"]
            outputs = list(outs[:first_out])
            for gi in range(self.generic_size):
                gv = first_value + gi
                for i in range(first_out, last_out):
                    o = dict(outs[i])
                    o[o["type"]] = o[o["type"]].replace("#", str(gv))
                    for k in ("shortdesc", "longdesc"):
                        if k in o:
                            o[k] = o[k].replace("#", str(gv))
                    outputs.append(o)
            outputs += outs[last_out:]
            pre["outputs"] = outputs
        if "code" in pre:
            pre["code"] = self.expand_generic_code(pre["code"], first_value)
        if "instance" in pre:
            pre["instance"] = self.expand_generic_code(pre["instance"], first_value)

    def set_shader_model(self, data):
        io_types = self.project.library.io_types
        self.shader_model = data
        # Outputs need their "type" key before generic expansion looks at it.
        for o in data.get("outputs", []):
            if "type" not in o or o["type"] not in o:
                for f in io_types:
                    if f in o:
                        o["type"] = f
                        break
        self.preprocessed = self.preprocess_shader_model(data)
        if self.is_generic():
            self.expand_generic()
        self.init_parameters()
        self.model_uses_seed = False
        sections = [o.get(o.get("type", ""), "") for o in data.get("outputs", [])]
        sections += [data.get("code", ""), data.get("instance", "")]
        for s in sections:
            if isinstance(s, str) and ("$seed" in s or "$(seed)" in s):
                self.model_uses_seed = True

    def _deserialize(self, data):
        if "shader_model" in data:
            self.set_shader_model(data["shader_model"])
        elif "model_data" in data:
            self.set_shader_model(data["model_data"])
        if "generic_size" in data:
            self.set_generic_size(data["generic_size"])

    def serialize(self):
        rv = super().serialize()
        if self.model is None:
            rv["shader_model"] = self.shader_model
        if self.is_generic():
            rv["generic_size"] = self.generic_size
        return rv

    # Code generation

    def replace_input_with_function_call(self, string, input_name, seed_parameter=", _seed_variation_", input_suffix=""):
        while True:
            uv = find_keyword_call(string, input_name + input_suffix)
            if uv is None or uv == "":
                if uv == "":
                    self.project.warn("Syntax error in %r: empty $%s()" % (self, input_name))
                return string
            string = string.replace(
                "$%s(%s)" % (input_name + input_suffix, uv),
                "%s_input_%s(%s%s)" % (self.genname, input_name, uv, seed_parameter))

    def replace_input(self, string, context, input_name, input_type, src, default):
        required_globals = []
        required_defs = ""
        required_code = ""
        new_pass_required = False
        while True:
            uv = find_keyword_call(string, input_name)
            if uv is None:
                break
            if uv == "":
                self.project.warn("Syntax error in %r: empty $%s()" % (self, input_name))
                break
            if "$" in uv:
                new_pass_required = True
                break
            if src is None:
                src_code = self.subst(default, context, "(%s)" % uv)
            else:
                src_code = src.generator.get_shader_code(uv, src.output_index, context)
                if input_type in src_code:
                    src_code["string"] = src_code[input_type]
                else:
                    self.project.warn("%r: input '%s' expects type '%s' but source %r provides '%s'" % (
                        self, input_name, input_type, src.generator, src_code.get("type")))
                    src_code["string"] = "*error missing " + input_type + "*"
            for d in src_code.get("globals", []):
                if d not in required_globals:
                    required_globals.append(d)
            required_defs += src_code.get("defs", "")
            required_code += src_code.get("code", "")
            string = string.replace("$%s(%s)" % (input_name, uv), src_code["string"])
        return {"string": string, "globals": required_globals, "defs": required_defs,
                "code": required_code, "new_pass_required": new_pass_required}

    def _parameter_value_string(self, p, rnd_offset):
        genname = self.genname
        value = self.parameters.get(p["name"])
        t = p["type"]
        if t == "float":
            if is_float(value):
                return "p_%s_%s" % (genname, p["name"])
            if isinstance(value, str):
                s = "(" + value + ")"
                if "$rnd(" in value:
                    s = replace_rnd(s, rnd_offset)
                if "$rndi(" in value:
                    s = replace_rndi(s, rnd_offset)
                return s
            self.project.warn("%r: bad float parameter %s=%r" % (self, p["name"], value))
            return "0.0"
        if t == "size":
            return "%.9f" % math.pow(2, value)
        if t == "enum":
            values = p.get("values", [])
            if not values:
                return ""
            if not is_float(value) or value < 0 or value >= len(values):
                value = 0
            return values[int(value)]["value"]
        if t == "color":
            return "p_%s_%s" % (genname, p["name"])
        if t == "gradient":
            return genname + "_" + p["name"] + "_gradient_fct"
        if t == "curve":
            return genname + "_" + p["name"] + "_curve_fct"
        if t in ("polygon", "polyline"):
            if not isinstance(value, Polygon):
                value = Polygon.deserialize(value)
            return value.get_shader()
        if t == "boolean":
            return "true" if value else "false"
        self.project.warn("%r: cannot replace parameter of type %s" % (self, t))
        return None

    def subst(self, string, context, uv=""):
        genname = self.genname
        parent = self.parent
        required_globals = []
        if parent is not None and hasattr(parent, "get_globals"):
            required_globals = [parent.get_globals()]
        required_defs = ""
        required_code = ""
        variables = {}
        if parent is not None and hasattr(parent, "get_named_parameters"):
            for np, d in parent.get_named_parameters().items():
                variables[np] = d["id"]
        variables["name"] = genname
        if uv != "":
            variables["name_uv"] = genname + "_" + str(context.get_variant(self, uv))
        if self.seed_locked:
            variables["seed"] = "seed_" + genname
        else:
            variables["seed"] = "(seed_" + genname + "+fract(_seed_variation_))"
        variables["node_id"] = str(self.uid)
        rnd_offset = 0
        for p in self.get_parameter_defs():
            if "name" not in p or "type" not in p:
                continue
            value_string = self._parameter_value_string(p, rnd_offset)
            if p["type"] == "float":
                rnd_offset += 17
            if value_string is not None:
                variables[p["name"]] = value_string
        if uv != "":
            if uv[0] == "(" and find_matching_parenthesis(uv, 0) == len(uv) - 1:
                variables["uv"] = uv
            else:
                variables["uv"] = "(" + uv + ")"
        variables["time"] = "elapsed_time"
        inputs = self.get_input_defs()
        for i, input_def in enumerate(inputs):
            source = self.get_source(i)
            if source is None:
                continue
            attributes = source.generator.get_output_attributes(source.output_index)
            if "texture" in attributes:
                for sg in source.generator.get_globals(attributes["texture"]):
                    if sg not in required_globals:
                        required_globals.append(sg)
            for a, av in attributes.items():
                if a == "texture_size":
                    variables[input_def["name"] + ".size"] = av
                else:
                    variables[input_def["name"] + "." + a] = av
        string = replace_variables(string, variables)
        if inputs:
            cont = True
            while cont:
                changed = False
                new_pass_required = False
                for i, input_def in enumerate(inputs):
                    source = self.get_source(i)
                    if input_def.get("function"):
                        string = self.replace_input_with_function_call(string, input_def["name"])
                        string = self.replace_input_with_function_call(string, input_def["name"], "", ".variation")
                    else:
                        result = self.replace_input(string, context, input_def["name"], input_def["type"],
                                                    source, input_def.get("default", "0.0"))
                        if string != result["string"]:
                            changed = True
                        if result["new_pass_required"]:
                            new_pass_required = True
                        string = result["string"]
                        for d in result["globals"]:
                            if d not in required_globals:
                                required_globals.append(d)
                        required_defs += result["defs"]
                        required_code += result["code"]
                cont = changed and new_pass_required
                string = replace_variables(string, variables)
        return {"string": string, "globals": required_globals, "defs": required_defs, "code": required_code}

    def generate_parameter_declarations(self, rv):
        genname = self.genname
        if self.has_randomness():
            rv["defs"] += "uniform float seed_%s = %.9f;\n" % (genname, self.get_seed())
        for p in self.get_parameter_defs():
            t = p.get("type")
            value = self.parameters.get(p.get("name"))
            if t == "float" and is_float(value):
                rv["defs"] += "uniform float p_%s_%s = %.9f;\n" % (genname, p["name"], value)
            elif t == "color":
                c = value if isinstance(value, Color) else deserialize_value(value)
                rv["defs"] += "uniform vec4 p_%s_%s = vec4(%.9f, %.9f, %.9f, %.9f);\n" % (
                    genname, p["name"], c.r, c.g, c.b, c.a)
            elif t == "gradient":
                g = value if isinstance(value, Gradient) else Gradient.deserialize(value)
                rv["defs"] += g.get_shader_params(genname + "_" + p["name"])
                rv["defs"] += g.get_shader(genname + "_" + p["name"])
            elif t == "curve":
                c = value if isinstance(value, Curve) else Curve.deserialize(value)
                for sp, spv in c.get_shader_params(genname + "_" + p["name"]).items():
                    rv["defs"] += "uniform float %s = %.9f;\n" % (sp, spv)
                rv["defs"] += c.get_shader(genname + "_" + p["name"])
        return rv

    def generate_input_declarations(self, rv, context):
        io_types = self.project.library.io_types
        genname = self.genname
        for i, input_def in enumerate(self.get_input_defs()):
            if not input_def.get("function"):
                continue
            io_type = io_types[input_def["type"]]
            source = self.get_source(i)
            string = "$%s(%s)" % (input_def["name"], io_type["params"])
            local_context = GenContext(context)
            result = self.replace_input(string, local_context, input_def["name"], input_def["type"],
                                        source, input_def.get("default", "0.0"))
            for d in result["globals"]:
                if d not in rv["globals"]:
                    rv["globals"].append(d)
            rv["defs"] += result["defs"]
            rv["defs"] += "%s %s_input_%s(%s, float _seed_variation_) {\n" % (
                io_type["type"], genname, input_def["name"], io_type["paramdefs"])
            rv["defs"] += "%s\n" % result["code"]
            rv["defs"] += "return %s;\n}\n" % result["string"]
        return rv

    def _get_shader_code(self, uv, output_index, context):
        io_types = self.project.library.io_types
        genname = self.genname
        rv = {"globals": [], "defs": "", "code": ""}
        output = self.get_preprocessed_output_def(output_index)
        if output is None:
            return rv
        if not context.has_variant(self):
            rv = self.generate_parameter_declarations(rv)
            rv = self.generate_input_declarations(rv, context)
            if "instance" in self.preprocessed:
                rv["defs"] += self.subst(self.preprocessed["instance"], context, "")["string"]
        output_expr = output.get(output.get("type", ""), "")
        if "code" in self.preprocessed and "@NOCODE" not in output_expr:
            variant_index = context.get_variant(self, uv)
            if variant_index == -1:
                variant_index = context.get_variant(self, uv)
                subst_code = self.subst(self.preprocessed["code"], context, uv)
                for d in subst_code["globals"]:
                    if d not in rv["globals"]:
                        rv["globals"].append(d)
                rv["defs"] += subst_code["defs"]
                rv["code"] += subst_code["code"]
                rv["code"] += subst_code["string"]
        variant_string = uv + "," + str(output_index)
        variant_index = context.get_variant(self, variant_string)
        if variant_index == -1:
            variant_index = context.get_variant(self, variant_string)
            for f in io_types:
                if f in output:
                    subst_output = self.subst(output[f].replace("@NOCODE", ""), context, uv)
                    for d in subst_output["globals"]:
                        if d not in rv["globals"]:
                            rv["globals"].append(d)
                    rv["defs"] += subst_output["defs"]
                    rv["code"] += subst_output["code"]
                    rv["code"] += "%s %s_%d_%d_%s = %s;\n" % (
                        io_types[f]["type"], genname, output_index, variant_index, f, subst_output["string"])
        for f in io_types:
            if f in output:
                rv[f] = "%s_%d_%d_%s" % (genname, output_index, variant_index, f)
        rv["type"] = output["type"]
        for inc in self.preprocessed.get("includes", []) or []:
            g = self.project.library.get_predefined_global(inc)
            if g != "" and g not in rv["globals"]:
                rv["globals"].append(g)
        if "global" in self.preprocessed and self.preprocessed["global"] not in rv["globals"]:
            rv["globals"].append(self.preprocessed["global"])
        return rv


class GenSDF(GenShader):
    """EasySDF node (port of gen_sdf.gd): its shader model is generated from an SDF scene."""

    gen_type = "sdf"

    def __init__(self, project):
        super().__init__(project)
        self.node_parameters = []
        self.scene = []

    def _deserialize(self, data):
        from .sdf import BUILDER, build_shader_model

        self.node_parameters = [dict(p) for p in data.get("node_parameters", [])]
        if "sdf_scene" in data:
            self.scene = BUILDER.deserialize_scene(data["sdf_scene"])
            self.set_shader_model(build_shader_model(self.scene, self.node_parameters))

    def serialize(self):
        def scene_data(items):
            rv = []
            for i in items:
                i = dict(i)
                if "parameters" in i:
                    i["parameters"] = {k: serialize_value(v) for k, v in i["parameters"].items()}
                if "children" in i:
                    i["children"] = scene_data(i["children"])
                rv.append(i)
            return rv

        rv = GenBase.serialize(self)
        rv.update(type="sdf", node_parameters=self.node_parameters, sdf_scene=scene_data(self.scene))
        return rv


class GenMaterial(GenShader):
    """Material node (material_export). Its outputs are the exported texture channels."""

    gen_type = "material_export"

    def get_export(self, profile):
        return self.shader_model.get("exports", {}).get(profile, {})

    def get_export_profiles(self):
        return sorted(self.shader_model.get("exports", {}).keys())

    def get_connections_and_parameters_context(self):
        context = {}
        for i, input_def in enumerate(self.shader_model.get("inputs", [])):
            context["$(connected:" + input_def["name"] + ")"] = "true" if self.get_source(i) is not None else "false"
        for p in self.shader_model.get("parameters", []):
            value = self.parameters.get(p["name"], p.get("default"))
            if p["type"] in ("float", "size"):
                context["$(param:" + p["name"] + ")"] = str(value)
            elif p["type"] == "boolean":
                context["$(param:" + p["name"] + ")"] = str(bool(value)).lower()
            elif p["type"] == "color" and isinstance(value, Color):
                for c in "rgba":
                    context["$(param:%s.%s)" % (p["name"], c)] = str(getattr(value, c))
        return context

    def get_image_size(self):
        if "size" in self.parameters:
            return int(math.pow(2, self.parameters["size"]))
        return 1024


# --------------------------------------------------------------------------------------
# Graph and its helpers
# --------------------------------------------------------------------------------------

class GenGraph(GenBase):
    gen_type = "graph"
    transmits_seed = True

    def __init__(self, project):
        super().__init__(project)
        self.label = "Graph"
        self.shortdesc = ""
        self.longdesc = ""
        self.children = {}
        self.connections = []

    def get_node(self, name):
        return self.children.get(name)

    def has_randomness(self):
        return any(c.has_randomness() and not c.seed_locked for c in self.children.values())

    def get_parameter_defs(self):
        gp = self.get_node("gen_parameters")
        return gp.get_parameter_defs() if gp is not None else []

    def set_parameter(self, n, v):
        super().set_parameter(n, v)
        gp = self.get_node("gen_parameters")
        if gp is not None:
            gp.set_parameter(n, v)

    def get_input_defs(self):
        gi = self.get_node("gen_inputs")
        return gi.get_output_defs() if gi is not None else []

    def get_output_defs(self, show_hidden=False):
        go = self.get_node("gen_outputs")
        return go.get_input_defs() if go is not None else []

    def get_port_source(self, gen_name, input_index):
        rv = None
        if gen_name == "gen_inputs":
            if isinstance(self.parent, GenGraph):
                rv = self.parent.get_port_source(self.name, input_index)
        else:
            for c in self.connections:
                if c["to"] == gen_name and int(c["to_port"]) == input_index:
                    src_gen = self.get_node(c["from"])
                    if src_gen is not None:
                        if isinstance(src_gen, GenGraph):
                            rv = src_gen.get_port_source("gen_outputs", int(c["from_port"]))
                        else:
                            rv = OutputPort(src_gen, c["from_port"])
        if rv is not None and rv.generator.name == "gen_inputs" and rv.generator.parent is self:
            rv = self.get_port_source("gen_inputs", rv.output_index)
        return rv

    def get_port_targets(self, gen_name, output_index):
        return [(self.get_node(c["to"]), int(c["to_port"])) for c in self.connections
                if c["from"] == gen_name and int(c["from_port"]) == output_index and c["to"] in self.children]

    def add_generator(self, generator):
        name = generator.name
        if name in ("Material", "Brush"):
            if name in self.children or isinstance(self.parent, GenGraph):
                return False
        elif name == "":
            name = generator.get_type() + "_1"
        if name in self.children:
            m = re.match(r"^(.*_)\d+$", generator.name)
            prefix = m.group(1) if m else generator.name + "_"
            index = 1
            while name in self.children:
                index += 1
                name = prefix + str(index)
        generator.name = name
        generator.parent = self
        self.children[name] = generator
        return True

    def connect_children(self, from_gen, from_port, to_gen, to_port):
        if from_gen is None or to_gen is None:
            return False
        self.connections = [c for c in self.connections
                            if not (c["to"] == to_gen.name and int(c["to_port"]) == int(to_port))]
        self.connections.append({"from": from_gen.name, "from_port": int(from_port),
                                 "to": to_gen.name, "to_port": int(to_port)})
        return True

    def get_named_parameters(self):
        named = {}
        for c in self.children.values():
            if isinstance(c, GenRemote):
                for k, v in c.get_named_parameters().items():
                    named[k] = {"id": v, "value": c.get_parameter(k)}
        return named

    def get_globals(self):
        return "".join(c.get_globals() for c in self.children.values() if isinstance(c, GenRemote))

    def fix_remotes(self):
        for c in self.children.values():
            if isinstance(c, GenRemote):
                c.fix()

    def _post_load(self):
        self.fix_remotes()

    def _get_shader_code(self, uv, output_index, context):
        outputs = self.get_node("gen_outputs")
        if outputs is not None:
            return outputs._get_shader_code(uv, output_index, context)
        return {"globals": [], "defs": "", "code": "", "textures": {}}

    def _deserialize(self, data):
        for k in ("label", "shortdesc", "longdesc"):
            if k in data:
                setattr(self, k, data[k])
        connections = data.get("connections", [])
        if isinstance(connections, dict):
            compact = connections
            connections = [{"from": f["node"], "from_port": f["port"], "to": n, "to_port": int(p)}
                           for n, ports in compact.items() for p, f in ports.items()]
        self.project.add_to_gen_graph(self, data.get("nodes", []), connections)

    def serialize(self):
        rv = super().serialize()
        if self.model is None:
            rv.update(label=self.label, shortdesc=self.shortdesc, longdesc=self.longdesc,
                      nodes=[c.serialize() for c in self.children.values()],
                      connections=[dict(c) for c in self.connections])
        return rv


class GenIOs(GenBase):
    gen_type = "ios"

    def __init__(self, project):
        super().__init__(project)
        self.ports = []

    def get_io_defs(self):
        return [{"name": p["name"], "type": p["type"]} for p in self.ports]

    def get_input_defs(self):
        return [] if self.name == "gen_inputs" else self.get_io_defs()

    def get_output_defs(self, show_hidden=False):
        return [] if self.name == "gen_outputs" else self.get_io_defs()

    def _get_shader_code(self, uv, output_index, context):
        source = self.get_source(output_index)
        if source is not None:
            return source.generator._get_shader_code(uv, source.output_index, context)
        return default_generated_shader()

    def _deserialize(self, data):
        self.ports = [dict(p) for p in data.get("ports", [])]

    def serialize(self):
        rv = super().serialize()
        rv["ports"] = [dict(p) for p in self.ports]
        return rv


class GenRemote(GenBase):
    """Remote / gen_parameters: exposes parameters that drive other nodes' parameters."""

    gen_type = "remote"

    def __init__(self, project):
        super().__init__(project)
        self.widgets = []

    def get_widget(self, n):
        for w in self.widgets:
            if w.get("name") == n:
                return w
        return {}

    def get_next_widget_name(self):
        i = 0
        while any(w.get("name") == "param%d" % i for w in self.widgets):
            i += 1
        return "param%d" % i

    def fix(self):
        for w in self.widgets:
            if "name" not in w:
                w["name"] = self.get_next_widget_name()
        parent = self.parent
        if parent is None:
            return
        updated = []
        for w in self.widgets:
            if "linked_widgets" not in w:
                updated.append(w)
                continue
            linked = []
            for lw in w["linked_widgets"]:
                g = parent.get_node(lw["node"])
                if g is not None and g.get_parameter_def(lw["widget"]):
                    linked.append(lw)
            if not linked:
                continue
            w["linked_widgets"] = linked
            updated.append(w)
            if w["name"] not in self.parameters and w["type"] in ("config_control", "linked_control"):
                self.parameters[w["name"]] = 0.0
        self.widgets = updated

    def get_parameter_defs(self):
        rv = []
        for w in self.widgets:
            wt = w.get("type")
            if wt == "config_control":
                p = {"name": w["name"], "label": w.get("label", ""), "type": "enum"}
                configurations = sorted(w.get("configurations", {}).keys())
                if configurations == ["False", "True"]:
                    p["type"] = "boolean"
                else:
                    p["values"] = [{"name": c, "value": c} for c in configurations]
            elif wt == "linked_control":
                p = {}
                if w.get("linked_widgets") and self.parent is not None:
                    linked = w["linked_widgets"][0]
                    g = self.parent.get_node(linked["node"])
                    if g is not None:
                        pd = g.get_parameter_def(linked["widget"])
                        if pd:
                            p = dict(pd)
                p["name"] = w["name"]
                p["label"] = w.get("label", "")
            elif wt == "named_parameter":
                p = {"name": w["name"], "label": w.get("label", ""), "type": "float", "min": w["min"],
                     "max": w["max"], "step": w["step"], "default": w["default"]}
            else:
                break
            p["widget_type"] = wt
            for k in ("shortdesc", "longdesc"):
                if k in w:
                    p[k] = w[k]
            rv.append(p)
        return rv

    def set_parameter(self, p, v):
        parent = self.parent
        if parent is not None:
            widget = self.get_widget(p)
            wt = widget.get("type")
            if wt == "linked_control":
                for lw in widget["linked_widgets"]:
                    node = parent.get_node(lw["node"])
                    if node is not None:
                        node.set_parameter(lw["widget"], v)
            elif wt == "config_control":
                if isinstance(v, bool):
                    v = 1 if v else 0
                configurations = sorted(widget.get("configurations", {}).keys())
                if not is_float(v) or not (0 <= v < len(configurations)):
                    self.project.warn("%r: incorrect config control value %r for %s" % (self, v, p))
                    return
                for cw in widget["configurations"][configurations[int(v)]]:
                    node = parent.get_node(cw["node"])
                    if node is not None:
                        node.set_parameter(cw["widget"], deserialize_value(cw["value"]))
        super().set_parameter(p, v)
        if parent is not None and self.name == "gen_parameters":
            parent.parameters[p] = v

    def enter_tree(self):
        """What MMGenRemote._enter_tree does once the whole graph is loaded."""
        self.fix()
        for p in list(self.parameters.keys()):
            self.set_parameter(p, deserialize_value(serialize_value(self.parameters[p])))

    def get_named_parameters(self):
        return {w["name"]: "o%d_%s" % (self.uid, w["name"]) for w in self.widgets
                if w.get("type") == "named_parameter"}

    def get_globals(self):
        rv = ""
        for w in self.widgets:
            if w.get("type") == "named_parameter":
                rv += "uniform float o%d_%s = %.09f;\n" % (self.uid, w["name"], self.get_parameter(w["name"]))
        return rv

    def _deserialize(self, data):
        self.widgets = [dict(w) for w in data.get("widgets", [])]
        self.fix()

    def serialize(self):
        rv = super().serialize()
        rv["widgets"] = self.widgets
        return rv


class GenSwitch(GenBase):
    gen_type = "switch"

    def get_parameter_defs(self):
        choices = int(self.parameters.get("choices", 2))
        return [
            {"name": "outputs", "label": "Outputs", "type": "float", "min": 1, "max": 5, "step": 1, "default": 2.0},
            {"name": "choices", "label": "Choices", "type": "float", "min": 2, "max": 5, "step": 1, "default": 2.0},
            {"name": "source", "label": "Source", "type": "float", "min": 0, "max": choices - 1, "step": 1,
             "default": 0.0},
        ]

    def get_input_defs(self):
        return [{"name": chr(65 + o) + str(c), "type": "any"}
                for c in range(int(self.get_parameter("choices"))) for o in range(int(self.get_parameter("outputs")))]

    def get_output_defs(self, show_hidden=False):
        return [{"name": chr(65 + o), "type": "any"} for o in range(int(self.get_parameter("outputs")))]

    def _get_shader_code(self, uv, output_index, context):
        index = output_index + int(self.get_parameter("source")) * int(self.get_parameter("outputs"))
        source = self.get_source(index)
        if source is not None:
            return source.generator._get_shader_code(uv, source.output_index, context)
        return default_generated_shader()


class GenReroute(GenBase):
    gen_type = "reroute"

    def get_input_defs(self):
        return [{"name": "in", "type": "any"}]

    def get_output_defs(self, show_hidden=False):
        return [{"type": "any"}]

    def _get_shader_code(self, uv, output_index, context):
        source = self.get_source(0)
        if source is not None:
            return source.generator._get_shader_code(uv, source.output_index, context)
        return default_generated_shader()


# --------------------------------------------------------------------------------------
# Texture generators (sampled through a sampler2D rendered in a separate pass)
# --------------------------------------------------------------------------------------

class GenTexture(GenBase):
    def get_output_defs(self, show_hidden=False):
        return [{"type": "rgba"}]

    def texture_name(self, suffix="_tex"):
        return self.genname + suffix

    def get_adjusted_uv(self, uv):
        return uv

    def get_globals(self, texture_name):
        return ["uniform sampler2D " + texture_name + ";\n"]

    def _get_shader_code_lod(self, uv, output_index, context, lod=-1.0, texture_suffix="_tex"):
        genname = self.genname
        rv = {"globals": [], "defs": "", "code": "", "type": "rgba"}
        texture_name = genname + texture_suffix
        self.project.register_texture(texture_name, self)
        variant_index = context.get_variant(self, uv)
        if variant_index == -1:
            variant_index = context.get_variant(self, uv)
            if lod < 0.0:
                rv["code"] = "vec4 %s_%d = textureLod(%s, %s, 0.0);\n" % (
                    genname, variant_index, texture_name, self.get_adjusted_uv(uv))
            else:
                rv["defs"] = "uniform float p_o%d_lod = %.09f;\n" % (self.uid, lod)
                rv["code"] = "vec4 %s_%d = textureLod(%s, %s, p_o%d_lod);\n" % (
                    genname, variant_index, texture_name, self.get_adjusted_uv(uv), self.uid)
        rv["rgba"] = "%s_%d" % (genname, variant_index)
        rv["globals"] = self.get_globals(texture_name)
        rv["textures"] = {texture_name: self}
        return rv

    def _get_shader_code(self, uv, output_index, context):
        return self._get_shader_code_lod(uv, output_index, context)


class GenBuffer(GenTexture):
    """Renders its input at 2^size resolution; downstream nodes sample the result."""

    gen_type = "buffer"
    VERSION_OLD, VERSION_SIMPLE, VERSION_COMPLEX = 0, 1, 2

    def __init__(self, project):
        super().__init__(project)
        self.version = self.VERSION_OLD

    def get_parameter_defs(self):
        defs = [{"name": "size", "type": "size", "first": 4, "last": 13, "default": 4}]
        if self.version == self.VERSION_OLD:
            defs.append({"name": "lod", "type": "float", "min": 0, "max": 10.0, "step": 0.01, "default": 0.0})
        elif self.version == self.VERSION_COMPLEX:
            defs.append({"name": "filter", "type": "boolean", "default": True})
            defs.append({"name": "mipmap", "type": "boolean", "default": True})
        return defs

    def get_input_defs(self):
        return [{"name": "in", "type": "rgba"}]

    def get_output_defs(self, show_hidden=False):
        if self.version == self.VERSION_OLD:
            return [{"type": "rgba"}, {"type": "rgba"}]
        return [{"type": "rgba"}]

    def get_size(self):
        return int(math.pow(2, self.get_parameter("size")))

    def get_sampling(self):
        """(filter, mipmap) like the Godot texture flags MMGenBuffer sets (always repeating)."""
        if self.version == self.VERSION_COMPLEX:
            return bool(self.parameters.get("filter", True)), bool(self.parameters.get("mipmap", True))
        return True, True

    def get_globals(self, texture_name):
        return ["uniform sampler2D %s;\nuniform float %s_size = %d.0;\n" % (texture_name, texture_name, self.get_size())]

    def _get_shader_code(self, uv, output_index, context):
        lod = -1.0 if output_index == 0 else float(self.get_parameter("lod"))
        return self._get_shader_code_lod(uv, output_index, context, lod)

    def get_output_attributes(self, output_index):
        return {"texture": "o%d_tex" % self.uid, "texture_size": "o%d_tex_size" % self.uid}

    def _deserialize(self, data):
        self.version = int(data.get("version", self.VERSION_OLD))

    def serialize(self):
        rv = super().serialize()
        if self.version != self.VERSION_OLD:
            rv["version"] = self.version
        return rv


class GenIterateBuffer(GenTexture):
    """Renders input 0 once, then input 1 `iterations` times, each pass reading the previous
    result through output 1 (o<id>_loop_tex). Output 0 (o<id>_tex) is the final result."""

    gen_type = "iterate_buffer"

    def get_parameter_defs(self):
        return [
            {"name": "size", "type": "size", "first": 4, "last": 13, "default": 4},
            {"name": "shrink", "type": "boolean", "default": False},
            {"name": "autostop", "type": "boolean", "default": False},
            {"name": "iterations", "type": "float", "min": 1, "max": 50, "step": 1, "default": 5.0},
            {"name": "filter", "type": "boolean", "default": True},
            {"name": "mipmap", "type": "boolean", "default": True},
        ]

    def get_input_defs(self):
        return [{"name": "in", "type": "rgba"}, {"name": "loop_in", "type": "rgba"}]

    def get_output_defs(self, show_hidden=False):
        return [{"type": "rgba"}, {"type": "rgba"}]

    def get_size(self):
        return int(math.pow(2, self.get_parameter("size")))

    def get_iterations(self):
        """Port of calculate_float_parameter("iterations"): a number, or an expression of named parameters."""
        value = self.get_parameter("iterations")
        if is_float(value):
            return max(0, int(value))
        if isinstance(value, str) and self.parent is not None and hasattr(self.parent, "get_named_parameters"):
            for name, np in self.parent.get_named_parameters().items():
                value = value.replace("$" + name, repr(float(np["value"])))
            try:
                return max(0, int(_safe_arithmetic(value)))
            except ValueError:
                self.project.warn("%r: cannot evaluate iterations %r" % (self, value))
        return 1

    def get_globals(self, texture_name):
        return ["uniform sampler2D %s;\nuniform float o%d_tex_size = %d.0;\nuniform float o%d_iteration = 0.0;\n" % (
            texture_name, self.uid, self.get_size(), self.uid)]

    def _get_shader_code(self, uv, output_index, context):
        return self._get_shader_code_lod(uv, output_index, context, -1.0, "_tex" if output_index == 0 else "_loop_tex")

    def get_output_attributes(self, output_index):
        attributes = {"texture": "o%d_tex" % self.uid if output_index == 0 else "o%d_loop_tex" % self.uid,
                      "texture_size": "o%d_tex_size" % self.uid}
        if output_index == 1:
            attributes["iteration"] = "o%d_iteration" % self.uid
        return attributes


class GenText(GenTexture):
    """Text drawn into a 2048x2048 texture (white on transparent, like MM's text renderer)."""

    gen_type = "text"
    TEXTURE_SIZE = 2048

    def get_parameter_defs(self):
        return [
            {"name": "text", "type": "string", "default": "Hello World"},
            {"name": "font", "type": "file", "filters": ["*.otf,*.ttf,*.fnt;Font file"], "default": ""},
            {"name": "font_size", "type": "float", "min": 0, "max": 128, "step": 1, "default": 32.0},
            {"name": "center", "type": "boolean", "default": False},
            {"name": "x", "type": "float", "min": -0.5, "max": 0.5, "step": 0.001, "default": 0.1},
            {"name": "y", "type": "float", "min": -0.5, "max": 0.5, "step": 0.001, "default": 0.1},
        ]

    def _float(self, name, default=0.0):
        value = self.get_parameter(name)
        if is_float(value):
            return float(value)
        try:
            return _safe_arithmetic(str(value))
        except ValueError:
            return default

    def text_settings(self):
        """Everything the renderer needs to draw the text (also its change signature)."""
        font = self.get_parameter("font") or ""
        font = font.replace("%PROJECT_PATH%", self.project.project_dir)
        return (str(self.get_parameter("text") or ""), font, int(self._float("font_size", 64.0)),
                self._float("x"), self._float("y"), bool(self.get_parameter("center")))


_ARITHMETIC_RE = re.compile(r"^[\s0-9.eE+\-*/()]*$")


def _safe_arithmetic(expression):
    """Evaluate a Godot Expression made only of numbers and + - * / ( )."""
    if not _ARITHMETIC_RE.match(expression):
        raise ValueError(expression)
    return float(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307 - whitelisted characters only


class GenImage(GenTexture):
    """Image file texture."""

    gen_type = "image"

    def get_parameter_defs(self):
        return [
            {"name": "image", "type": "image_path", "label": "", "default": ""},
            {"name": "fix_ar", "type": "boolean", "label": "Fix Aspect Ratio", "default": False},
            {"name": "clamp", "type": "boolean", "label": "Clamp", "default": False},
        ]

    def get_image_path(self):
        path = self.get_parameter("image") or ""
        return (path.replace("%PROJECT_PATH%", self.project.project_dir)
                .replace("%MATERIAL_MAKER_PATH%", self.project.mm_install_dir or ""))

    def get_adjusted_uv(self, uv):
        if self.get_parameter("fix_ar"):
            w, h = self.project.image_size(self.get_image_path())
            ar = (h / w) if w and h else 1.0
            uv = "((%s) - vec2(0.0, %f)) * vec2(1.0, %f)" % (uv, (1 - ar) / 2, 1 / ar)
        if self.get_parameter("clamp"):
            uv = "clamp(%s, 0.0, 1.0)" % uv
        return uv
