"""Render example materials with the Python port inside Blender and compare them
with Material Maker's own exports in golden/ (see export_golden.py).

Run inside Blender (UI session, the gpu module needs a GPU context):
    import sys; sys.argv = ["", "--only", "wood,bricks"]
    exec(open(r".../tools/golden_compare.py").read())
or call run() after importing this file as a module.

Rendering goes through the add-on's MaterialRenderer.
"""

import os
import sys

import bpy
import numpy as np

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from mm_bridge.core import NodeLibrary, compile_material, load_ptex  # noqa: E402
from mm_bridge.gpu_render.renderer import MaterialRenderer  # noqa: E402

DEFAULT_MM = r"E:\Program Files\material_maker_1_3_windows"
GOLDEN_DIR = os.path.join(PROJECT_DIR, "golden")


def linear_to_srgb(c):
    c = np.clip(c, 0.0, None)
    return np.where(c < 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def load_golden(path):
    image = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = image.size
        pixels = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        return pixels.reshape(h, w, 4)
    finally:
        bpy.data.images.remove(image)


def compare_material(name, library, mm_dir=DEFAULT_MM):
    project = load_ptex(os.path.join(mm_dir, "examples", name + ".ptex"), library, mm_dir)
    program = compile_material(project)
    renderer = MaterialRenderer(program)
    results = {}
    try:
        rendered = renderer.render()
        random_textures = set()
        for buffer_pass in program.buffers:
            if buffer_pass.source.uses_randomness or random_textures & set(buffer_pass.samplers):
                random_textures.add(buffer_pass.name)
        for channel in program.channels.values():
            mine = rendered[channel.name]
            if channel.is_color:
                mine = np.concatenate([linear_to_srgb(mine[..., :3]), mine[..., 3:]], axis=-1)
            golden_path = os.path.join(GOLDEN_DIR, name, "%s_%s.%s" % (name, channel.name, channel.file_format))
            if not os.path.exists(golden_path):
                results[channel.name] = {"error": "no golden file"}
                continue
            golden = load_golden(golden_path)
            if golden.shape != mine.shape:
                results[channel.name] = {"error": "size %s vs %s" % (golden.shape, mine.shape)}
                continue
            greyscale = channel.texture.shader.output_type == "f"
            comps = slice(0, 1) if greyscale else slice(0, 3)
            if channel.file_format == "png":
                mine_q = np.round(np.clip(mine, 0.0, 1.0) * 255.0) / 255.0
            else:
                mine_q = mine
            a, b = mine_q[..., comps], golden[..., comps]
            diff = np.abs(a - b)
            # Hash-based randomness depends on the GPU's cos() precision (MM's own output differs
            # between GPUs), so random channels are compared statistically: mean/std per channel.
            results[channel.name] = {
                "mean_abs": round(float(diff.mean()), 5),
                "p99": round(float(np.percentile(diff, 99)), 5),
                "dmean": round(float(np.abs(a.mean(axis=(0, 1)) - b.mean(axis=(0, 1))).max()), 5),
                "dstd": round(float(np.abs(a.std(axis=(0, 1)) - b.std(axis=(0, 1))).max()), 5),
                "random": channel.texture.source.uses_randomness
                or bool(random_textures & set(channel.texture.samplers)),
            }
    finally:
        renderer.free()
    return results


def run(only=(), mm_dir=DEFAULT_MM):
    library = NodeLibrary.from_install(mm_dir)
    names = sorted(d for d in os.listdir(GOLDEN_DIR) if os.path.isdir(os.path.join(GOLDEN_DIR, d))
                   and os.listdir(os.path.join(GOLDEN_DIR, d)))
    if only:
        names = [n for n in names if n in only]
    report = {}
    for name in names:
        try:
            report[name] = compare_material(name, library, mm_dir)
        except Exception as e:
            report[name] = {"error": "%s: %s" % (type(e).__name__, str(e)[:500])}
    return report


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else ()
    for k, v in run(set(argv)).items():
        print(k, v)
