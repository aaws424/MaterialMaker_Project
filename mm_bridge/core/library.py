"""Material Maker node library: the .mmg node definitions and io types."""

import copy
import json
import os

# Keys whose values Material Maker stores as arrays of lines in files (loader.gd).
_MULTILINE_PROCESS_ITEMS = ("code", "custom", "global", "instance", "preview_shader", "template")
_MULTILINE_WALK_ITEMS = ("shader_model", "nodes", "template", "files")
_MULTILINE_WALK_CHILDREN = ("exports",)

_COMMON_GLSL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.glsl")


def _join_multiline_strings(data, walk_children=False):
    """Port of loader.gd replace_arrays_with_multiline_strings."""
    if isinstance(data, dict):
        for k in list(data.keys()):
            if k in _MULTILINE_PROCESS_ITEMS:
                if isinstance(data[k], list):
                    data[k] = "\n".join(data[k])
            elif walk_children or k in _MULTILINE_WALK_ITEMS:
                data[k] = _join_multiline_strings(data[k])
            elif k in _MULTILINE_WALK_CHILDREN:
                data[k] = _join_multiline_strings(data[k], True)
    elif isinstance(data, list):
        for i in range(len(data)):
            data[i] = _join_multiline_strings(data[i])
    return data


def parse_mm_json(text):
    """Parse a .ptex/.mmg file the way Godot does: every JSON number is a float."""
    data = json.loads(text, parse_int=float)
    if not isinstance(data, dict):
        raise ValueError("Material Maker file root must be an object")
    return _join_multiline_strings(data)


def load_mm_file(path):
    with open(path, encoding="utf-8") as fh:
        return parse_mm_json(fh.read())


class NodeLibrary:
    """Predefined generators (nodes/*.mmg) plus io types, loaded from an MM install."""

    def __init__(self, nodes_dirs):
        if isinstance(nodes_dirs, str):
            nodes_dirs = [nodes_dirs]
        self.nodes_dirs = [d for d in nodes_dirs if os.path.isdir(d)]
        if not self.nodes_dirs:
            raise FileNotFoundError("No Material Maker nodes directory found in %r" % (nodes_dirs,))
        self.generators = {}
        self.errors = {}
        for d in self.nodes_dirs:
            for file_name in sorted(os.listdir(d)):
                if not file_name.endswith(".mmg"):
                    continue
                node_name = file_name[:-4]
                if node_name.startswith("website_"):
                    node_name = "website:" + node_name[8:]
                try:
                    self.generators[node_name] = load_mm_file(os.path.join(d, file_name))
                except (ValueError, OSError) as e:
                    self.errors[node_name] = str(e)
        self.io_types = self._load_io_types()
        self.menu = self._load_menu()
        with open(_COMMON_GLSL_PATH, encoding="utf-8") as fh:
            self.common_glsl = fh.read()

    @classmethod
    def from_install(cls, mm_install_dir):
        return cls([os.path.join(mm_install_dir, "nodes")])

    def _load_io_types(self):
        for d in self.nodes_dirs:
            path = os.path.join(d, "io_types.mmt")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    type_list = json.loads(fh.read())
                # Dict order matters: the generator tests output keys in this order.
                return {t["name"]: t for t in type_list}
        raise FileNotFoundError("io_types.mmt not found in %r" % (self.nodes_dirs,))

    def _load_menu(self):
        """Library menu: [(path like "Filter/Blur/Gaussian", node data)] from library/base.json.

        Entries carry everything needed to create the node: a type and preset parameters, or a
        whole graph / shader model, exactly as Material Maker's library stores them.
        """
        entries = []
        for d in self.nodes_dirs:
            path = os.path.join(os.path.dirname(d), "library", "base.json")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                data = _join_multiline_strings(json.loads(fh.read(), parse_int=float))
            for item in data.get("lib", []):
                if item.get("type") and item.get("tree_item"):
                    node = {k: v for k, v in item.items() if k not in ("icon", "icon_data", "tree_item")}
                    entries.append((item["tree_item"], node))
            break
        if not entries:  # no library file: every node by name
            entries = [("Nodes/" + name, {"type": name, "name": name}) for name in sorted(self.generators)]
        return entries

    def has(self, name):
        return name in self.generators

    def get(self, name):
        """A private copy of a predefined generator description."""
        return copy.deepcopy(self.generators[name])

    def get_predefined_global(self, name):
        g = self.generators.get(name)
        if not g or "shader_model" not in g:
            return ""
        return g["shader_model"].get("global", "")

    def get_material_nodes(self):
        return sorted(k for k, g in self.generators.items() if g.get("type") == "material_export")
