"""String helpers used by the shader generator (ports of gen_shader.gd helpers).

These are deliberately faithful to the GDScript originals, quirks included,
because the generated code must match Material Maker's exactly.
"""

import math

WORD_LETTERS = frozenset("azertyuiopqsdfghjklmwxcvbnAZERTYUIOPQSDFGHJKLMWXCVBN1234567890_")


def is_word_letter(c):
    return c in WORD_LETTERS


def find_matching_parenthesis(string, i, op="(", cp=")"):
    level = 0
    length = len(string)
    while i < length:
        c = string[i]
        if c == op:
            level += 1
        elif c == cp:
            level -= 1
            if level == 0:
                return i
        i += 1
        next_op = string.find(op, i)
        next_cp = string.find(cp, i)
        max_p = max(next_op, next_cp)
        if max_p < 0:
            return -1
        min_p = min(next_op, next_cp)
        i = max_p if min_p < 0 else min_p
    return i


def find_keyword_call(string, keyword):
    """Return the text between the parentheses of the first `$keyword(...)`."""
    search = "$%s(" % keyword
    position = string.find(search)
    if position == -1:
        return None
    begin = position + len(search)
    end = find_matching_parenthesis(string, begin - 1)
    if end > 0:
        return string[begin:end]
    return None


def replace_variables(string, variables):
    """Replace `$(name)` and `$name` (when not followed by a word letter)."""
    while True:
        old_string = string
        for variable, value in variables.items():
            string = string.replace("$(%s)" % variable, value)
            keyword = "$" + variable
            keyword_size = len(keyword)
            parts = []
            while string:
                pos = string.find(keyword)
                if pos == -1:
                    parts.append(string)
                    break
                parts.append(string[:pos])
                string = string[pos:]
                if len(string) > keyword_size and is_word_letter(string[keyword_size]):
                    parts.append(string[:keyword_size])
                    string = string[keyword_size:]
                    continue
                parts.append(value)
                string = string[keyword_size:]
            string = "".join(parts)
        if string == old_string:
            return string


def _replace_random(string, keyword, function, offset):
    while True:
        params = find_keyword_call(string, keyword)
        if params is None:
            return string
        replace = "$%s(%s)" % (keyword, params)
        while True:
            position = string.find(replace)
            if position == -1:
                break
            with_ = "%s(%s, $seed+%f)" % (function, params, math.sin(position) + offset)
            string = string.replace(replace, with_)


def replace_rnd(string, offset=0):
    return _replace_random(string, "rnd", "param_rnd", offset)


def replace_rndi(string, offset=0):
    return _replace_random(string, "rndi", "param_rndi", offset)


def is_valid_integer(s):
    s = s.strip()
    if s[:1] in "+-":
        s = s[1:]
    return s.isdigit()


def is_identifier_char(c):
    return c.isalnum() or c == "_"
