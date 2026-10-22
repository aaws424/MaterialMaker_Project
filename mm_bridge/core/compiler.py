"""Compile a loaded Project into the set of render passes that produce a material's textures."""

import re
from dataclasses import dataclass, field

from .context import GenContext
from .generators import GenBuffer, GenImage, GenIterateBuffer, GenText, default_generated_shader
from .glsl import to_blender_glsl


@dataclass
class ShaderSource:
    """Generated code for one output, before any backend-specific conversion."""
    globals: list
    defs: str
    code: str
    rgba: str
    output_type: str
    warnings: list = field(default_factory=list)

    @property
    def uses_randomness(self):
        """True when the code hashes anything (rand*, seeds), i.e. results depend on GPU cos precision."""
        return bool(re.search(r"\b(rand|rand2|rand3|param_rnd|param_rndi)\s*\(|\bseed_o\d+", self.defs + self.code))

    def to_godot_shader(self, common_shader=""):
        """Approximately what Material Maker's generate_shader() produces (for diffing)."""
        return "".join([
            "shader_type canvas_item;\nrender_mode blend_disabled;\n",
            common_shader, "\n",
            "".join(self.globals),
            self.defs,
            "\nuniform float mm_chunk_size = 1.0;\n\nuniform vec2 mm_chunk_offset = vec2(0.0);\n",
            "\nuniform float variation = 0.0;\n\nvoid fragment() {\n",
            "float _seed_variation_ = variation;\nvec2 uv = mm_chunk_offset+mm_chunk_size*UV;\n",
            self.code,
            "COLOR = %s;\n}\n" % (self.rgba or "vec4(1.0, 0.0, 0.0, 1.0)"),
        ])


@dataclass
class TexturePass:
    """One GPU render: a shader drawn into a size x size texture."""
    name: str
    size: int
    source: ShaderSource
    shader: object  # glsl.BlenderShader
    sampling: tuple = (True, True)  # (linear filter, mipmapped) when sampled by other passes
    needs_mip_atlas: bool = False

    @property
    def samplers(self):
        return self.shader.samplers


@dataclass
class IteratePass:
    """An iterate_buffer: `init` renders once, then `loop` runs once per iteration, sampling the
    previous result as `loop_texture`. The final result is published as `name`."""
    name: str            # o<id>_tex
    loop_texture: str    # o<id>_loop_tex
    iteration_uniform: str
    init: TexturePass
    loop: TexturePass
    generator: object    # GenIterateBuffer: iteration count, shrink and autostop are read live

    @property
    def samplers(self):
        own = {self.name, self.loop_texture}
        return [s for s in self.init.samplers + self.loop.samplers if s not in own]

    @property
    def size(self):
        return self.generator.get_size()

    def loop_settings(self):
        g = self.generator
        return self.generator.get_iterations(), bool(g.get_parameter("shrink")), bool(g.get_parameter("autostop"))


# Channels Material Maker stores as sRGB-encoded colors; they are rendered as linear values.
COLOR_CHANNELS = ("albedo", "emission")


@dataclass
class MaterialChannel:
    name: str          # "albedo", "rough", "normal", ...
    output_index: int
    file_format: str   # "png" or "exr", as MM's export profile names it
    texture: TexturePass

    @property
    def is_color(self):
        return self.name in COLOR_CHANNELS


@dataclass
class MaterialProgram:
    material_type: str
    size: int
    channels: dict              # name -> MaterialChannel, in export-profile order
    buffers: list               # TexturePass/IteratePass, dependencies before dependents
    images: dict                # sampler name -> image file path
    warnings: list
    texts: dict = field(default_factory=dict)   # sampler name -> GenText (settings read at render time)

    def passes(self):
        """Every shader pass in a valid render order (iterate buffers contribute init and loop)."""
        rv = []
        for b in self.buffers:
            rv += [b.init, b.loop] if isinstance(b, IteratePass) else [b]
        return rv + [c.texture for c in self.channels.values()]


def _mip_samplers(project):
    """{sampler: filter} for every buffer whose texture Material Maker mipmaps."""
    rv = {}
    for name, gen in project.textures.items():
        if isinstance(gen, GenBuffer):
            use_filter, use_mipmap = gen.get_sampling()
            if use_mipmap:
                rv[name] = use_filter
    return rv


def compile_output(generator, output_index, common_glsl=None, output_transform=None):
    """Port of MMGenBase.generate_output_shader: code for one output of one generator."""
    project = generator.project
    context = GenContext()
    src = generator.get_shader_code("uv", output_index, context)
    if not src:
        src = default_generated_shader()
    outputs = generator.get_output_defs(True)
    output_type = outputs[output_index].get("type", "rgba") if output_index < len(outputs) else "rgba"
    source = ShaderSource(
        globals=list(src.get("globals", [])),
        defs=src.get("defs", ""),
        code=src.get("code", ""),
        rgba=src.get("rgba", ""),
        output_type=output_type,
    )
    common = project.library.common_glsl if common_glsl is None else common_glsl
    shader = to_blender_glsl(source, common, _mip_samplers(project), output_transform)
    return source, shader


def _buffer_source(buffer, input_index=0):
    """Port of MMGenBuffer.do_update_shader: the shader rendering the buffer's input."""
    context = GenContext()
    src_port = buffer.get_source(input_index)
    src = {}
    if src_port is not None:
        src = src_port.generator.get_shader_code("uv", src_port.output_index, context)
    if not src:
        src = default_generated_shader()  # unconnected: Material Maker renders red, as here
    elif "rgba" not in src:
        buffer.project.warn("Buffer %s input has no rgba output" % buffer.name)
    source = ShaderSource(globals=list(src.get("globals", [])), defs=src.get("defs", ""),
                          code=src.get("code", ""), rgba=src.get("rgba", ""), output_type="rgba")
    return source, to_blender_glsl(source, buffer.project.library.common_glsl, _mip_samplers(buffer.project))


_SAFE_CONDITION_RE = re.compile(r"^[\s()a-z0-9.<>=!+\-*/]*$")


def _eval_condition(expression):
    """Evaluate an export-profile condition such as 'true or false' (Godot Expression subset)."""
    expr = expression.strip()
    if not _SAFE_CONDITION_RE.match(expr):
        raise ValueError("Unsupported export condition %r" % expression)
    expr = re.sub(r"\btrue\b", "True", expr)
    expr = re.sub(r"\bfalse\b", "False", expr)
    return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - whitelisted characters only


def _substitute(s, context):
    for k, v in context.items():
        s = s.replace(k, v)
    return s


def _channel_name(file_name):
    m = re.search(r"\$\(path_prefix\)_?([\w-]+)\.(\w+)$", file_name)
    if m:
        return m.group(1), m.group(2).lower()
    return file_name, "png"


def compile_material(project, profile="Blender"):
    """Compile every texture of `profile` for the project's material, plus the buffers they need."""
    material = project.find_material()
    if material is None:
        raise ValueError("The project has no material node")
    export = material.get_export(profile)
    if not export:
        raise ValueError("Material type '%s' has no '%s' export (available: %s)" % (
            material.model, profile, ", ".join(material.get_export_profiles())))

    context = material.get_connections_and_parameters_context()
    channels = {}
    for f in export.get("files", []):
        if f.get("type") != "texture":
            continue
        if "conditions" in f and not _eval_condition(_substitute(f["conditions"], context)):
            continue
        if "output" not in f:
            project.warn("Export file %s uses an expression output, not supported yet" % f.get("file_name"))
            continue
        name, file_format = _channel_name(f["file_name"])
        output_index = int(f["output"])
        transform = "srgb_to_linear" if name in COLOR_CHANNELS else None
        source, shader = compile_output(material, output_index, output_transform=transform)
        texture = TexturePass(name=name, size=material.get_image_size(), source=source, shader=shader)
        channels[name] = MaterialChannel(name, output_index, file_format, texture)

    buffers, images, texts, done = [], {}, {}, set()

    def visit(texture_pass, skip=()):
        atlases = set(texture_pass.shader.mip_atlases.values())
        for sampler in texture_pass.samplers:
            if sampler in done or sampler in atlases or sampler in skip:
                continue
            done.add(sampler)
            gen = project.textures.get(sampler)
            if isinstance(gen, GenIterateBuffer):
                tex, loop_tex = "o%d_tex" % gen.uid, "o%d_loop_tex" % gen.uid
                done.update((tex, loop_tex))
                init_source, init_shader = _buffer_source(gen, 0)
                loop_source, loop_shader = _buffer_source(gen, 1)
                iterate = IteratePass(
                    name=tex, loop_texture=loop_tex, iteration_uniform="o%d_iteration" % gen.uid,
                    init=TexturePass(name=tex + "@init", size=gen.get_size(), source=init_source, shader=init_shader),
                    loop=TexturePass(name=tex + "@loop", size=gen.get_size(), source=loop_source, shader=loop_shader),
                    generator=gen)
                visit(iterate.init, (tex, loop_tex))
                visit(iterate.loop, (tex, loop_tex))
                buffers.append(iterate)
            elif isinstance(gen, GenBuffer):
                source, shader = _buffer_source(gen)
                buffer_pass = TexturePass(name=sampler, size=gen.get_size(), source=source, shader=shader,
                                          sampling=gen.get_sampling())
                visit(buffer_pass)
                buffers.append(buffer_pass)
            elif isinstance(gen, GenText):
                texts[sampler] = gen
            elif isinstance(gen, GenImage):
                images[sampler] = gen.get_image_path()
            else:
                project.warn("Sampler %s has no known producer" % sampler)

    for channel in channels.values():
        visit(channel.texture)

    # Buffers sampled at a non-zero lod somewhere need a mip atlas built after rendering.
    program_passes = [c.texture for c in channels.values()]
    for b in buffers:
        program_passes += [b.init, b.loop] if isinstance(b, IteratePass) else [b]
    for p in program_passes:
        for buffer_name in p.shader.mip_atlases:
            for b in buffers:
                if b.name == buffer_name and isinstance(b, TexturePass):
                    b.needs_mip_atlas = True

    warnings = list(project.warnings)
    for p in program_passes:
        warnings += ["%s: %s" % (p.name, w) for w in p.source.warnings]
    return MaterialProgram(
        material_type=material.model or material.get_type(),
        size=material.get_image_size(),
        channels=channels,
        buffers=buffers,
        images=images,
        warnings=warnings,
        texts=texts,
    )
