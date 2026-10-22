"""Live sessions: one per Material Maker material in the open file.

A session owns the loaded generator tree, the compiled program and its GPU
renderer. Property update callbacks only queue the changed keys; a timer applies
them, recompiles when Material Maker's rules require it, and renders:
at preview resolution while values keep changing, then at full resolution once
they have settled.
"""

import os
import threading
import time
import traceback
import uuid

import bpy

from . import helpers, library_cache, material_builder, props
from .core import compile_material, load_ptex
from .core import schema as S
from .core.generators import GenMaterial
from .core.params import uniform_values
from .gpu_render import images as image_utils

SETTLE_SECONDS = 0.3      # no change for this long -> full resolution
RECOMPILE_DELAY = 0.15    # coalesce rapid structural changes
FAST_FULL_RENDER = 0.08   # full renders faster than this skip the preview step
TICK_BUSY = 0.02
TICK_IDLE = 0.1

_sessions = {}            # uid -> Session


class Session:
    def __init__(self, material):
        settings = material.mmb
        self.uid = settings.uid
        self.material_name = material.name
        self.error = ""
        self.notice = ""   # graph editor feedback (e.g. a refused link)
        self.pending = set()
        self.recompile = False
        self.dirty = True
        self.preview_done = False
        self.last_change = 0.0
        self.last_full_seconds = 1.0
        self.last_render = {}
        self.renderer = None
        library = library_cache.get_library()
        project_dir = os.path.dirname(bpy.path.abspath(settings.filepath)) if settings.filepath else ""
        self.project = load_ptex(settings.source, library, library_cache.get_install_dir(), project_dir=project_dir)
        self.source_text = settings.source
        self.sections = S.build_schema(self.project)
        self.specs = {spec.key: spec for section in self.sections for spec in section.specs}
        self.specs_by_path = {}
        for spec in self.specs.values():
            self.specs_by_path.setdefault(spec.gen_path, []).append(spec)
        attr = settings.schema_attr or S.schema_attr(settings.uid)
        props.ensure_schema_class(attr, self.sections)
        if settings.schema_attr != attr:
            settings.schema_attr = attr
        self.attr = attr
        self.time = 0.0
        self.helper_signatures = {}
        self._ensure_helpers(material)
        # Values saved in the file (or changed before this session existed) override the .ptex.
        for key in self.specs:
            self._apply_key(material, key)
        self.program = compile_material(self.project)
        self.uses_time = _uses_time(self.program)

    def _ensure_helpers(self, material):
        """Color Ramp / Float Curve nodes holding gradient and curve parameters."""
        if material.node_tree is None:
            material.use_nodes = True
        for section in self.sections:
            for spec in section.specs:
                if spec.kind not in (S.GRADIENT, S.CURVE):
                    continue
                node = helpers.find(material, spec.key)
                if node is None:
                    node = helpers.create(material, spec.key, spec.kind, "%s: %s" % (section.label, spec.label))
                    helpers.write(node, spec.value)
                self.helper_signatures[spec.key] = helpers.signature(node)

    def check_helpers(self, material):
        """Queue gradient/curve parameters whose helper node was edited. Returns True if any was."""
        changed = False
        for key, old in self.helper_signatures.items():
            node = helpers.find(material, key)
            if node is None:
                continue
            sig = helpers.signature(node)
            if sig != old:
                self.helper_signatures[key] = sig
                self.pending.add(key)
                changed = True
        return changed

    def animated_keys(self, material):
        """Parameters of this material driven by keyframes (they change without update callbacks)."""
        prefix = self.attr + "."
        return {path[len(prefix):] for path in _fcurve_paths(material) if path.startswith(prefix)} & set(self.specs)

    # Lookup

    def material(self):
        material = bpy.data.materials.get(self.material_name)
        if material is not None and material.mmb.uid == self.uid:
            return material
        for material in bpy.data.materials:
            if material.mmb.is_mm and material.mmb.uid == self.uid:
                self.material_name = material.name
                return material
        return None

    def params(self, material):
        return getattr(material, self.attr, None)

    def free(self):
        if self.renderer is not None:
            self.renderer.free()
            self.renderer = None

    # Parameter changes

    def _apply_key(self, material, key):
        """Push one property value into the generator tree; returns True if shaders must be rebuilt."""
        spec = self.specs.get(key)
        params = self.params(material)
        if spec is None or params is None or spec.kind == S.UNSUPPORTED:
            return False
        gen = S.resolve(self.project, spec.gen_path)
        if gen is None:
            return False
        if spec.kind == S.SEED:
            self.project.set_seed(gen, getattr(params, key))
            return False
        if spec.kind in (S.GRADIENT, S.CURVE):
            node = helpers.find(material, key)
            if node is None:
                return False
            # Untouched helper: keep the file's exact value (a curve read back from Blender's
            # widget only approximates Material Maker's tangents).
            value = spec.value if helpers.is_unchanged_since_written(node) else helpers.read(node)
            return self.project.set_parameter(gen, spec.name, value)
        value = getattr(params, key)
        if spec.kind == S.COLOR:
            value = tuple(value)
        return self.project.set_parameter(gen, spec.name, S.to_mm_value(spec, value))

    def commit(self, material):
        """Make the (structurally edited) project the material's source and rebuild the session.

        Current parameter values are part of the serialized source, so nothing is lost; stored
        values of parameters that no longer exist are cleared so a node reusing a name starts
        from its own defaults. Returns the new session.
        """
        from .core.serialize import project_to_ptex

        text = project_to_ptex(self.project)
        new_keys = {spec.key for section in S.build_schema(self.project) for spec in section.specs}
        params = self.params(material)
        for key in set(self.specs) - new_keys:
            if params is not None and hasattr(params, key):
                params.property_unset(key)
            node = helpers.find(material, key)
            if node is not None:
                material.node_tree.nodes.remove(node)
        settings = material.mmb
        settings.source = text
        settings.schema_attr = S.schema_attr(settings.uid)
        self.free()
        session = Session(material)
        session.dirty = True
        session.preview_done = True
        _sessions[self.uid] = session
        _ensure_timer()
        for hook in commit_hooks:
            hook(material, session)
        return session

    def queue(self, key):
        self.pending.add(key)

    def sync_all(self):
        """Re-read every property (after undo/redo or loading)."""
        self.pending.update(self.specs.keys())

    # Rendering

    def material_node(self):
        return self.project.find_material()

    def full_size(self, material):
        res = material.mmb.resolution
        return 2 ** int(res) if res != "0" else self.material_node().get_image_size()

    def _rebuild_program(self):
        self.program = compile_material(self.project)
        self.uses_time = _uses_time(self.program)
        if self.renderer is not None:
            self.renderer.free()
            self.renderer = None

    def update(self, now):
        """Called by the timer. Returns True while there is work in progress."""
        material = self.material()
        if material is None:
            return False
        if self.pending:
            keys, self.pending = self.pending, set()
            material_only = True
            for key in keys:
                spec = self.specs.get(key)
                if spec is None:
                    continue
                self.recompile |= self._apply_key(material, key)
                gen = S.resolve(self.project, spec.gen_path)
                if not (isinstance(gen, GenMaterial) and spec.name != "size"):
                    material_only = False
            self.apply_material_section(material)
            if not material_only:
                self.last_change = now
                self.dirty = True
                self.preview_done = False
        if self.recompile:
            if now - self.last_change < RECOMPILE_DELAY:
                return True
            self._rebuild_program()
            self.recompile = False
            self.dirty = True
        if not self.dirty or not material.mmb.live_update:
            return False
        settled = now - self.last_change >= SETTLE_SECONDS
        if settled or self.last_full_seconds < FAST_FULL_RENDER:
            self.render(material, self.full_size(material))
            self.dirty = False
        elif not self.preview_done:
            self.render(material, min(2 ** int(material.mmb.preview_resolution), self.full_size(material)))
            self.preview_done = True
        return True

    def render(self, material, size):
        from .gpu_render.renderer import MaterialRenderer

        start = time.perf_counter()
        if self.renderer is None:
            self.renderer = MaterialRenderer(self.program)
        self.renderer.time = self.time
        results = self.renderer.render(uniform_values(self.project), size=size)
        images = self.channel_images(material)
        for name, pixels in results.items():
            image = images.get(name)
            if image is None:
                continue
            image_utils.prepare_image(image, pixels.shape[0], self.program.channels[name].is_color)
            image_utils.write_image(image, pixels)
        seconds = time.perf_counter() - start
        if size == self.full_size(material):
            self.last_full_seconds = seconds
        self.last_render = {"size": size, "seconds": seconds, "passes": self.renderer.stats.get("passes_drawn", 0)}
        _tag_redraw()

    def channel_images(self, material):
        """{channel: Image} owned by this material, created when missing or shared with a copy."""
        settings = material.mmb
        slots = {slot.channel: slot for slot in settings.images}
        rv = {}
        changed = False
        for name, channel in self.program.channels.items():
            slot = slots.get(name)
            image = slot.image if slot is not None else None
            if image is None or image.get("mmb_uid") != self.uid:
                image = image_utils.new_image("%s_%s" % (material.name, name), 64, channel.is_color)
                image["mmb_uid"] = self.uid
                if slot is None:
                    slot = settings.images.add()
                    slot.channel = name
                slot.image = image
                changed = True
            rv[name] = image
        if changed or not self._nodes_present(material):
            material_builder.build(material, rv, material.name)
            self.apply_material_section(material)
        return rv

    @staticmethod
    def _nodes_present(material):
        return material.node_tree is not None and material.node_tree.nodes.get(material_builder.PREFIX + "principled")

    def apply_material_section(self, material):
        node = self.material_node()
        if node is None or material.node_tree is None:
            return
        values = {}
        for k, v in node.parameters.items():
            values[k] = (v.r, v.g, v.b, v.a) if hasattr(v, "r") else v
        inputs = [i.get("name") for i in node.get_input_defs()]
        has_opacity = "opacity_tex" in inputs and node.get_source(inputs.index("opacity_tex")) is not None
        material_builder.apply_material_parameters(material, values, lambda c: c in self.program.channels,
                                                   has_opacity)


# Called as hook(material, session) after a structural commit (the graph editor rebuilds its view).
commit_hooks = []
# Called every timer tick (the graph editor applies queued edits of its trees).
tick_hooks = []


def _uses_time(program):
    """True when any pass reads Material Maker's $time (elapsed_time)."""
    return any("elapsed_time" in "".join(p.source.globals) + p.source.defs + p.source.code + p.source.rgba
               for p in program.passes())


def _fcurve_paths(idblock):
    anim = idblock.animation_data
    action = anim.action if anim is not None else None
    if action is None:
        return []
    fcurves = getattr(action, "fcurves", None)   # legacy actions (Blender < 5.0)
    if fcurves is None:
        fcurves = []
        slot = getattr(anim, "action_slot", None)
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    if slot is None or bag.slot == slot:
                        fcurves.extend(bag.fcurves)
    return [fc.data_path for fc in fcurves]


def scene_time(scene):
    """Seconds at the current frame, the clock Material Maker's $time follows."""
    render = scene.render
    return (scene.frame_current + scene.frame_subframe) * render.fps_base / render.fps


# --------------------------------------------------------------------------------------
# Module API used by properties, operators and handlers
# --------------------------------------------------------------------------------------

def get_session(material, create=True):
    settings = material.mmb
    if not settings.is_mm:
        return None
    session = _sessions.get(settings.uid)
    if session is None and create:
        session = _sessions[settings.uid] = Session(material)
    return session


def on_param_changed(material, key):
    session = _sessions.get(material.mmb.uid)
    if session is not None:
        session.queue(key)
    _ensure_timer()


def on_settings_changed(material):
    session = _sessions.get(material.mmb.uid)
    if session is not None:
        session.dirty = True
        session.preview_done = True
    _ensure_timer()


def request_render(material, force=False):
    session = get_session(material)
    if force and session.renderer is not None:
        session.renderer.free()
        session.renderer = None
    session.dirty = True
    session.preview_done = True
    _ensure_timer()
    return session


def render_now(material):
    """Synchronous full-resolution render (used right after import)."""
    session = get_session(material)
    session.render(material, session.full_size(material))
    session.dirty = False
    return session


def drop_session(uid):
    session = _sessions.pop(uid, None)
    if session is not None:
        session.free()


def clear_sessions():
    for uid in list(_sessions):
        drop_session(uid)


def new_uid():
    return uuid.uuid4().hex


def _fix_duplicate_uids():
    """Copies of a material share its uid; give each copy its own (its images follow on render)."""
    owners = {}
    for material in bpy.data.materials:
        settings = getattr(material, "mmb", None)
        if settings is None or not settings.is_mm:
            continue
        uid = settings.uid
        session = _sessions.get(uid)
        if uid not in owners:
            owners[uid] = material
            continue
        # Keep the uid on the material the session already serves; re-key the other one.
        if session is not None and session.material_name == material.name:
            owners[uid].mmb.uid = new_uid()
            owners[uid] = material
        else:
            settings.uid = new_uid()


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "NODE_EDITOR", "IMAGE_EDITOR", "PROPERTIES"}:
                area.tag_redraw()


def _tick():
    busy = False
    now = time.perf_counter()
    try:
        for hook in tick_hooks:
            busy |= bool(hook())
        _fix_duplicate_uids()
        for uid, session in list(_sessions.items()):
            if session.material() is None:
                drop_session(uid)
                continue
            try:
                busy |= session.update(now)
                session.error = ""
            except Exception as e:  # keep the timer alive, show the error in the panel
                session.error = "%s: %s" % (type(e).__name__, e)
                session.dirty = False
                session.recompile = False
                traceback.print_exc()
        # Materials loaded from a file get their session (and textures) here.
        for material in bpy.data.materials:
            settings = getattr(material, "mmb", None)
            if settings is not None and settings.is_mm and settings.uid not in _sessions:
                try:
                    session = get_session(material)
                    session.dirty = True
                    session.preview_done = True
                    busy = True
                except Exception as e:
                    print("MM Bridge: cannot restore %s: %s" % (material.name, e))
                    _sessions[settings.uid] = _FailedSession(settings.uid, material.name, e)
    except Exception:
        traceback.print_exc()
    return TICK_BUSY if busy else None


class _FailedSession:
    """Stands in for a session that could not be created, so it is not retried every tick."""

    def __init__(self, uid, material_name, error):
        self.uid = uid
        self.material_name = material_name
        self.error = "%s: %s" % (type(error).__name__, error)
        self.sections = []
        self.last_render = {}
        self.renderer = None

    def material(self):
        return bpy.data.materials.get(self.material_name)

    def update(self, now):
        return False

    def free(self):
        pass


def request_tick():
    """Ask for a timer tick without touching Blender data (safe from any callback)."""
    _ensure_timer()


def _ensure_timer():
    # Persistent: Blender drops other timers when a file is loaded, and the restore tick must run.
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0, persistent=True)


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    clear_sessions()
    _ensure_timer()


@bpy.app.handlers.persistent
def _on_frame_change(scene, *_args):
    """Keyframed parameters and $time: re-render for the new frame (preview size while playing)."""
    if threading.current_thread() is not threading.main_thread():
        return  # animation renders evaluate frames off the main thread; the GPU is not usable there
    t = scene_time(scene)
    now = time.perf_counter()
    for session in list(_sessions.values()):
        if not isinstance(session, Session):
            continue
        material = session.material()
        if material is None or not material.mmb.live_update:
            continue
        keys = session.animated_keys(material)
        session.pending.update(keys)
        if session.uses_time and session.time != t:
            session.time = t
            session.dirty = True
            session.preview_done = False
            session.last_change = now
        if keys or session.dirty:
            try:
                session.update(now)
            except Exception as e:
                session.error = "%s: %s" % (type(e).__name__, e)
    _ensure_timer()


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    """Edits of gradient/curve helper nodes arrive as material/node tree updates."""
    if not _sessions:
        return
    if not any(isinstance(u.id, (bpy.types.Material, bpy.types.NodeTree)) for u in depsgraph.updates):
        return
    queued = False
    for session in list(_sessions.values()):
        if isinstance(session, Session):
            material = session.material()
            if material is not None and session.check_helpers(material):
                queued = True
    if queued:
        _ensure_timer()


@bpy.app.handlers.persistent
def _on_undo_redo(*_args):
    for uid, session in list(_sessions.items()):
        if isinstance(session, Session):
            if session.material() is None:
                drop_session(uid)
            elif session.source_text != session.material().mmb.source:
                drop_session(uid)  # the graph itself was undone: rebuild from the restored source
                for hook in commit_hooks:
                    hook(None, None)
            else:
                session.sync_all()
                session.check_helpers(session.material())
    _ensure_timer()


def register():
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.undo_post.append(_on_undo_redo)
    bpy.app.handlers.redo_post.append(_on_undo_redo)
    bpy.app.handlers.frame_change_post.append(_on_frame_change)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    _ensure_timer()


def unregister():
    for handlers, fn in ((bpy.app.handlers.load_post, _on_load_post), (bpy.app.handlers.undo_post, _on_undo_redo),
                         (bpy.app.handlers.redo_post, _on_undo_redo),
                         (bpy.app.handlers.frame_change_post, _on_frame_change),
                         (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)):
        if fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    clear_sessions()
