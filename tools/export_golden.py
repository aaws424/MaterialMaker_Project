"""Export reference ("golden") textures for every example material using
Material Maker's own command-line exporter.

The images produced here are the ground truth the Blender renderer is
compared against.

Usage:
    python tools/export_golden.py [--mm "E:/Program Files/material_maker_1_3_windows"]
                                  [--out golden] [--target Blender] [--only wood,bricks]
"""

import argparse
import json
import os
import subprocess
import sys
import time

DEFAULT_MM = r"E:\Program Files\material_maker_1_3_windows"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def export_one(mm_exe, mm_dir, ptex, out_dir, target, timeout):
    os.makedirs(out_dir, exist_ok=True)
    cmd = [mm_exe, "--export-material", "--target", target, "-o", out_dir, ptex]
    start = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=mm_dir, capture_output=True, text=True, timeout=timeout)
        code = proc.returncode
        log = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code = "timeout"
        log = ""
    elapsed = time.perf_counter() - start
    files = sorted(f for f in os.listdir(out_dir) if not f.endswith(".log"))
    if log.strip():
        with open(os.path.join(out_dir, "export.log"), "w", encoding="utf-8") as fh:
            fh.write(log)
    return {"exit": code, "seconds": round(elapsed, 1), "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mm", default=DEFAULT_MM, help="Material Maker install directory")
    parser.add_argument("--out", default=os.path.join(PROJECT_DIR, "golden"), help="output directory")
    parser.add_argument("--target", default="Blender", help="export target defined by the material node")
    parser.add_argument("--only", default="", help="comma separated example names (without .ptex)")
    parser.add_argument("--timeout", type=int, default=180, help="seconds per material")
    args = parser.parse_args()

    mm_exe = os.path.join(args.mm, "material_maker.exe")
    examples_dir = os.path.join(args.mm, "examples")
    names = sorted(f[:-5] for f in os.listdir(examples_dir) if f.endswith(".ptex"))
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        names = [n for n in names if n in wanted]

    manifest_path = os.path.join(args.out, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)

    for i, name in enumerate(names, 1):
        result = export_one(mm_exe, args.mm, os.path.join(examples_dir, name + ".ptex"),
                            os.path.join(args.out, name), args.target, args.timeout)
        manifest[name] = result
        print(f"[{i}/{len(names)}] {name}: exit={result['exit']} {result['seconds']}s files={len(result['files'])}",
              flush=True)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1, sort_keys=True)

    empty = [n for n in names if not manifest[n]["files"]]
    print(f"done: {len(names) - len(empty)} exported, {len(empty)} produced no files: {', '.join(empty)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
