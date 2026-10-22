"""Loading .ptex projects into generator trees (port of loader.gd create_gen/add_to_gen_graph)."""

import os
import struct

from .generators import (
    GenBuffer,
    GenComment,
    GenDebug,
    GenGraph,
    GenImage,
    GenIOs,
    GenIterateBuffer,
    GenMaterial,
    GenRemote,
    GenReroute,
    GenSDF,
    GenShader,
    GenSwitch,
    GenText,
    GenUnsupported,
)
from .library import load_mm_file, parse_mm_json

# Same order as loader.gd: the first matching key path decides the generator class.
_GUESS = [
    (("shader_model", "preview_shader"), GenMaterial),
    (("connections",), GenGraph),
    (("nodes",), GenGraph),
    (("is_brush",), "brush"),
    (("shader_model",), GenShader),
    (("sdf_scene",), GenSDF),
    (("model_data",), GenShader),
    (("widgets",), GenRemote),
]

_TYPES = {
    "material_export": GenMaterial,
    "buffer": GenBuffer,
    "image": GenImage,
    "ios": GenIOs,
    "switch": GenSwitch,
    "comment": GenComment,
    "debug": GenDebug,
    "reroute": GenReroute,
    "graph": GenGraph,
    "remote": GenRemote,
    "shader": GenShader,
    # Not ported yet: rendered as 0 with a warning.
    "text": GenText,
    "iterate_buffer": GenIterateBuffer,
    "sdf": GenSDF,
    "export": "export",
}


class Project:
    """A loaded .ptex: the root graph plus everything the compiler needs to know about it."""

    def __init__(self, library, project_dir="", mm_install_dir=""):
        self.library = library
        self.project_dir = project_dir
        self.mm_install_dir = mm_install_dir
        self.root = None
        self.textures = {}
        self.warnings = []
        self._uid = 0
        self._changes = None

    def next_uid(self):
        self._uid += 1
        return self._uid

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def register_texture(self, texture_name, generator):
        self.textures[texture_name] = generator

    # Editing

    def record_change(self, generator, name, old, new):
        if self._changes is not None:
            self._changes.append((generator, name, old, new))

    def set_parameter(self, generator, name, value):
        """Set a parameter like the Material Maker UI does (remotes propagate to linked nodes).

        Returns True when the shaders must be regenerated, False when new uniform
        values (params.uniform_values) are enough.
        """
        from .params import needs_recompile

        self._changes = []
        try:
            generator.set_parameter(name, value)
            changes = self._changes
        finally:
            self._changes = None
        return any(needs_recompile(g, n, old, new) for g, n, old, new in changes)

    def set_seed(self, generator, value):
        """Change a node's (or a graph's) random seed; only uniforms change."""
        generator.seed_value = float(value)

    @staticmethod
    def image_size(path):
        """(width, height) of a PNG file, (0, 0) when unknown."""
        try:
            with open(path, "rb") as fh:
                header = fh.read(24)
            if header[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", header[16:24])
        except OSError:
            pass
        return 0, 0

    # Generator creation

    def _instantiate(self, cls):
        if isinstance(cls, str):
            return GenUnsupported(self, cls)
        return cls(self)

    def create_gen(self, data):
        generator = None
        for keys, cls in _GUESS:
            d = data
            for k in keys:
                if not isinstance(d, dict) or k not in d:
                    break
                d = d[k]
            else:
                generator = self._instantiate(cls)
                break
        if generator is None and "type" in data:
            t = data["type"]
            if t in _TYPES:
                generator = self._instantiate(_TYPES[t])
            elif self.library.has(t):
                generator = self.create_gen(self.library.get(t))
                if generator is not None:
                    generator.model = t
            else:
                self.warn("Unknown node type '%s' (node %s)" % (t, data.get("name", "?")))
                generator = GenUnsupported(self, t)
            generator.name = t
        if generator is None:
            self.warn("Cannot load node data: %s" % str(data)[:200])
            return None
        generator.deserialize(data)
        return generator

    def add_to_gen_graph(self, graph, nodes, connections):
        gennames = {}
        for n in nodes:
            g = self.create_gen(n)
            if g is None:
                continue
            orig_name = g.name
            if graph.add_generator(g):
                gennames[orig_name] = g.name
            else:
                self.warn("Cannot add node %s to graph %s" % (orig_name, graph.name or "<root>"))
        for c in connections:
            c = dict(c)
            c["from"] = gennames.get(c["from"], c["from"])
            c["to"] = gennames.get(c["to"], c["to"])
            if not graph.connect_children(graph.get_node(c["from"]), c["from_port"],
                                          graph.get_node(c["to"]), c["to_port"]):
                self.warn("Cannot connect %s:%d to %s:%d" % (c["from"], c["from_port"], c["to"], c["to_port"]))

    def enter_tree(self, generator=None):
        """Replay what Godot does when the loaded tree enters the scene (_enter_tree/_ready)."""
        generator = generator or self.root
        if isinstance(generator, GenRemote):
            generator.enter_tree()
        generator.init_parameters()
        for child in list(getattr(generator, "children", {}).values()):
            self.enter_tree(child)

    # Queries

    def iter_generators(self, generator=None):
        generator = generator or self.root
        yield generator
        for child in getattr(generator, "children", {}).values():
            yield from self.iter_generators(child)

    def find_material(self):
        """The top-level material node (named "Material" by Material Maker)."""
        if self.root is None:
            return None
        material = self.root.get_node("Material")
        if isinstance(material, GenMaterial):
            return material
        for c in self.root.children.values():
            if isinstance(c, GenMaterial):
                return c
        return None


def load_ptex(path_or_text, library, mm_install_dir="", project_dir=""):
    """Load a .ptex file (path) or JSON text into a Project.

    project_dir resolves %PROJECT_PATH% in image nodes when loading from text.
    """
    if path_or_text.lstrip().startswith("{"):
        data = parse_mm_json(path_or_text)
    else:
        data = load_mm_file(path_or_text)
        project_dir = project_dir or os.path.dirname(os.path.abspath(path_or_text))
    project = Project(library, project_dir, mm_install_dir)
    root = project.create_gen(data)
    if not isinstance(root, GenGraph):
        raise ValueError("The .ptex root is not a graph")
    project.root = root
    project.enter_tree()
    return project
