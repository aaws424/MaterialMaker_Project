"""Write a project back to .ptex, the way Material Maker saves it (loader.gd save_gen)."""

import copy
import json

from .library import _MULTILINE_PROCESS_ITEMS, _MULTILINE_WALK_CHILDREN, _MULTILINE_WALK_ITEMS


def _split_multiline_strings(data, walk_children=False):
    """Port of replace_multiline_strings_with_arrays: code sections become arrays of lines."""
    if isinstance(data, dict):
        for k in list(data.keys()):
            if k in _MULTILINE_PROCESS_ITEMS:
                if isinstance(data[k], str):
                    data[k] = data[k].replace("    ", "\t")
                    if "\n" in data[k]:
                        data[k] = data[k].split("\n")
            elif walk_children or k in _MULTILINE_WALK_ITEMS:
                data[k] = _split_multiline_strings(data[k])
            elif k in _MULTILINE_WALK_CHILDREN:
                data[k] = _split_multiline_strings(data[k], True)
    elif isinstance(data, list):
        for i in range(len(data)):
            data[i] = _split_multiline_strings(data[i])
    return data


def serialize_project(project, name=None):
    """The root graph as a .ptex dictionary."""
    data = project.root.serialize()
    if name is not None:
        data["name"] = name
    data["node_position"] = {"x": 0, "y": 0}
    return data


def dumps_ptex(data):
    """JSON text like Godot's JSON.print(data, "\\t", true) after splitting multiline strings."""
    return json.dumps(_split_multiline_strings(copy.deepcopy(data)), indent="\t", sort_keys=True,
                      ensure_ascii=False)


def project_to_ptex(project, name=None):
    return dumps_ptex(serialize_project(project, name))
