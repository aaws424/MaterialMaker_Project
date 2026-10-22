"""Blender image datablocks holding a material's rendered channels."""

import bpy

# Rendered values are already linear: color channels go through sRGB->linear in the shader.
COLOR_SPACE_COLOR = "Linear Rec.709"
COLOR_SPACE_DATA = "Non-Color"


def new_image(name, size, is_color):
    image = bpy.data.images.new(name, size, size, alpha=True, float_buffer=True)
    prepare_image(image, size, is_color)
    return image


def prepare_image(image, size, is_color):
    """Make `image` a size x size float image in the right color space (fast for generated images)."""
    if tuple(image.size) != (size, size):
        if image.source == "GENERATED":
            image.generated_width = size
            image.generated_height = size
        else:
            image.scale(size, size)
    if image.source == "GENERATED" and not image.use_generated_float:
        image.use_generated_float = True
    space = COLOR_SPACE_COLOR if is_color else COLOR_SPACE_DATA
    if image.colorspace_settings.name != space:
        image.colorspace_settings.name = space
    return image


def ensure_image(name, size, is_color):
    """A float RGBA image named `name`, created or resized as needed."""
    image = bpy.data.images.get(name)
    if image is None:
        return new_image(name, size, is_color)
    return prepare_image(image, size, is_color)


def write_image(image, pixels):
    """Copy a (h, w, 4) float32 array into the image and refresh its GPU texture."""
    image.pixels.foreach_set(pixels.reshape(-1))
    image.update()


def write_channels(program, results, prefix):
    """Write renderer results into images named '<prefix>_<channel>'; returns {channel: image}."""
    images = {}
    for name, pixels in results.items():
        channel = program.channels[name]
        image = ensure_image("%s_%s" % (prefix, name), pixels.shape[0], channel.is_color)
        write_image(image, pixels)
        images[name] = image
    return images
