"""Unit tests for mm_bridge.core (plain Python, no Blender needed).

    python -m unittest discover -s tests -v

Tests that need Material Maker look for it in MM_INSTALL_DIR (environment
variable) or the default path below, and are skipped if it is missing.
"""

import json
import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from mm_bridge.core import NodeLibrary, compile_material, load_ptex  # noqa: E402
from mm_bridge.core.context import GenContext  # noqa: E402
from mm_bridge.core.glsl import to_blender_glsl  # noqa: E402
from mm_bridge.core.mmtypes import Color, Gradient  # noqa: E402
from mm_bridge.core.textutil import find_keyword_call, find_matching_parenthesis, replace_variables  # noqa: E402

MM_DIR = os.environ.get("MM_INSTALL_DIR", r"E:\Program Files\material_maker_1_3_windows")
HAVE_MM = os.path.isfile(os.path.join(MM_DIR, "nodes", "io_types.mmt"))
GOLDEN_MANIFEST = os.path.join(PROJECT_DIR, "golden", "manifest.json")


class TextUtilTests(unittest.TestCase):
    def test_matching_parenthesis(self):
        s = "f(a(b), c(d(e)))+g(x)"
        self.assertEqual(find_matching_parenthesis(s, 1), 15)
        self.assertEqual(find_matching_parenthesis("(a", 0), -1)

    def test_keyword_call(self):
        self.assertEqual(find_keyword_call("x = $in($uv+vec2(1.0, 0.0));", "in"), "$uv+vec2(1.0, 0.0)")
        self.assertIsNone(find_keyword_call("$input($uv)", "in"))

    def test_replace_variables_word_boundaries(self):
        variables = {"name": "o1", "name_uv": "o1_0", "uv": "(uv)"}
        self.assertEqual(replace_variables("$name_uv $name $(name)x $uv.x $uvs", variables),
                         "o1_0 o1 o1x (uv).x $uvs")


class TypesTests(unittest.TestCase):
    def test_gradient_shader_linear(self):
        g = Gradient.deserialize({"type": "Gradient", "interpolation": 1, "points": [
            {"pos": 1.0, "r": 1, "g": 1, "b": 1, "a": 1}, {"pos": 0.0, "r": 0, "g": 0, "b": 0, "a": 1}]})
        params = g.get_shader_params("o1_gradient")
        self.assertIn("uniform float p_o1_gradient_0_pos = 0.000000000;", params)
        self.assertIn("vec4 o1_gradient_gradient_fct(float x)", g.get_shader("o1_gradient"))

    def test_glsl_param_packing(self):
        from mm_bridge.core.compiler import ShaderSource
        src = ShaderSource(
            globals=["uniform sampler2D o3_tex;\nuniform float o3_tex_size = 512.0;\n"],
            defs="uniform float p_o1_a = 0.5;\nuniform vec4 p_o1_c = vec4(0.1, 0.2, 0.3, 1.0);\n"
                 "uniform float seed_o1 = -42.000000000;\n",
            code="float v = p_o1_a;\n", rgba="vec4(v)", output_type="f")
        shader = to_blender_glsl(src, "")
        self.assertEqual(shader.samplers, ["o3_tex"])
        index = shader.param_index()
        self.assertEqual(index["p_o1_c"].default, (0.1, 0.2, 0.3, 1.0))
        self.assertEqual(index["seed_o1"].default, (-42.0,))
        packed = shader.pack_params({"p_o1_a": 0.75})
        a = index["p_o1_a"]
        self.assertEqual(packed[4 * a.slot + a.component], 0.75)
        self.assertNotIn("uniform", shader.fragment_source.split("void main")[0].replace("// Generated", ""))


@unittest.skipUnless(HAVE_MM, "Material Maker not found (set MM_INSTALL_DIR)")
class ExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = NodeLibrary.from_install(MM_DIR)
        cls.examples = os.path.join(MM_DIR, "examples")

    def load(self, name):
        return load_ptex(os.path.join(self.examples, name + ".ptex"), self.library, MM_DIR)

    def test_library(self):
        self.assertFalse(self.library.errors)
        self.assertIn("material", self.library.get_material_nodes())

    def test_seeds_match_material_maker(self):
        # Values read from the shader Material Maker 1.3 generates for wood.ptex.
        project = self.load("wood")
        seeds = {g.name: g.get_seed() for g in project.iter_generators() if g.has_randomness() and g.parent}
        self.assertEqual(seeds, {"perlin_2": -60870.0, "perlin_1": -12466.0, "perlin_0": -12599.0,
                                 "voronoi_0": -51094.0})

    def test_remote_parameters_reach_linked_nodes(self):
        # normal_map is a library graph whose gen_parameters drive an inner buffer's size.
        project = self.load("wood")
        normal_map = project.root.get_node("normal_map_0")
        self.assertEqual(normal_map.get_node("buffer").get_parameter("size"), 11.0)

    def test_variants_reused(self):
        project = self.load("wood")
        material = project.find_material()
        context = GenContext()
        first = material.get_shader_code("uv", 0, context)
        second = material.get_shader_code("uv", 0, context)
        self.assertEqual(first["rgba"], second["rgba"])
        self.assertEqual(second["code"], "")

    def test_uniform_values_match_compiled_defaults(self):
        # Live parameter values must cover every shader parameter and equal what the code was compiled with.
        from mm_bridge.core.params import uniform_values
        for name in ("wood", "bricks", "medieval_wall", "mmm_donuts", "3d_shapes"):
            with self.subTest(example=name):
                project = self.load(name)
                program = compile_material(project)
                values = uniform_values(project)
                for p in program.passes():
                    for param in p.shader.params:
                        self.assertIn(param.name, values, "%s/%s" % (p.name, param.name))
                        for a, b in zip(values[param.name], param.default):
                            self.assertAlmostEqual(a, b, places=5, msg="%s/%s" % (p.name, param.name))

    def test_parameter_changes(self):
        from mm_bridge.core.mmtypes import Gradient
        from mm_bridge.core.params import uniform_values
        project = self.load("wood")
        perlin = project.root.get_node("perlin_2")
        self.assertFalse(project.set_parameter(perlin, "scale_x", 16.0))
        self.assertEqual(uniform_values(project)["p_o%d_scale_x" % perlin.uid], (16.0,))
        self.assertTrue(project.set_parameter(perlin, "scale_x", "$rnd(1, 4)"))  # expression: new code
        colorize = project.root.get_node("colorize_2")
        g = Gradient.deserialize(colorize.parameters["gradient"].serialize())
        self.assertFalse(project.set_parameter(colorize, "gradient", g))
        g2 = Gradient.deserialize(g.serialize())
        g2.add_point(0.5, g.points[0].c)
        self.assertTrue(project.set_parameter(colorize, "gradient", g2))  # point count changed
        self.assertTrue(project.set_parameter(project.root.get_node("blend_0"), "blend_type", 0.0))
        # Graph parameter -> remote -> linked buffer size: structural.
        self.assertTrue(project.set_parameter(project.root.get_node("normal_map_0"), "param0", 9.0))
        # Graph parameter -> remote -> linked float: uniforms only.
        normal_map = project.root.get_node("normal_map_0")
        float_params = [p["name"] for p in normal_map.get_parameter_defs() if p.get("type") == "float"]
        self.assertTrue(float_params)
        self.assertFalse(project.set_parameter(normal_map, float_params[0], 0.5))

    def test_schema(self):
        from mm_bridge.core import schema as S
        for name in ("wood", "bricks", "medieval_wall"):
            with self.subTest(example=name):
                project = self.load(name)
                sections = S.build_schema(project)
                keys = [spec.key for s in sections for spec in s.specs]
                self.assertEqual(len(keys), len(set(keys)))
                for s in sections:
                    for spec in s.specs:
                        self.assertIsNotNone(S.resolve(project, spec.gen_path), spec.gen_path)
                self.assertEqual(sum(s.kind == "material" for s in sections), 1)
        # A gradient is one spec carrying the file's Gradient (its widget lives in a helper node).
        project = self.load("wood")
        sections = S.build_schema(project)
        grads = [spec for s in sections for spec in s.specs if spec.gen_path == "colorize_2"]
        self.assertEqual([g.kind for g in grads], [S.GRADIENT])
        original = project.root.get_node("colorize_2").parameters["gradient"]
        self.assertEqual(grads[0].value.serialize(), original.serialize())
        # Exposed controls come first (bricks has a top-level remote).
        self.assertEqual(S.build_schema(self.load("bricks"))[0].kind, "controls")

    def load_data(self, name):
        return load_ptex(os.path.join(PROJECT_DIR, "tests", "data", name + ".ptex"), self.library, MM_DIR)

    def test_iterate_buffer(self):
        from mm_bridge.core.compiler import IteratePass
        program = compile_material(self.load_data("filltest"))
        self.assertEqual(program.warnings, [])
        iterate = [b for b in program.buffers if isinstance(b, IteratePass)]
        self.assertEqual(len(iterate), 1)
        it = iterate[0]
        self.assertEqual(it.loop_settings(), (12, False, False))
        self.assertIn(it.loop_texture, it.loop.samplers)          # the loop reads its own previous result
        self.assertNotIn(it.loop_texture, it.samplers)            # ...which is not an outside dependency
        self.assertIn(it.iteration_uniform, it.loop.shader.param_index())
        # Iteration count is a renderer setting, not a recompile.
        project = it.generator.project
        self.assertFalse(project.set_parameter(it.generator, "iterations", 20.0))
        self.assertEqual(it.loop_settings()[0], 20)

    def test_sdf(self):
        from mm_bridge.core.generators import GenSDF
        from mm_bridge.core.sdf import BUILDER
        project = self.load_data("sdftest")
        sdf2d, sdf3d = project.root.get_node("sdf_0"), project.root.get_node("sdf_1")
        self.assertIsInstance(sdf2d, GenSDF)
        self.assertEqual([o["type"] for o in sdf2d.get_output_defs()], ["sdf2d", "rgba", "f", "f", "rgb"])
        self.assertEqual(sdf3d.get_output_defs()[0]["type"], "sdf3d")
        self.assertIn("sd_box(", sdf2d.shader_model["instance"])
        self.assertIn("sdTorus(", sdf3d.shader_model["instance"])
        program = compile_material(project)
        self.assertEqual(program.warnings, [])
        for item in BUILDER.items.values():  # every registered item's includes exist in the library
            for inc in item.get_includes():
                self.assertTrue(self.library.get_predefined_global(inc), inc)

    def test_text(self):
        program = compile_material(self.load_data("texttest"))
        self.assertEqual(len(program.texts), 2)
        settings = sorted(g.text_settings() for g in program.texts.values())
        self.assertEqual(settings[0][0], "Hello Blender")

    def test_time_gradient_curve_schema(self):
        from mm_bridge.core import schema as S
        project = self.load_data("p4test")
        kinds = {(spec.gen_path, spec.kind) for s in S.build_schema(project) for spec in s.specs}
        for expected in (("tonality_0", S.CURVE), ("colorize_0", S.GRADIENT), ("transform_0", S.EXPRESSION),
                         ("text_0", S.STRING), ("text_0", S.FILE)):
            self.assertIn(expected, kinds)
        program = compile_material(project)
        self.assertTrue(any("elapsed_time" in p.source.code + p.source.defs for p in program.passes()))

    def test_serialize_round_trip(self):
        # Load -> save -> load gives the same shaders (whitespace aside: MM's saver turns 4 spaces into tabs).
        import re
        from mm_bridge.core.serialize import project_to_ptex
        norm = lambda passes: [re.sub(r"\s+", " ", p.shader.fragment_source) for p in passes]  # noqa: E731
        names = [os.path.join(self.examples, f) for f in sorted(os.listdir(self.examples)) if f.endswith(".ptex")]
        names += [os.path.join(PROJECT_DIR, "tests", "data", f) for f in os.listdir(os.path.join(PROJECT_DIR, "tests", "data"))]
        for path in names:
            with self.subTest(file=os.path.basename(path)):
                p1 = load_ptex(path, self.library, MM_DIR)
                if p1.find_material().model == "material_unlit":
                    continue
                p2 = load_ptex(project_to_ptex(p1), self.library, MM_DIR, project_dir=os.path.dirname(path))
                self.assertEqual(norm(compile_material(p1).passes()), norm(compile_material(p2).passes()))

    def test_graph_edits(self):
        from mm_bridge.core import graph_edit as G
        from mm_bridge.core import schema as S
        from mm_bridge.core.serialize import project_to_ptex
        project = self.load("wood")
        root = project.root
        keys_before = {spec.key: spec.gen_path for s in S.build_schema(project) for spec in s.specs}
        # Every library entry can be added.
        self.assertGreater(len(self.library.menu), 200)
        entry = next(data for path, data in self.library.menu if path == "Noise/Voronoi")
        gen = G.add_node(project, root, entry, (10.0, 20.0))
        self.assertTrue(gen.name.startswith("voronoi"))
        mat = project.find_material()
        inputs = [i["name"] for i in mat.get_input_defs()]
        G.connect(project, root, gen.name, 1, "Material", inputs.index("roughness_tex"))
        # Incompatible ports and loops are refused.
        with self.assertRaises(G.EditError):
            G.connect(project, root, "perlin_2", 0, "Material", 99)
        with self.assertRaises(G.EditError):
            G.connect(project, root, "colorize_0", 0, "blend_0", 0)   # blend_0 feeds colorize_0
        with self.assertRaises(G.EditError):
            G.remove_node(root, "Material")
        copy = G.duplicate_node(project, root, "perlin_2", (0.0, 0.0))
        self.assertNotEqual(copy.name, "perlin_2")
        G.remove_node(root, "warp_1")
        self.assertFalse(any("warp_1" in (c["from"], c["to"]) for c in root.connections))
        # The edited graph serializes, reloads and compiles; keys of untouched nodes are unchanged.
        p2 = load_ptex(project_to_ptex(project), self.library, MM_DIR)
        program = compile_material(p2)
        self.assertIn("rough", program.channels)
        keys_after = {spec.key: spec.gen_path for s in S.build_schema(p2) for spec in s.specs}
        for key, path in keys_before.items():
            if path != "warp_1":
                self.assertEqual(keys_after.get(key), path)

    def test_iterate_feedback_is_not_a_loop(self):
        from mm_bridge.core import graph_edit as G
        fill = self.load_data("filltest").root.get_node("fill_0")
        # The fill graph's own feedback (iterate output 1 -> fill_iterate -> iterate input 1) is legal.
        self.assertFalse(G.creates_loop(fill, "fill_iterate", 0, "iterate_buffer", 1))

    def test_all_examples_compile(self):
        manifest = {}
        if os.path.exists(GOLDEN_MANIFEST):
            with open(GOLDEN_MANIFEST, encoding="utf-8") as fh:
                manifest = json.load(fh)
        names = sorted(f[:-5] for f in os.listdir(self.examples) if f.endswith(".ptex"))
        for name in names:
            with self.subTest(example=name):
                project = self.load(name)
                if project.find_material().model == "material_unlit":
                    continue  # no Blender export profile
                program = compile_material(project)
                self.assertEqual(program.warnings, [])
                for p in program.passes():
                    # Markers the generator leaves when a substitution fails.
                    self.assertNotIn("*error missing", p.shader.fragment_source)
                    self.assertNotIn("(error:", p.shader.fragment_source)
                    self.assertNotIn("$", p.shader.fragment_source)
                if name in manifest:
                    golden = sorted(f[len(name) + 1:].rsplit(".", 1)[0] for f in manifest[name]["files"])
                    self.assertEqual(sorted(program.channels), golden)


if __name__ == "__main__":
    unittest.main()
