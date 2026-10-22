"""Render compiled Material Maker programs with Blender's gpu module.

MaterialRenderer keeps every pass's GPU shader and every intermediate texture
alive between renders, so a parameter change only uploads new push constants
and re-draws the passes whose parameters or inputs changed.

All of this must run on Blender's main thread (it needs the GPU context).
"""

import hashlib
from collections import OrderedDict

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..core.compiler import IteratePass
from .shader import create_shader

# Material Maker renders larger textures in 2048 chunks; tiles also keep any
# single draw short enough to avoid a GPU driver timeout on heavy graphs.
TILE_SIZE = 2048
BUFFER_FORMAT = "RGBA16F"   # Godot renders buffers into half-float HDR viewports
OUTPUT_FORMAT = "RGBA32F"
_QUAD = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
_SHADER_CACHE_SIZE = 256
_shader_cache = OrderedDict()


class CompiledPass:
    """A GPU shader for one TexturePass plus the bits needed to draw it quickly."""

    def __init__(self, blender_shader):
        self.spec = blender_shader
        self.shader = create_shader(blender_shader)
        self.batch = batch_for_shader(self.shader, "TRI_FAN", {"pos": _QUAD})
        self.params_location = self._location("mm_p")
        self.has_frame = self._location("mm_frame") != -1
        self.has_chunk_offset = self._location("mm_chunk_offset") != -1
        # $time: the define line mentions elapsed_time once; any other use reads the clock.
        source = blender_shader.fragment_source
        self.uses_time = source.count("elapsed_time") > 1 or "#define TIME" in source

    def _location(self, name):
        try:
            return self.shader.uniform_from_name(name)
        except ValueError:  # optimized out by the driver
            return -1

    def draw(self, packed_params, frame, chunk_offset, textures):
        shader = self.shader
        shader.bind()
        if self.has_frame:
            shader.uniform_float("mm_frame", frame)
        if self.has_chunk_offset:
            shader.uniform_float("mm_chunk_offset", chunk_offset)
        if self.params_location != -1:
            slots = self.spec.param_slots
            shader.uniform_vector_float(self.params_location, gpu.types.Buffer("FLOAT", 4 * slots, packed_params),
                                        4, slots)
        for name in self.spec.samplers:
            shader.uniform_sampler(name, textures[name])
        self.batch.draw(shader)


def get_compiled_pass(blender_shader):
    """Compiled shaders are cached by source, so recompiling an unchanged pass is free."""
    key = hashlib.sha1(blender_shader.fragment_source.encode("utf-8")).hexdigest()
    compiled = _shader_cache.get(key)
    if compiled is None:
        compiled = CompiledPass(blender_shader)
        _shader_cache[key] = compiled
        while len(_shader_cache) > _SHADER_CACHE_SIZE:
            _shader_cache.popitem(last=False)
    else:
        _shader_cache.move_to_end(key)
        compiled.spec = blender_shader  # same code; parameter defaults may differ
    return compiled


def clear_shader_cache():
    _shader_cache.clear()


# --------------------------------------------------------------------------------------
# Small utility shaders: blit with flip, 2x2 box downsample, atlas placement
# --------------------------------------------------------------------------------------

_utility = {}


def _utility_shader(kind):
    if kind in _utility:
        return _utility[kind]
    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "pos")
    info.push_constant("IVEC2", "offset")
    info.push_constant("INT", "flip")
    info.sampler(0, "FLOAT_2D", "src")
    info.fragment_out(0, "VEC4", "out_color")
    info.vertex_source("void main() { gl_Position = vec4(pos, 0.0, 1.0); }")
    if kind == "blit":
        # Copy src 1:1 into the viewport at `offset`, optionally flipping rows.
        info.fragment_source(
            "void main() {\n"
            "  ivec2 p = ivec2(gl_FragCoord.xy) - offset;\n"
            "  ivec2 size = textureSize(src, 0);\n"
            "  if (flip != 0) p.y = size.y - 1 - p.y;\n"
            "  out_color = texelFetch(src, p, 0);\n"
            "}\n")
    else:
        # 2x2 box filter (Godot Image.generate_mipmaps).
        info.fragment_source(
            "void main() {\n"
            "  ivec2 p = 2*(ivec2(gl_FragCoord.xy) - offset);\n"
            "  ivec2 m = textureSize(src, 0) - 1;\n"
            "  out_color = 0.25*(texelFetch(src, min(p, m), 0) + texelFetch(src, min(p+ivec2(1, 0), m), 0)\n"
            "             + texelFetch(src, min(p+ivec2(0, 1), m), 0) + texelFetch(src, min(p+ivec2(1, 1), m), 0));\n"
            "}\n")
    shader = gpu.shader.create_from_info(info)
    _utility[kind] = (shader, batch_for_shader(shader, "TRI_FAN", {"pos": _QUAD}))
    return _utility[kind]


def _draw_utility(kind, src_texture, offset=(0, 0), flip=False):
    shader, batch = _utility_shader(kind)
    shader.bind()
    for name, value in (("offset", offset), ("flip", 1 if flip else 0)):
        try:
            shader.uniform_int(name, value)
        except ValueError:  # unused by this utility shader, optimized out
            pass
    shader.uniform_sampler("src", src_texture)
    batch.draw(shader)


def build_mip_atlas(texture, size):
    """Mip chain of a size x size texture packed in a 1.5*size x size atlas (layout in core.glsl)."""
    atlas = gpu.types.GPUOffScreen(size + size // 2, size, format=BUFFER_FORMAT)
    levels = []
    with atlas.bind():
        gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0))
        gpu.state.viewport_set(0, 0, size, size)
        _draw_utility("blit", texture)
    src, level, level_size = texture, 1, size // 2
    try:
        while level_size >= 1:
            dst = gpu.types.GPUOffScreen(level_size, level_size, format=BUFFER_FORMAT)
            levels.append(dst)
            with dst.bind():
                _draw_utility("downsample", src)
            origin = (size, size - (size >> (level - 1)))
            with atlas.bind():
                gpu.state.viewport_set(origin[0], origin[1], level_size, level_size)
                _draw_utility("blit", dst.texture_color, origin)
            src, level, level_size = dst.texture_color, level + 1, level_size // 2
    finally:
        for lv in levels:
            lv.free()
    return atlas


def load_image_texture(path):
    """Image file -> offscreen texture with Material Maker row order (row 0 = top of the image)."""
    import bpy

    image = bpy.data.images.load(path, check_existing=True)
    source = gpu.texture.from_image(image)
    w, h = image.size
    target = gpu.types.GPUOffScreen(w, h, format=BUFFER_FORMAT)
    with target.bind():
        _draw_utility("blit", source, flip=True)
    return target


_fonts = {}


def _font_id(path):
    import blf

    if not path:
        return 0
    if path not in _fonts:
        font = blf.load(path)
        _fonts[path] = font if font != -1 else 0
    return _fonts[path]


def render_text_texture(settings, size):
    """Port of renderer.gd render_text: white text drawn at (0.5+x, 0.5+y) of the texture,
    top-left anchored (or centered), returned with Material Maker row order."""
    import blf
    from mathutils import Matrix

    text, font_path, font_size, x, y, center = settings
    font = _font_id(font_path)
    blf.size(font, max(1, font_size))
    lines = text.split("\n")
    _, line_height = blf.dimensions(font, "Ág")
    ascent = blf.dimensions(font, "Á")[1]
    width = max((blf.dimensions(font, line)[0] for line in lines), default=0.0)
    left = size * (0.5 + x)
    top = size * (0.5 + y)  # Godot coordinates: y grows downwards
    if center:
        left -= 0.5 * width
        top -= 0.5 * line_height * len(lines)
    upright = gpu.types.GPUOffScreen(size, size, format=BUFFER_FORMAT)
    try:
        with upright.bind():
            gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0))
            with gpu.matrix.push_pop():
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(Matrix(
                    ((2.0 / size, 0, 0, -1.0), (0, 2.0 / size, 0, -1.0), (0, 0, 1, 0), (0, 0, 0, 1))))
                gpu.state.blend_set("ALPHA")
                blf.color(font, 1.0, 1.0, 1.0, 1.0)
                for i, line in enumerate(lines):
                    baseline = size - (top + ascent + i * line_height)
                    blf.position(font, left, baseline, 0.0)
                    blf.draw(font, line)
                gpu.state.blend_set("NONE")
        target = gpu.types.GPUOffScreen(size, size, format=BUFFER_FORMAT)
        with target.bind():
            _draw_utility("blit", upright.texture_color, flip=True)
    finally:
        upright.free()
    tex = target.texture_color
    tex.filter_mode(True)
    tex.extend_mode("REPEAT")
    return target


# --------------------------------------------------------------------------------------
# Material renderer
# --------------------------------------------------------------------------------------

class MaterialRenderer:
    """Renders a core.compiler.MaterialProgram; keeps GPU state between renders."""

    def __init__(self, program, image_loader=load_image_texture):
        self.program = program
        self.image_loader = image_loader
        self.passes = {p.name: get_compiled_pass(p.shader) for p in program.passes()}
        self._targets = {}       # buffer name -> GPUOffScreen
        self._atlases = {}       # buffer name -> GPUOffScreen (mip atlas)
        self._images = {}        # sampler name -> GPUOffScreen
        self._last_params = {}   # pass name -> packed params last drawn with
        self._channel_sizes = {}
        self._aliases = {}       # iterate buffer loop texture -> its final texture
        self.time = 0.0
        self.variation = 0.0
        self.stats = {}

    def free(self):
        for d in (self._targets, self._atlases, self._images):
            for t in d.values():
                t.free()
            d.clear()
        self._last_params.clear()

    def _textures(self):
        textures = {}
        for name, target in self._targets.items():
            textures[name] = target.texture_color
        for alias, name in self._aliases.items():
            if name in self._targets:
                textures[alias] = self._targets[name].texture_color
        for name, atlas in self._atlases.items():
            textures[name + "_mips"] = atlas.texture_color
        for name, target in self._images.items():
            textures[name] = target.texture_color
        return textures

    def _ensure_texts(self, rendered):
        for name, gen in getattr(self.program, "texts", {}).items():
            settings = gen.text_settings()
            if name in self._images and self._last_params.get(("text", name)) == settings:
                continue
            old = self._images.pop(name, None)
            if old is not None:
                old.free()
            self._images[name] = render_text_texture(settings, gen.TEXTURE_SIZE)
            self._last_params[("text", name)] = settings
            rendered.add(name)

    def _ensure_images(self):
        for name, path in self.program.images.items():
            if name not in self._images:
                self._images[name] = self.image_loader(path)
                tex = self._images[name].texture_color
                tex.filter_mode(True)
                tex.extend_mode("REPEAT")

    def _draw(self, compiled, packed, size, flip, textures):
        frame = (self.time, self.variation, 1.0, 1.0 if flip else 0.0)
        if size <= TILE_SIZE:
            compiled.draw(packed, frame, (0.0, 0.0), textures)
            return
        tile = TILE_SIZE
        scale = tile / size
        for y0 in range(0, size, tile):
            for x0 in range(0, size, tile):
                gpu.state.viewport_set(x0, y0, tile, tile)
                offset_y = 1.0 - (y0 + tile) / size if flip else y0 / size
                compiled.draw(packed, (frame[0], frame[1], scale, frame[3]), (x0 / size, offset_y), textures)

    def _render_iterate(self, p, values, textures, rendered, force):
        """Port of MMGenIterateBuffer's render loop. Returns False when nothing changed."""
        iterations, shrink, autostop = p.loop_settings()
        uses_time = self.passes[p.init.name].uses_time or self.passes[p.loop.name].uses_time
        signature = (tuple(p.init.shader.pack_params(values)), tuple(p.loop.shader.pack_params(values)),
                     iterations, shrink, autostop, self.time if uses_time else None)
        if (not force and p.name in self._targets and not (set(p.samplers) & rendered)
                and self._last_params.get(p.name) == signature):
            return False
        vals = dict(values)
        vals[p.iteration_uniform] = (0.0,)
        size = p.size
        current = gpu.types.GPUOffScreen(size, size, format=BUFFER_FORMAT)
        with current.bind():
            self._draw(self.passes[p.init.name], p.init.shader.pack_params(vals), size, False, textures)
        loop_textures = dict(textures)
        previous_pixels = None
        for k in range(1, iterations + 1):
            loop_size = max(4, size >> k) if shrink else size
            tex = current.texture_color
            tex.filter_mode(False)        # Godot: texture.flags = 0 (nearest, clamped)
            tex.extend_mode("EXTEND")
            loop_textures[p.loop_texture] = tex
            vals[p.iteration_uniform] = (float(k),)
            following = gpu.types.GPUOffScreen(loop_size, loop_size, format=BUFFER_FORMAT)
            with following.bind():
                fb = gpu.state.active_framebuffer_get()
                self._draw(self.passes[p.loop.name], p.loop.shader.pack_params(vals), loop_size, False, loop_textures)
                pixels = (np.asarray(fb.read_color(0, 0, loop_size, loop_size, 4, 0, "FLOAT")).copy()
                          if autostop else None)
            current.free()
            current = following
            if autostop and previous_pixels is not None and previous_pixels.shape == pixels.shape                     and np.array_equal(previous_pixels, pixels):
                break
            previous_pixels = pixels
        current.texture_color.filter_mode(False)
        current.texture_color.extend_mode("EXTEND")
        old = self._targets.pop(p.name, None)
        if old is not None:
            old.free()
        self._targets[p.name] = current
        self._aliases[p.loop_texture] = p.name
        self._last_params[p.name] = signature
        return True

    def render(self, values=None, size=None, channels=None, force=False):
        """Render what changed; returns {channel: float32 array (h, w, 4)} for re-rendered channels.

        values: {uniform name: value} as produced by core.params.uniform_values().
        size: output resolution (defaults to the material's); buffers keep their own sizes.
        channels: names to render (default: all).
        """
        import time as _time

        start = _time.perf_counter()
        values = values or {}
        self._ensure_images()
        rendered = set()
        self._ensure_texts(rendered)
        textures = self._textures()
        n_drawn = 0

        for p in self.program.buffers:
            if isinstance(p, IteratePass):
                if self._render_iterate(p, values, textures, rendered, force):
                    textures = self._textures()
                    rendered.update((p.name, p.loop_texture))
                    n_drawn += 1
                continue
            compiled = self.passes[p.name]
            packed = tuple(p.shader.pack_params(values))
            signature = packed + ((self.time,) if compiled.uses_time else ())
            depends = set(p.samplers) & rendered
            if not force and p.name in self._targets and not depends and self._last_params.get(p.name) == signature:
                continue
            target = self._targets.get(p.name)
            if target is None:
                target = self._targets[p.name] = gpu.types.GPUOffScreen(p.size, p.size, format=BUFFER_FORMAT)
            with target.bind():
                self._draw(compiled, packed, p.size, False, textures)
            use_filter, _mipmap = p.sampling
            target.texture_color.filter_mode(use_filter)
            target.texture_color.extend_mode("REPEAT")
            if p.needs_mip_atlas:
                old = self._atlases.pop(p.name, None)
                if old is not None:
                    old.free()
                self._atlases[p.name] = build_mip_atlas(target.texture_color, p.size)
            textures = self._textures()
            self._last_params[p.name] = signature
            rendered.add(p.name)
            rendered.add(p.name + "_mips")
            n_drawn += 1

        results = {}
        out_size = int(size or self.program.size)
        for name, channel in self.program.channels.items():
            if channels is not None and name not in channels:
                continue
            p = channel.texture
            compiled = self.passes[p.name]
            packed = tuple(p.shader.pack_params(values))
            signature = packed + ((self.time,) if compiled.uses_time else ())
            depends = set(p.samplers) & rendered
            key = ("channel", name)
            if (not force and not depends and self._last_params.get(key) == signature
                    and self._channel_sizes.get(name) == out_size):
                continue
            target = gpu.types.GPUOffScreen(out_size, out_size, format=OUTPUT_FORMAT)
            try:
                with target.bind():
                    fb = gpu.state.active_framebuffer_get()
                    self._draw(compiled, packed, out_size, True, textures)
                    gpu.state.viewport_set(0, 0, out_size, out_size)
                    buffer = fb.read_color(0, 0, out_size, out_size, 4, 0, "FLOAT")
            finally:
                target.free()
            results[name] = np.asarray(buffer, dtype=np.float32)  # keeps `buffer` alive, no copy
            self._last_params[key] = signature
            self._channel_sizes[name] = out_size
            n_drawn += 1

        self.stats = {"passes_drawn": n_drawn, "seconds": _time.perf_counter() - start}
        return results
