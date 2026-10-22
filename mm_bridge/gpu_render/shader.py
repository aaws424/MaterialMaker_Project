"""Build Blender GPU shaders from compiled Material Maker passes."""

import gpu


def create_shader(blender_shader, name="mm_pass"):
    """Compile a core.glsl.BlenderShader with Blender's gpu module.

    Raises an Exception with the driver's error log when compilation fails.
    """
    interface = gpu.types.GPUStageInterfaceInfo(name + "_iface")
    interface.smooth("VEC2", "mm_uv")

    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "pos")
    info.vertex_out(interface)
    info.push_constant("VEC4", "mm_frame")
    info.push_constant("VEC2", "mm_chunk_offset")
    info.push_constant("VEC4", "mm_p", blender_shader.param_slots)
    for slot, sampler in enumerate(blender_shader.samplers):
        info.sampler(slot, "FLOAT_2D", sampler)
    info.fragment_out(0, "VEC4", "mm_out")
    info.vertex_source(blender_shader.vertex_source)
    info.fragment_source(blender_shader.fragment_source)
    return gpu.shader.create_from_info(info)
