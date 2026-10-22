"""Material Maker graph editor inside Blender's node editor.

A "Material Maker Graph" node tree mirrors one level of a material's generator graph.
The project (session.project) stays the source of truth: the tree is rebuilt from it after
every structural change. Edits made in the node editor (new or removed links, deleted or
duplicated nodes) only mark the tree; the session timer then applies them to the project
with Material Maker's rules, commits (re-serializes) and rebuilds the view. Node positions
are copied back into the project whenever it is serialized.
"""

import bpy
from bpy_extras.io_utils import ExportHelper

from . import library_cache, session, ui
from .core import graph_edit as G
from .core import schema as S
from .core.generators import GenComment, GenGraph, GenIOs, GenMaterial, GenRemote

TREE_TYPE = "MMB_NodeTree"
NODE_TYPE = "MMB_GraphNode"

_building = 0            # >0 while the add-on itself (re)builds a tree
_trees_edited = False    # set by NodeTree.update(); the timer finds and diffs the trees
# Blender nodes show parameters inline and are taller than Material Maker's: spread the layout.
SCALE_X, SCALE_Y = 1.3, 1.8


# --------------------------------------------------------------------------------------
# Sockets: one class per Material Maker io type, colored like Material Maker's ports
# --------------------------------------------------------------------------------------

_IO_TYPES = {  # name: (label, color) from nodes/io_types.mmt
    "f": ("Greyscale", (0.5, 0.5, 0.5, 1.0)), "rgb": ("Color", (0.5, 0.5, 1.0, 1.0)),
    "rgba": ("RGBA", (0.0, 0.5, 0.0, 1.0)), "sdf2d": ("SDF2D", (1.0, 0.5, 0.0, 1.0)),
    "sdf3d": ("SDF3D", (0.5, 0.0, 0.0, 1.0)), "sdf3dc": ("SDF3D-C", (1.0, 0.0, 0.0, 1.0)),
    "tex3d_gs": ("Greyscale TEX3D", (0.8, 0.0, 1.0, 1.0)), "tex3d": ("TEX3D", (1.0, 0.0, 1.0, 1.0)),
    "v4v4": ("V4->V4", (0.6, 0.4, 0.1, 1.0)), "fill": ("Fill", (0.86, 0.73, 0.53, 1.0)),
    "any": ("Any", (1.0, 1.0, 1.0, 1.0)),
}


def socket_idname(io_type):
    return "MMB_Socket_" + (io_type if io_type in _IO_TYPES else "any")


def _make_socket_class(io_type, label, color):
    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return color

    return type("MMB_Socket_" + io_type, (bpy.types.NodeSocket,), {
        "bl_idname": "MMB_Socket_" + io_type,
        "bl_label": label,
        "draw": draw,
        "draw_color": draw_color,
        "draw_color_simple": classmethod(lambda cls: color),
    })


_SOCKET_CLASSES = tuple(_make_socket_class(t, label, color) for t, (label, color) in _IO_TYPES.items())


# --------------------------------------------------------------------------------------
# Tree and node
# --------------------------------------------------------------------------------------

class MMB_NodeTree(bpy.types.NodeTree):
    """Material Maker graph of a material (edit nodes and links; parameters re-render live)"""

    bl_idname = TREE_TYPE
    bl_label = "Material Maker Graph"
    bl_icon = "NODE_MATERIAL"

    mmb_material: bpy.props.PointerProperty(type=bpy.types.Material)
    mmb_path: bpy.props.StringProperty(description="Graph shown: \"\" for the material's graph, or a group path")

    def update(self):
        # Blender also calls this while classes are being registered (e.g. enabling the add-on on a
        # file that contains graph trees), when reading this tree's own properties crashes Blender
        # 5.2. So: touch no Blender data here, only flag; the timer finds the edited trees.
        global _trees_edited
        if not _building:
            _trees_edited = True
            session.request_tick()


def _node_session(node):
    tree = node.id_data
    material = tree.mmb_material
    if material is None:
        return None, None, None
    s = session._sessions.get(material.mmb.uid)
    if not isinstance(s, session.Session):
        return material, None, None
    return material, s, tree.mmb_path + node.gen_name


class MMB_GraphNode(bpy.types.Node):
    """A Material Maker node"""

    bl_idname = NODE_TYPE
    bl_label = "Material Maker Node"
    bl_width_default = 200

    gen_name: bpy.props.StringProperty()
    is_group: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE_TYPE

    def draw_buttons(self, context, layout):
        material, s, gen_path = _node_session(self)
        if self.is_group:
            op = layout.operator("mm_bridge.graph_enter", text="Open Group", icon="NODETREE")
            op.gen_name = self.gen_name
        if s is None:
            return
        specs = s.specs_by_path.get(gen_path, [])
        params = s.params(material)
        if params is not None and specs:
            ui._draw_specs(layout.column(), material, params, specs)

    def draw_label(self):
        return self.label or self.gen_name


# --------------------------------------------------------------------------------------
# Building the view from the project, and applying the user's edits back
# --------------------------------------------------------------------------------------

def _port_label(d):
    label = d.get("shortdesc") or d.get("label") or d.get("name", "")
    if label == d.get("name") and label.endswith("_tex"):   # material inputs: "albedo_tex" -> "Albedo"
        label = label[:-4]
    return label.replace("_", " ").title() if label == label.lower() else label


def _shown(gen):
    return not isinstance(gen, GenComment)


def _graph_of(tree, s):
    try:
        return G.resolve_graph(s.project, tree.mmb_path)
    except G.EditError:
        tree.mmb_path = ""
        return s.project.root


def build_tree(tree, s):
    """Recreate the tree's nodes and links from the project graph at tree.mmb_path."""
    global _building
    _building += 1
    try:
        graph = _graph_of(tree, s)
        tree.nodes.clear()
        nodes = {}
        for name, gen in graph.children.items():
            if not _shown(gen):
                continue
            node = tree.nodes.new(NODE_TYPE)
            node.name = name
            node.gen_name = name
            node.label = S.node_label(gen)
            node.is_group = isinstance(gen, GenGraph) and gen.model is None
            node.location = (gen.position[0] * SCALE_X, -gen.position[1] * SCALE_Y)
            for i, d in enumerate(gen.get_input_defs()):
                node.inputs.new(socket_idname(d.get("type")), _port_label(d), identifier="i%d" % i)
            # The material node's outputs are its export channels, hidden in Material Maker too.
            outputs = [] if isinstance(gen, GenMaterial) else gen.get_output_defs()
            for o, d in enumerate(outputs):
                node.outputs.new(socket_idname(d.get("type")), d.get("shortdesc") or d.get("name") or "",
                                 identifier="o%d" % o)
            if isinstance(gen, (GenMaterial, GenIOs, GenRemote)):
                node.use_custom_color = True
                node.color = {GenMaterial: (0.35, 0.25, 0.12), GenIOs: (0.15, 0.25, 0.35),
                              GenRemote: (0.25, 0.18, 0.32)}[type(gen)]
            nodes[name] = node
        for c in graph.connections:
            a, b = nodes.get(c["from"]), nodes.get(c["to"])
            fp, tp = int(c["from_port"]), int(c["to_port"])
            if a is not None and b is not None and fp < len(a.outputs) and tp < len(b.inputs):
                tree.links.new(a.outputs[fp], b.inputs[tp])
        _built_signatures[tree.name] = _tree_signature(tree)
    finally:
        _building -= 1


def read_positions(tree, s):
    """Copy node locations back into the project (Material Maker y points down)."""
    try:
        graph = G.resolve_graph(s.project, tree.mmb_path)
    except G.EditError:
        return
    G.set_positions(graph, {n.gen_name: (n.location.x / SCALE_X, -n.location.y / SCALE_Y)
                            for n in tree.nodes if n.bl_idname == NODE_TYPE and n.gen_name in graph.children})


def _port(socket):
    return int(socket.identifier[1:])


def apply_tree_edits(tree, s, report=print):
    """Diff the tree against the project graph and apply the user's edits. Returns True if any."""
    graph = _graph_of(tree, s)
    read_positions(tree, s)
    changed = False
    mm_nodes = [n for n in tree.nodes if n.bl_idname == NODE_TYPE]

    # Duplicated nodes (Shift+D) carry the gen_name of their original under a new node name.
    claimed = set()
    for node in sorted(mm_nodes, key=lambda n: n.name != n.gen_name):
        if node.gen_name in claimed or node.gen_name not in graph.children:
            if node.gen_name in graph.children:
                try:
                    gen = G.duplicate_node(s.project, graph, node.gen_name,
                                           (node.location.x / SCALE_X, -node.location.y / SCALE_Y))
                    node.gen_name = gen.name
                    changed = True
                except G.EditError as e:
                    report(str(e))
            continue
        claimed.add(node.gen_name)

    present = {n.gen_name for n in mm_nodes}
    for name, gen in list(graph.children.items()):
        if _shown(gen) and name not in present:
            try:
                G.remove_node(graph, name)
                changed = True
            except G.EditError as e:
                report(str(e))   # protected node: the rebuild brings it back
                changed = True

    by_name = {n.gen_name: n for n in mm_nodes}
    current = set()
    for link in tree.links:
        a, b = link.from_node, link.to_node
        if a.bl_idname == NODE_TYPE and b.bl_idname == NODE_TYPE:
            current.add((a.gen_name, _port(link.from_socket), b.gen_name, _port(link.to_socket)))
    expected = {(c["from"], int(c["from_port"]), c["to"], int(c["to_port"])) for c in graph.connections
                if c["from"] in by_name and c["to"] in by_name}
    for (f, fp, t, tp) in expected - current:
        G.disconnect(graph, t, tp)
        changed = True
    for (f, fp, t, tp) in current - expected:
        try:
            G.connect(s.project, graph, f, fp, t, tp)
        except G.EditError as e:
            report(str(e))   # the rebuild removes the rejected link
        changed = True
    return changed


def _tree_session(tree):
    material = tree.mmb_material
    if material is None or not material.mmb.is_mm:
        return None, None
    s = session.get_session(material)
    return material, (s if isinstance(s, session.Session) else None)


def _tree_signature(tree):
    """What a user edit can change: nodes (and their copies) and links."""
    nodes = tuple(sorted((n.name, n.gen_name) for n in tree.nodes if n.bl_idname == NODE_TYPE))
    links = tuple(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                         for l in tree.links))
    return nodes, links


_built_signatures = {}   # tree name -> signature right after the add-on built it


def process_dirty_trees():
    """Session timer hook: apply the user's edits to graph trees."""
    global _trees_edited
    if not _trees_edited:
        return False
    _trees_edited = False
    for tree in [t for t in bpy.data.node_groups if t.bl_idname == TREE_TYPE]:
        if _built_signatures.get(tree.name) == _tree_signature(tree):
            continue   # nothing the user did in this tree
        material, s = _tree_session(tree)
        if s is None:
            continue
        messages = []
        try:
            if apply_tree_edits(tree, s, messages.append):
                s = s.commit(material)   # the commit hook rebuilds the view
            elif messages:
                build_tree(tree, s)      # e.g. a rejected link: show the project's state again
        except Exception as e:
            messages.append("%s: %s" % (type(e).__name__, e))
            build_tree(tree, s)
        s.notice = "; ".join(messages)   # shown in the sidebar until the next graph edit
    return True


def _on_commit(material, s):
    """Rebuild every graph view of a material after its project changed."""
    for tree in bpy.data.node_groups:
        if tree.bl_idname == TREE_TYPE and tree.mmb_material is not None:
            if material is None or tree.mmb_material == material:
                t_material, t_session = _tree_session(tree)
                if t_session is not None:
                    build_tree(tree, t_session)


# --------------------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------------------

def _context_material(context):
    tree = getattr(context.space_data, "edit_tree", None) if context.space_data else None
    if tree is not None and tree.bl_idname == TREE_TYPE:
        return tree.mmb_material, tree
    material = getattr(context, "material", None) or (context.object.active_material if context.object else None)
    return material, None


def ensure_tree(material):
    tree = material.mmb.graph_tree
    if tree is None or tree.bl_idname != TREE_TYPE:
        tree = bpy.data.node_groups.new("MM Graph: " + material.name, TREE_TYPE)
        tree.use_fake_user = True   # kept with the file even when no editor shows it
        tree.mmb_material = material
        material.mmb.graph_tree = tree
    return tree


class MMBRIDGE_OT_edit_graph(bpy.types.Operator):
    """Edit the Material Maker graph of this material in a node editor"""

    bl_idname = "mm_bridge.edit_graph"
    bl_label = "Edit Graph"

    @classmethod
    def poll(cls, context):
        material, _tree = _context_material(context)
        return material is not None and material.mmb.is_mm

    def execute(self, context):
        material, _tree = _context_material(context)
        s = session.get_session(material)
        if not isinstance(s, session.Session):
            self.report({"ERROR"}, "The material could not be loaded")
            return {"CANCELLED"}
        tree = ensure_tree(material)
        build_tree(tree, s)
        area = next((a for a in context.screen.areas if a.type == "NODE_EDITOR"), None)
        if area is None:
            bpy.ops.wm.window_new()
            area = bpy.context.window_manager.windows[-1].screen.areas[0]
        area.ui_type = TREE_TYPE
        area.spaces.active.node_tree = tree
        return {"FINISHED"}


class _TreeOperator:
    @classmethod
    def poll(cls, context):
        tree = getattr(context.space_data, "edit_tree", None) if context.space_data else None
        return tree is not None and tree.bl_idname == TREE_TYPE and tree.mmb_material is not None


def _rebuild(context):
    tree = context.space_data.edit_tree
    material, s = _tree_session(tree)
    if s is not None:
        read_positions(tree, s)
        build_tree(tree, s)


class MMBRIDGE_OT_graph_enter(_TreeOperator, bpy.types.Operator):
    """Show the nodes inside this group"""

    bl_idname = "mm_bridge.graph_enter"
    bl_label = "Open Group"

    gen_name: bpy.props.StringProperty()

    def execute(self, context):
        tree = context.space_data.edit_tree
        material, s = _tree_session(tree)
        if s is None:
            return {"CANCELLED"}
        read_positions(tree, s)
        tree.mmb_path = tree.mmb_path + self.gen_name + "/"
        build_tree(tree, s)
        return {"FINISHED"}


class MMBRIDGE_OT_graph_up(_TreeOperator, bpy.types.Operator):
    """Back to the graph containing this group"""

    bl_idname = "mm_bridge.graph_up"
    bl_label = "Parent Graph"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and context.space_data.edit_tree.mmb_path != ""

    def execute(self, context):
        tree = context.space_data.edit_tree
        material, s = _tree_session(tree)
        if s is not None:
            read_positions(tree, s)
        parts = [p for p in tree.mmb_path.split("/") if p]
        tree.mmb_path = "".join(p + "/" for p in parts[:-1])
        if s is not None:
            build_tree(tree, s)
        return {"FINISHED"}


_menu_items_cache = []


def _menu_items(self, context):
    global _menu_items_cache
    try:
        menu = library_cache.get_library(context).menu
    except Exception:
        menu = []
    # Blender needs the strings kept alive while the enum is shown.
    _menu_items_cache = [(str(i), path, "") for i, (path, _data) in enumerate(menu)] or [("-1", "(no library)", "")]
    return _menu_items_cache


class MMBRIDGE_OT_add_node(_TreeOperator, bpy.types.Operator):
    """Add a node from Material Maker's library"""

    bl_idname = "mm_bridge.add_node"
    bl_label = "Add Material Maker Node"
    bl_property = "item"

    item: bpy.props.EnumProperty(name="Node", items=_menu_items)

    def invoke(self, context, event):
        if self.properties.is_property_set("item"):
            return self.execute(context)
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        index = int(self.item)
        tree = context.space_data.edit_tree
        material, s = _tree_session(tree)
        menu = library_cache.get_library(context).menu
        if s is None or not 0 <= index < len(menu):
            return {"CANCELLED"}
        read_positions(tree, s)
        location = context.space_data.cursor_location
        try:
            graph = G.resolve_graph(s.project, tree.mmb_path)
            gen = G.add_node(s.project, graph, menu[index][1], (location.x / SCALE_X, -location.y / SCALE_Y))
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        s = s.commit(material)   # rebuilds the tree through the commit hook
        node = tree.nodes.get(gen.name)
        if node is not None:
            for n in tree.nodes:
                n.select = False
            node.select = True
            tree.nodes.active = node
        return {"FINISHED"}


class MMBRIDGE_OT_save_ptex(bpy.types.Operator, ExportHelper):
    """Save the material's graph, with its current parameters, as a Material Maker file"""

    bl_idname = "mm_bridge.save_ptex"
    bl_label = "Save as Material Maker File"

    filename_ext = ".ptex"
    filter_glob: bpy.props.StringProperty(default="*.ptex", options={"HIDDEN"})
    use_as_source: bpy.props.BoolProperty(
        name="Link to This File", default=True,
        description="Make this the material's file (Reload from File reads it)")

    @classmethod
    def poll(cls, context):
        material, _tree = _context_material(context)
        return material is not None and material.mmb.is_mm

    def invoke(self, context, event):
        material, _tree = _context_material(context)
        if material.mmb.filepath and not self.filepath:
            self.filepath = bpy.path.abspath(material.mmb.filepath)
        return super().invoke(context, event)

    def execute(self, context):
        from .core.serialize import project_to_ptex

        material, _tree = _context_material(context)
        s = session.get_session(material)
        if not isinstance(s, session.Session):
            return {"CANCELLED"}
        tree = material.mmb.graph_tree
        if tree is not None and tree.bl_idname == TREE_TYPE:
            read_positions(tree, s)
        import os
        text = project_to_ptex(s.project, os.path.splitext(os.path.basename(self.filepath))[0])
        with open(self.filepath, "w", encoding="utf-8") as fh:
            fh.write(text)
        if self.use_as_source:
            material.mmb.filepath = self.filepath
        self.report({"INFO"}, "Saved %s" % self.filepath)
        return {"FINISHED"}


class MMBRIDGE_OT_graph_sync(_TreeOperator, bpy.types.Operator):
    """Rebuild the graph view from the material"""

    bl_idname = "mm_bridge.graph_sync"
    bl_label = "Refresh Graph"

    def execute(self, context):
        _rebuild(context)
        return {"FINISHED"}


# --------------------------------------------------------------------------------------
# Add menu and sidebar
# --------------------------------------------------------------------------------------

class MMBRIDGE_MT_add_category(bpy.types.Menu):
    bl_idname = "MMBRIDGE_MT_add_category"
    bl_label = "Material Maker Nodes"

    def draw(self, context):
        category = getattr(context, "mmb_category", "")
        try:
            menu = library_cache.get_library(context).menu
        except Exception:
            return
        prefix = category + "/"
        for i, (path, _data) in enumerate(menu):
            if path.startswith(prefix):
                op = self.layout.operator("mm_bridge.add_node", text=path[len(prefix):])
                op.item = str(i)


def draw_add_menu(self, context):
    if getattr(context.space_data, "tree_type", "") != TREE_TYPE:
        return
    layout = self.layout
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator("mm_bridge.add_node", text="Search Material Maker Nodes...", icon="VIEWZOOM")
    layout.separator()
    try:
        menu = library_cache.get_library(context).menu
    except Exception as e:
        layout.label(text=str(e), icon="ERROR")
        return
    categories = []
    for path, _data in menu:
        top = path.split("/")[0]
        if top not in categories:
            categories.append(top)
    for category in categories:
        row = layout.row()
        if hasattr(row, "context_string_set"):
            row.context_string_set("mmb_category", category)
            row.menu(MMBRIDGE_MT_add_category.bl_idname, text=category)


class MMBRIDGE_PT_graph(bpy.types.Panel):
    bl_label = "Material Maker"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Material Maker"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", "") == TREE_TYPE and context.space_data.edit_tree is not None

    def draw(self, context):
        tree = context.space_data.edit_tree
        layout = self.layout
        material = tree.mmb_material
        if material is None:
            layout.label(text="Not linked to a material", icon="ERROR")
            return
        row = layout.row(align=True)
        row.label(text=material.name, icon="MATERIAL")
        row.operator("mm_bridge.graph_sync", text="", icon="FILE_REFRESH")
        row = layout.row(align=True)
        row.operator("mm_bridge.graph_up", text="", icon="FILE_PARENT")
        row.label(text="/" + tree.mmb_path if tree.mmb_path else "Material graph")
        layout.operator("mm_bridge.save_ptex", icon="FILE_TICK")
        s = session._sessions.get(material.mmb.uid)
        for message, alert in ((getattr(s, "notice", ""), False), (getattr(s, "error", ""), True)):
            if message:
                box = layout.box()
                box.alert = alert
                box.label(text=message[:200], icon="ERROR" if alert else "INFO")
        node = tree.nodes.active
        if isinstance(s, session.Session) and node is not None and node.bl_idname == NODE_TYPE:
            box = layout.box()
            box.label(text=node.draw_label(), icon="NODE")
            specs = s.specs_by_path.get(tree.mmb_path + node.gen_name, [])
            params = s.params(material)
            if specs and params is not None:
                ui._draw_specs(box.column(), material, params, specs)
            else:
                box.label(text="No parameters")


classes = _SOCKET_CLASSES + (
    MMB_NodeTree,
    MMB_GraphNode,
    MMBRIDGE_OT_edit_graph,
    MMBRIDGE_OT_graph_enter,
    MMBRIDGE_OT_graph_up,
    MMBRIDGE_OT_add_node,
    MMBRIDGE_OT_save_ptex,
    MMBRIDGE_OT_graph_sync,
    MMBRIDGE_MT_add_category,
    MMBRIDGE_PT_graph,
)


def register():
    session.tick_hooks.append(process_dirty_trees)
    session.commit_hooks.append(_on_commit)
    bpy.types.NODE_MT_add.append(draw_add_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(draw_add_menu)
    for hooks, fn in ((session.tick_hooks, process_dirty_trees), (session.commit_hooks, _on_commit)):
        if fn in hooks:
            hooks.remove(fn)
    _built_signatures.clear()
