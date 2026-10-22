"""Structural edits of a generator graph: what Material Maker's graph editor does.

Every function edits the project in place; the caller then serializes it
(serialize.project_to_ptex) and rebuilds what depends on it.
"""

import copy

from .generators import GenGraph, GenIterateBuffer, GenSwitch
from .serialize import serialize_project  # noqa: F401  (re-exported for callers)

PROTECTED = ("Material", "Brush", "gen_inputs", "gen_outputs", "gen_parameters")


class EditError(ValueError):
    pass


def resolve_graph(project, graph_path):
    """The GenGraph at "", "graph_3", "graph_3/graph_1", ..."""
    graph = project.root
    for part in [p for p in graph_path.split("/") if p]:
        graph = graph.get_node(part)
        if not isinstance(graph, GenGraph):
            raise EditError("%s is not a graph" % graph_path)
    return graph


def _slot_type(project, type_name):
    return project.library.io_types.get(type_name, {}).get("slot_type", 0)


def can_connect(project, from_gen, from_port, to_gen, to_port):
    """Port of MMGenGraph.check_input_connects: slot types match, or the source is "any"."""
    outputs = from_gen.get_output_defs()
    inputs = to_gen.get_input_defs()
    if not (0 <= from_port < len(outputs) and 0 <= to_port < len(inputs)):
        return False
    out_type, in_type = outputs[from_port].get("type", "rgba"), inputs[to_port].get("type", "rgba")
    out_slot, in_slot = _slot_type(project, out_type), _slot_type(project, in_type)
    return out_slot == in_slot or out_slot == 42 or in_slot == 42


def _follow_input(gen, input_index):
    """Outputs that depend on an input (follow_input in MM)."""
    if isinstance(gen, GenIterateBuffer) and input_index == 1:
        return [0]
    if isinstance(gen, GenSwitch):
        return [input_index % int(gen.get_parameter("outputs"))]
    return list(range(len(gen.get_output_defs())))


def creates_loop(graph, from_name, from_port, to_name, to_port):
    """Would from.out(from_port) -> to.in(to_port) close a cycle inside this graph?"""
    todo = [(to_name, o) for o in _follow_input(graph.get_node(to_name), to_port)]
    seen = set()
    while todo:
        name, port = todo.pop()
        if (name, port) in seen:
            continue
        seen.add((name, port))
        if name == from_name and port == from_port:
            return True
        for c in graph.connections:
            if c["from"] == name and int(c["from_port"]) == port and c["to"] in graph.children:
                for o in _follow_input(graph.get_node(c["to"]), int(c["to_port"])):
                    todo.append((c["to"], o))
    return False


def connect(project, graph, from_name, from_port, to_name, to_port):
    from_gen, to_gen = graph.get_node(from_name), graph.get_node(to_name)
    if from_gen is None or to_gen is None:
        raise EditError("Unknown node")
    if not can_connect(project, from_gen, from_port, to_gen, to_port):
        raise EditError("Incompatible ports: %s.%d -> %s.%d" % (from_name, from_port, to_name, to_port))
    if creates_loop(graph, from_name, from_port, to_name, to_port):
        raise EditError("This link would create a loop")
    graph.connect_children(from_gen, from_port, to_gen, to_port)


def disconnect(graph, to_name, to_port):
    graph.connections = [c for c in graph.connections
                         if not (c["to"] == to_name and int(c["to_port"]) == int(to_port))]


def remove_node(graph, name):
    if name in PROTECTED:
        raise EditError("%s cannot be deleted" % name)
    if name not in graph.children:
        return
    del graph.children[name]
    graph.connections = [c for c in graph.connections if c["from"] != name and c["to"] != name]
    graph.fix_remotes()


def _insert(project, graph, data, position):
    data = dict(data)
    data["node_position"] = {"x": float(position[0]), "y": float(position[1])}
    gen = project.create_gen(data)
    if gen is None:
        raise EditError("Cannot create node %s" % data.get("type"))
    if not graph.add_generator(gen):
        raise EditError("Cannot add %s here" % gen.name)
    project.enter_tree(gen)
    return gen


def add_node(project, graph, node_data, position=(0.0, 0.0)):
    """Add a node from a library entry (core.library NodeLibrary.menu) or {"type": ...}."""
    data = copy.deepcopy(node_data)
    data.setdefault("name", data.get("type", "node"))
    return _insert(project, graph, data, position)


def duplicate_node(project, graph, name, position):
    src = graph.get_node(name)
    if src is None or name in PROTECTED:
        raise EditError("%s cannot be duplicated" % name)
    data = src.serialize()
    data.pop("seed_int", None)   # a copy gets its own seed, from its position
    data.pop("seed", None)
    return _insert(project, graph, data, position)


def set_positions(graph, positions):
    """positions: {node name: (x, y)} in Material Maker coordinates."""
    for name, (x, y) in positions.items():
        gen = graph.get_node(name)
        if gen is not None:
            gen.position = (float(x), float(y))
