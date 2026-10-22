"""Port of gen_context.gd: tracks which (generator, uv) variants were already emitted."""


class GenContext:
    def __init__(self, parent=None):
        self.variants = {}
        self.parent = parent

    def has_variant(self, generator):
        return generator in self.variants or (self.parent is not None and self.parent.has_variant(generator))

    def touch_variant(self, generator):
        self.variants.setdefault(generator, [])
        if self.parent is not None:
            self.parent.touch_variant(generator)

    def get_variant(self, generator, variant):
        """Index of `variant` for `generator`, or -1 (and record it) when new."""
        rv = -1
        if generator in self.variants:
            lst = self.variants[generator]
            if variant in lst:
                rv = lst.index(variant)
            else:
                lst.append(variant)
        else:
            self.variants[generator] = [variant]
        self.touch_variant(generator)
        return rv
