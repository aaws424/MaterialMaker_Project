# Material Maker Bridge

A Blender add-on that opens [Material Maker](https://github.com/RodZill4/material-maker)
procedural materials (`.ptex` files) directly in Blender. You can change their parameters, and
even edit their node graph, and see the result live in the viewport, without leaving Blender
and without Material Maker running.

## What it does

- **Imports `.ptex` materials.** File › Import › Material Maker (.ptex) creates a Blender
  material wired to a Principled BSDF (base color, roughness, metallic, normal, emission,
  subsurface, displacement), renders its textures and assigns it to the selected objects.
- **Live parameters.** Every parameter of every node appears in a "Material Maker" panel
  with Material Maker's own ranges and steps. Dragging a slider re-renders the textures in
  milliseconds: a fast preview while you drag, full resolution once you stop.
- **Native widgets for gradients and curves.** Gradients use Blender's color ramp, curves
  use Blender's curve editor.
- **Animation.** Parameters can be keyframed, and materials that use Material Maker's
  `$time` animate with the timeline.
- **Graph editor.** "Edit Graph" shows the material's node graph in Blender's node editor.
  You can add nodes from Material Maker's library (Shift+A), link, unlink, delete and
  duplicate nodes, and open groups. Edits follow Material Maker's rules (port types, no loops).
- **Saves back to Material Maker.** "Save .ptex" writes the edited graph and its current
  parameters to a file that Material Maker opens.
- **Matches Material Maker.** The textures are the ones Material Maker produces for the same
  file (see [Accuracy](#accuracy)).

## How it works

Material Maker nodes are small pieces of GLSL shader code. Material Maker assembles them into
one shader per texture and renders it on the GPU. This add-on does the same thing inside
Blender, with no Material Maker process involved:

```
.ptex file ──► loader ──► generator tree ──► compiler ──► one GLSL shader per pass
                (reads your MM install's                    │
                 node library: nodes/*.mmg)                 ▼
                                               Blender GPU renderer (gpu module)
                                                            │
                                    float image datablocks (albedo, rough, normal, ...)
                                                            │
                                         Principled BSDF material in Blender
```

1. **Loading.** The `.ptex` file is a JSON node graph. Each node refers to a definition in
   Material Maker's library (`nodes/*.mmg`), which the add-on reads from your Material Maker
   install.
2. **Compiling.** A Python port of Material Maker 1.3's shader generator turns the graph into
   GLSL, exactly as Material Maker would: same code, same random seeds, same parameter values.
3. **Rendering.** Blender's `gpu` module compiles and runs those shaders off screen, and the
   results are written into Blender images used by the material.
4. **Live editing.** Parameter values are passed to the shaders as uniforms, so most changes
   only re-run the existing shaders (no recompiling), and only the passes affected by the
   change are redrawn. Changes that alter the code (an enum, a buffer size, a new link)
   recompile; compiled shaders are cached.

**Buffers.** Some nodes (blur, normal map, occlusion) sample their input many times. Material
Maker puts a buffer in front of them, and so does the add-on: each buffer is rendered as its
own pass into a half-float texture at the buffer's size, before the passes that read it, and is
re-rendered only when its own inputs or parameters change. Buffers read at lower detail levels
(fast blur) get a mip chain the add-on builds itself; iterate buffers (fill nodes) run their
loop pass N times.

**Graph edits.** The node editor shows a view of the material's graph. When you link, delete or
add a node, the change is applied to the graph with Material Maker's rules, the graph is written
back to `.ptex` form and stored in the material (so it is saved with the `.blend`), and the
material is recompiled.

## Requirements

- Blender 4.2 or newer (developed and tested with Blender 5.2 LTS, OpenGL backend).
- Material Maker 1.3 installed (the add-on reads its `nodes` and `library` folders; Material
  Maker does not need to be running).
- A GPU that runs Blender's viewport.

## Installation

1. Build the extension package from the `mm_bridge` folder:

   ```bash
   blender --command extension build --source-dir mm_bridge --output-dir .
   ```

2. In Blender: Edit › Preferences › Get Extensions › (menu) Install from Disk, and pick the
   generated `.zip`.
3. In the add-on's preferences, set the Material Maker folder (the folder containing
   `material_maker.exe` and `nodes`). The search button finds common install locations.

## Using it

**Import and tweak**

1. File › Import › Material Maker (.ptex). Choose a resolution (or keep the file's), and keep
   "Assign to Selected" on to put it on the selected objects.
2. Open Material Properties › Material Maker, or the "Material Maker" tab in the 3D View or
   Shader Editor sidebar (N). There is one collapsible section per node; exposed controls of
   the material come first, and groups are nested.
3. Drag any value. The textures update live ("Drag" sets the preview resolution).
4. The "Material" section (albedo color, metallic, roughness, normal strength, emission,
   depth) adjusts the Blender material directly, without re-rendering.

Header buttons: Re-render, Reload from File (back to the file's values), Edit Graph,
Save .ptex, resolution, preview resolution, and a live-update toggle.

**Edit the graph**

1. Click Edit Graph. A node editor shows the "Material Maker Graph" of the material.
2. Shift+A adds a node from Material Maker's library (searchable, by category).
3. Link, unlink, delete (X) and duplicate (Shift+D) as usual; Open Group enters a group and
   Parent (sidebar) goes back up. The active node's parameters are also in the sidebar.
4. Save .ptex writes the result for Material Maker.

**Animate**

Keyframe any parameter (hover it and press I), or use a material that reads `$time`
(seconds = frame / fps). Textures follow the timeline: preview resolution while playing,
full resolution when playback stops.

## Accuracy

Each example material that ships with Material Maker 1.3 was exported by Material Maker itself
and compared with the add-on's render:

- All 42 examples that have a Blender export compile and render; their texture channels are the
  same ones Material Maker exports.
- Channels without randomness match within 1/255. Iterate buffers (fill), EasySDF 2D/3D and
  mipmapped blurs were checked separately and match to half-float precision or exactly.
- Channels built on random noise match in look and statistics, but not pixel for pixel.
  Material Maker's hash function amplifies the GPU's `cos()` rounding by about 40,000×, so the
  exact random layout depends on how the driver evaluates `cos`. Material Maker's own output
  differs the same way between GPUs.
- Graphs saved by the add-on open in Material Maker, and Material Maker's export of them
  matches the add-on's render.

## Limitations

- **Textures are not stored in the `.blend` yet.** They are rendered again when the file is
  opened, so rendering on another machine or a render farm needs the add-on (packing and
  baking are planned).
- **F12 animation renders** keep the textures of the current frame: Blender evaluates animation
  render frames off the main thread, where the GPU cannot be used (baking image sequences is
  planned).
- **Polygon and polyline parameters** are not editable in the panel yet.
- **Text nodes** use Blender's default font unless the node sets a font file (Material Maker
  uses Godot's default font).
- **Unlit materials** (`material_unlit`) have no Blender export profile and are not imported.
- Undo/redo of parameter changes is handled but not yet tested extensively.

## Why Material Maker 1.3

The add-on reads the node library of your Material Maker install, and its shader generator
is a port of Material Maker 1.3's, so the code it produces matches that library exactly.
Material Maker 1.4 and later run on Godot 4 and generate shaders differently (buffers use
compute shaders, among other changes). Supporting them means porting that generator and
testing it against their exports, which is planned as a separate step.

## Development

```
mm_bridge/                 the add-on (Blender extension)
  core/                    pure Python, no bpy (unit-testable without Blender)
    library.py             node library (nodes/*.mmg, io types, library menu)
    loader.py              .ptex -> generator tree
    generators.py          generators: shader, graph, remote, switch, buffer, iterate buffer,
                           image, text, EasySDF, material
    compiler.py            material -> render passes (buffers first, then channels)
    glsl.py                Material Maker code -> Blender GLSL
    params.py              live uniform values; which changes need a recompile
    schema.py              editable parameters, laid out like Material Maker's inspector
    serialize.py           project -> .ptex
    graph_edit.py          add / remove / duplicate / connect with Material Maker's rules
    sdf.py                 EasySDF builder (64 item types)
  gpu_render/              GPU shaders, renderer (buffers, mip atlases), image writing
  session.py               live sessions: apply changes, recompile, preview/full renders
  graph_editor.py          "Material Maker Graph" node tree, Add menu, Save as .ptex
  material_builder.py      Principled BSDF node setup
  helpers.py               color ramp / curve helper nodes for gradients and curves
  props.py, ui.py, operators.py, preferences.py
tests/                     unit tests and small test materials (tests/data)
tools/                     export reference textures with Material Maker, compare renders
```

Run the tests (plain Python, no Blender needed):

```bash
python -m unittest discover -s tests -v
```

Export reference textures of every example with Material Maker's command line (into
`golden/`, not committed):

```bash
python tools/export_golden.py
```

`tools/golden_compare.py` renders the examples inside Blender and compares them with
`golden/`. Tests and tools look for Material Maker in
`E:\Program Files\material_maker_1_3_windows`; set `MM_INSTALL_DIR` (tests) or `--mm` (tools)
for another location.

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 0–1 | Add-on skeleton; `.ptex` → GLSL compiler | done |
| 2 | GPU renderer: buffers, mipmaps, fast re-render | done |
| 3 | Import, Principled BSDF setup, live parameter panel | done |
| 4 | Remaining node types, gradient/curve widgets, `$time`, keyframes | done |
| 5 | Graph editor in Blender, save back to `.ptex` | done |
| 6 | Pack/bake textures into the `.blend`, image sequences for animation, release packaging | next |

## Credits and license

Material Maker is © 2018–2023 Rodolphe Suescun and contributors, under the MIT License.
`mm_bridge/core` contains a port of its shader generator and SDF builder. The add-on is
licensed GPL-3.0-or-later (see `mm_bridge/blender_manifest.toml`).
