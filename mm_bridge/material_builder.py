"""Build the Blender node tree for a Material Maker material.

Only nodes named "MMB ..." belong to the add-on; rebuilding removes and recreates
them and leaves any other node in the tree alone.

Material Maker's material node multiplies its textures by its own parameters
(ALBEDO = albedo_tex * albedo_color, ...; an unconnected texture input counts as
its default, 1.0 for metallic/roughness). The same happens here with math/mix nodes
whose values follow the "Material" section of the panel.
"""

import bpy

PREFIX = "MMB "
X0 = -1300


def _sock(sockets, identifier):
    for s in sockets:
        if s.identifier == identifier:
            return s
    return sockets[identifier]


def _new(nodes, type_name, name, label, location, parent=None):
    node = nodes.new(type_name)
    node.name = PREFIX + name
    node.label = label
    node.location = location
    if parent is not None:
        node.parent = parent
    return node


def _principled(nodes):
    for n in nodes:
        if n.type == "BSDF_PRINCIPLED" and n.name == PREFIX + "principled":
            return n
    return None


def _output(nodes):
    for n in nodes:
        if n.type == "OUTPUT_MATERIAL" and n.is_active_output:
            return n
    for n in nodes:
        if n.type == "OUTPUT_MATERIAL":
            return n
    return None


def build(material, channel_images, title):
    """(Re)create the add-on's nodes. channel_images: {channel: bpy.types.Image}."""
    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    for n in [n for n in nodes if n.name.startswith(PREFIX)]:
        nodes.remove(n)

    output = _output(nodes) or nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    principled = _new(nodes, "ShaderNodeBsdfPrincipled", "principled", "", (0, 0))
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    frame = _new(nodes, "NodeFrame", "frame", "Material Maker: %s" % title, (X0, 0))
    coords = _new(nodes, "ShaderNodeTexCoord", "uv", "UV", (X0, 0), frame)
    mapping = _new(nodes, "ShaderNodeMapping", "mapping", "Tiling", (X0 + 200, 0), frame)
    links.new(coords.outputs["UV"], mapping.inputs["Vector"])

    y = 300
    image_nodes = {}
    for channel, image in channel_images.items():
        node = _new(nodes, "ShaderNodeTexImage", "img " + channel, channel.capitalize(), (X0 + 450, y), frame)
        node.image = image
        node.interpolation = "Linear"
        node.extension = "REPEAT"
        node.hide = True
        links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        image_nodes[channel] = node
        y -= 60

    x = X0 + 800
    if "albedo" in image_nodes:
        mix = _new(nodes, "ShaderNodeMix", "albedo color", "Albedo Color", (x, 300))
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        _sock(mix.inputs, "Factor_Float").default_value = 1.0
        links.new(image_nodes["albedo"].outputs["Color"], _sock(mix.inputs, "A_Color"))
        links.new(_sock(mix.outputs, "Result_Color"), principled.inputs["Base Color"])
    for channel, socket, label, y in (("metal", "Metallic", "Metallic", 150), ("rough", "Roughness", "Roughness", 50),
                                     ("sss", "Subsurface Weight", "Subsurface", -50)):
        if channel in image_nodes:
            math = _new(nodes, "ShaderNodeMath", channel + " factor", label, (x, y))
            math.operation = "MULTIPLY"
            links.new(image_nodes[channel].outputs["Color"], math.inputs[0])
            links.new(math.outputs["Value"], principled.inputs[socket])
    if "normal" in image_nodes:
        normal_map = _new(nodes, "ShaderNodeNormalMap", "normal map", "Normal Map", (x, -150))
        links.new(image_nodes["normal"].outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    if "emission" in image_nodes:
        links.new(image_nodes["emission"].outputs["Color"], principled.inputs["Emission Color"])
    if "displace" in image_nodes:
        displacement = _new(nodes, "ShaderNodeDisplacement", "displacement", "Displacement", (x + 200, -350))
        displacement.inputs["Midlevel"].default_value = 0.5
        links.new(image_nodes["displace"].outputs["Color"], displacement.inputs["Height"])
        links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])
    if "occlusion" in image_nodes:
        # Principled has no ambient occlusion input; the texture is kept for the user to wire.
        image_nodes["occlusion"].label = "Occlusion (not connected)"
    return principled


def apply_material_parameters(material, values, has_channel, has_opacity=False):
    """Push the Material section values into the nodes. values: Material Maker parameter -> value.

    has_opacity: the material node's opacity input is connected (the albedo alpha carries it).
    """
    nodes = material.node_tree.nodes
    principled = _principled(nodes)
    if principled is None:
        return

    def node(name):
        return nodes.get(PREFIX + name)

    color = tuple(values.get("albedo_color", (1.0, 1.0, 1.0, 1.0)))
    mix = node("albedo color")
    if mix is not None:
        _sock(mix.inputs, "B_Color").default_value = color
    else:
        principled.inputs["Base Color"].default_value = color
    for channel, param, socket in (("metal", "metallic", "Metallic"), ("rough", "roughness", "Roughness"),
                                   ("sss", "sss", "Subsurface Weight")):
        v = float(values.get(param, 1.0 if param != "sss" else 0.0))
        math = node(channel + " factor")
        if math is not None:
            math.inputs[1].default_value = v
        elif param != "sss":
            principled.inputs[socket].default_value = v
    normal_map = node("normal map")
    if normal_map is not None:
        normal_map.inputs["Strength"].default_value = float(values.get("normal", 1.0))
    principled.inputs["Emission Strength"].default_value = (
        float(values.get("emission_energy", 1.0)) if has_channel("emission") else 0.0)
    displacement = node("displacement")
    if displacement is not None:
        # Godot's depth scale is in UV units; 0.1 keeps the default bump readable in Blender.
        displacement.inputs["Scale"].default_value = 0.1 * float(values.get("depth_scale", 1.0))
    transparent = bool(values.get("flags_transparent", False)) and has_opacity
    albedo = node("img albedo")
    alpha_links = [l for l in material.node_tree.links if l.to_socket == principled.inputs["Alpha"]]
    if transparent and albedo is not None and not alpha_links:
        material.node_tree.links.new(albedo.outputs["Alpha"], principled.inputs["Alpha"])
    elif not transparent:
        for l in alpha_links:
            material.node_tree.links.remove(l)
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED" if transparent else "DITHERED"
