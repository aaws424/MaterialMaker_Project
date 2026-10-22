"""Compile every example material with the Python port.

Runs with plain Python (code generation only), or inside Blender where it also
compiles every generated shader on the GPU.

Usage:
    python tools/compile_all.py [--mm DIR] [--dump OUT_DIR] [--only a,b]
    In Blender: exec(open(r".../tools/compile_all.py").read()) with MM_ARGS set, or
    blender --python tools/compile_all.py -- --gpu
"""

import argparse
import os
import sys
import time
import traceback

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from mm_bridge.core import NodeLibrary, compile_material, load_ptex  # noqa: E402

DEFAULT_MM = r"E:\Program Files\material_maker_1_3_windows"


def run(mm_dir=DEFAULT_MM, only=(), dump_dir="", gpu_compile=False):
    library = NodeLibrary.from_install(mm_dir)
    examples_dir = os.path.join(mm_dir, "examples")
    names = sorted(f[:-5] for f in os.listdir(examples_dir) if f.endswith(".ptex"))
    if only:
        names = [n for n in names if n in only]
    create_shader = None
    if gpu_compile:
        from mm_bridge.gpu_render.shader import create_shader
    report = {}
    for name in names:
        entry = report[name] = {"ok": False}
        start = time.perf_counter()
        try:
            project = load_ptex(os.path.join(examples_dir, name + ".ptex"), library, mm_dir)
            program = compile_material(project)
        except Exception as e:  # report and keep going
            entry["error"] = "%s: %s" % (type(e).__name__, e)
            entry["traceback"] = traceback.format_exc()
            continue
        entry.update(
            channels=list(program.channels),
            buffers=len(program.buffers),
            images=len(program.images),
            warnings=program.warnings,
            gen_seconds=round(time.perf_counter() - start, 3),
            ok=True,
        )
        if dump_dir:
            out = os.path.join(dump_dir, name)
            os.makedirs(out, exist_ok=True)
            for p in program.passes():
                with open(os.path.join(out, p.name + ".frag"), "w", encoding="utf-8") as fh:
                    fh.write(p.shader.fragment_source)
        if create_shader is not None:
            gpu_errors = {}
            start = time.perf_counter()
            for p in program.passes():
                try:
                    create_shader(p.shader, "mm_" + p.name)
                except Exception as e:
                    gpu_errors[p.name] = str(e)[:2000]
            entry["gpu_seconds"] = round(time.perf_counter() - start, 3)
            entry["gpu_errors"] = gpu_errors
            entry["ok"] = not gpu_errors
    return report


def summarize(report):
    lines = []
    for name, e in report.items():
        if "error" in e:
            lines.append("FAIL  %-22s %s" % (name, e["error"]))
        elif e.get("gpu_errors"):
            lines.append("GPU   %-22s %s" % (name, ", ".join(e["gpu_errors"])))
        else:
            extra = " gpu=%.2fs" % e["gpu_seconds"] if "gpu_seconds" in e else ""
            lines.append("ok    %-22s %d ch, %d buf, %d warn, gen=%.3fs%s" % (
                name, len(e["channels"]), e["buffers"], len(e["warnings"]), e["gen_seconds"], extra))
    ok = sum(1 for e in report.values() if e["ok"])
    lines.append("%d/%d ok" % (ok, len(report)))
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mm", default=DEFAULT_MM)
    parser.add_argument("--only", default="")
    parser.add_argument("--dump", default="")
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args(argv)
    only = {n.strip() for n in args.only.split(",") if n.strip()}
    report = run(args.mm, only, args.dump, args.gpu)
    print(summarize(report))
    for name, e in report.items():
        if "traceback" in e:
            print("\n==", name, "\n", e["traceback"])
    return 0 if all(e["ok"] for e in report.values()) else 1


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    sys.exit(main(argv))
