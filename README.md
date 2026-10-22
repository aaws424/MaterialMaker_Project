# Material Maker Bridge

A Blender add-on that opens Material Maker procedural materials (`.ptex`) and
renders them inside Blender, so their parameters can be tweaked live.

It does not reimplement Material Maker's nodes. It ports Material Maker's
graph-to-GLSL generator to Python, then renders the generated shaders with
Blender's `gpu` module, so all ~300 Material Maker nodes work from their
`.mmg` definitions.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Add-on skeleton, preferences, golden exports | done |
| 1 | `.ptex` → GLSL compiler (pure Python) | done |
| 2 | GPU renderer: sampler state, mipmaps, render into Blender images, fast re-render | done |
| 3 | Import operator, Principled BSDF wiring, live parameter panel | done |
| 4 | Remaining node types, gradient/curve widgets, `$time` | done |
| 5 | MM graph editor inside Blender, save back to `.ptex` | done |
| 6 | Pack/bake textures into the .blend, image sequences for animation, release packaging | next |

## Layout

```
mm_bridge/                 the add-on (Blender 4.2+ extension)
  core/                    pure Python, no bpy: unit-testable anywhere
    library.py             loads nodes/*.mmg and io_types.mmt from an MM install
    loader.py              .ptex -> generator tree (port of loader.gd)
    generators.py          shader/graph/ios/remote/switch/buffer/image (port of gen_*.gd)
    mmtypes.py             Color, Gradient, Curve, Polygon
    compiler.py            material -> texture passes (channels + buffers, dependency ordered)
    glsl.py                Godot-flavoured code -> Blender GLSL (params packed in vec4 mm_p[],
                           mip-atlas lookups, sRGB->linear for color channels)
    params.py              live uniform values + which changes need a recompile
    schema.py              editable parameters laid out like MM's inspector
    serialize.py           write a project back to .ptex like Material Maker's saver
    graph_edit.py          add / remove / duplicate nodes, connect with MM's type and loop rules
    sdf.py                 EasySDF builder port (64 item types: 2D/3D shapes, booleans, colors, textures)
    common.glsl            port of MM's common.shader
  gpu_render/shader.py     GPUShaderCreateInfo for a compiled pass
  gpu_render/renderer.py   MaterialRenderer: cached shaders, buffers, mip atlases, incremental re-render
  gpu_render/images.py     float image datablocks per channel (Linear Rec.709 / Non-Color)
  preferences.py           MM install folder (with auto-detect)
  props.py                 per-material settings + one generated PropertyGroup per parameter layout
  session.py               live sessions: apply changes, recompile when needed, preview/full renders
  material_builder.py      Principled BSDF node setup and Material Maker's material multipliers
  helpers.py               Color Ramp / Float Curve helper nodes behind gradient and curve parameters
  operators.py             File > Import > Material Maker (.ptex), Re-render, Reload, Randomize Seed
  graph_editor.py          "Material Maker Graph" node tree: view of one graph level, edits, Add menu,
                           Save as .ptex
  ui.py                    "Material Maker" panel (Material Properties, 3D View and Shader Editor sidebars)
tools/
  export_golden.py         MM command-line export of every example -> golden/
  compile_all.py           compile all examples (add --gpu inside Blender)
  golden_compare.py        render in Blender, compare against golden/ (needs a UI session)
tests/test_core.py         python -m unittest discover -s tests -v
golden/                    reference textures exported by Material Maker 1.3 (generated, 264 MB)
```

## Running

```bash
python -m unittest discover -s tests -v
```

```bash
python tools/compile_all.py
```

```bash
python tools/export_golden.py
```

Tests and tools look for Material Maker in `E:\Program Files\material_maker_1_3_windows`;
set `MM_INSTALL_DIR` (tests) or `--mm` (tools) to use another install.

## Findings so far

- All 42 example materials with a Blender export compile to GLSL, and every
  generated shader compiles on the GPU in Blender 5.2. Channel lists match
  Material Maker's exports exactly. `load_effect` uses `material_unlit`,
  which has no Blender export profile.
- Seeds and the generated code match the shader Material Maker generates
  (checked against MM's own dynamic-material export of `wood.ptex`).
- Deterministic channels match MM's exported textures to within 1/255
  (41 of 42 after Phase 2; the remaining one, pentagram, hashes internally).
- Random channels match statistically but not pixel for pixel. MM's hash,
  `fract(cos(mod(dot(...), 3.14)) * 43758.5453)`, amplifies the GPU's `cos()`
  rounding about 40,000x, so the random layout depends on how the driver
  evaluates `cos`. MM's own output differs the same way between GPUs.
- Generation takes 2–700 ms per material in Python. GPU compile takes
  0.03–3.7 s per material for all its passes. Rendering a 1024² pass takes
  about 12 ms.

## Using the add-on

1. Install `mm_bridge` as an extension (or zip it), then set the Material Maker folder in its
   preferences (the search button finds common install locations).
2. File > Import > Material Maker (.ptex). The material is created, its textures are rendered
   and it is assigned to the selected objects.
3. Edit parameters in Material Properties > Material Maker, or in the "Material Maker" tab of
   the 3D View / Shader Editor sidebar. One collapsible section per node, user-made groups
   nested; exposed controls (remote nodes) first.

While a value is being dragged, textures render at the "Drag" resolution (512 by default),
then at full resolution 0.3 s after the last change. The "Material" section (albedo color,
metallic, roughness, normal strength, emission, depth) only changes node values, no render.
Parameter values are Blender properties saved in the .blend; an undo/redo handler re-syncs
them (not exercised in testing yet). Gradients and curves use Blender's own color ramp and
curve widgets (hidden helper nodes in the material). Keyframed parameters and `$time`
(seconds = frame / fps) re-render on frame change: at the Drag resolution while playing,
full resolution once playback stops. Animation renders (F12) evaluate frames off the main
thread, where the GPU cannot be used, so they keep the textures of the current frame (Phase 6:
bake image sequences). Textures are rendered again when a file is opened; they are not saved
into the .blend yet (Phase 6: pack/bake).

## Graph editor (Phase 5)

Material panel > Edit Graph opens a "Material Maker Graph" node tree in a node editor: one
node per Material Maker node, parameters inline, links as in the .ptex. Groups made in
Material Maker open with "Open Group" (the sidebar has a Parent button). Shift+A adds nodes
from Material Maker's library (library/base.json: 250 entries, searchable). Linking,
unlinking, deleting and duplicating (Shift+D) nodes are applied to the graph with Material
Maker's rules (port types, no loops, protected Material/group IO nodes); the edited graph is
re-serialized and becomes the material's source, so it survives saving the .blend.
Save as .ptex writes it for Material Maker.

Verified: every example round-trips load -> save -> load to identical shaders; every one of
the 250 library entries can be added and compiles when wired to the material; a graph edited
in Python and saved by the add-on opens in Material Maker 1.3, whose export matches our render
(exactly for the deterministic channels).

Verified in Blender (a separate, empty Blender 5.2 instance): opening the graph view; adding a
node with the Add operator; linking it into the material (project, stored source and textures
update); a link that would create a loop is refused and reported in the sidebar; deleting and
Shift+D duplicating nodes; opening a group, editing inside it, and going back up; Save as .ptex
from the UI, which Material Maker 1.3 opens and exports (all channels match our render).

Crash fixed during this phase: NodeTree.update() is also called while classes register (add-on
enabled on a file that contains graph trees), and reading the tree's own properties there is an
access violation in Blender 5.2. update() now touches no Blender data; it only flags, and the
timer diffs the trees. Re-registering with a graph tree present and reopening a saved .blend
both work now. A second bug found there: Blender drops non-persistent timers when a file loads,
so sessions were not restored after opening a .blend; the timer is persistent now.

## Node types

All `.mmg` shader nodes and graphs, plus the built-in generators: graph, ios, remote,
switch, reroute, buffer (with mipmaps), iterate_buffer (fill nodes), image, text, EasySDF
(sdf), comment. Verified against Material Maker's own exports with test materials in
`tests/data/`:

- iterate_buffer (bricks -> fill -> fill_to_position / fill_to_size, 12 iterations): exact.
- EasySDF 2D (smooth union, colors, pattern texture) and 3D (torus + box, raymarched): within
  1/255 / half-float precision.
- text: same placement and size; glyphs use Blender's font unless the node sets a font file
  (Material Maker uses Godot's default font).

Not editable in the panel yet: polygon / polyline parameters. Not ported: brush, export,
webcam nodes (not material nodes).

## Renderer notes

- Buffers render to RGBA16F (Godot's HDR viewports) with REPEAT + linear filtering.
- Blender 5.2's `GPUTexture.mipmap_mode()` binding raises `SystemError`, and offscreen
  textures have no mip levels, so buffers sampled with `textureLod(..., lod>0)`
  (fast_blur, occlusion2) get a mip atlas built with 2x2 box downsampling (like Godot's
  `Image.generate_mipmaps`). The generated GLSL does trilinear lookups into it with
  REPEAT wrapping. Verified against MM on a fast_blur test material (error ~3e-5).
- `mm_frame.w` flips V: 1 for passes read back into Blender images, 0 for buffers.
- Textures larger than 2048 render in 2048 tiles (as MM does), which keeps single draws short.
- Live editing: `Project.set_parameter()` returns whether the shaders must be regenerated
  (MM's own rules: float/color/gradient/curve value changes don't). Otherwise
  `params.uniform_values()` + `MaterialRenderer.render()` redraws only passes whose
  parameters or input textures changed.
- Timings for wood at 2048² on an RTX 3050: GPU compile 0.34 s; first render 0.44 s;
  float change, re-render, and 4 images written 0.21 s; gradient color change
  (albedo only) 0.07 s; 512² preview render 26 ms; enum change (recompile) 0.96 s.
- Golden comparison with this renderer: 41/42 deterministic channels within 1/255
  (pentagram's runes hash internally, so it is really random); random channels as before.
- iterate_buffer: init pass, then `iterations` loop passes ping-ponging two textures; the
  `o<id>_iteration` uniform counts 1..N; nearest + clamped sampling (Godot `flags = 0`).
  Iteration count, shrink and autostop are read at render time (no recompile).
- text: drawn with `blf` into an offscreen texture, flipped into Material Maker row order.
- Large graphs pack 100+ vec4 params (tested only on the OpenGL backend so far).
  Verify on Vulkan; move `mm_p` to a uniform buffer if a push-constant limit is hit.

Material Maker is © 2018-2023 Rodolphe Suescun and contributors, MIT License.
`mm_bridge/core` contains a port of its generator.
