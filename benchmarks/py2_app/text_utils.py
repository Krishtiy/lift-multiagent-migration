# -*- coding: utf-8 -*-
"""Text processing utilities with Python 2 legacy idioms."""

import hashlib
import StringIO


def render_banner(title, fill_char="="):
    # Gotcha G-01: print statement without parenthesis
    # Gotcha G-10: unicode string manipulation
    width = 40
    line = fill_char * width
    buffer = StringIO.StringIO()
    buffer.write(line + "\n")
    buffer.write(title.center(width) + "\n")
    buffer.write(line + "\n")
    return buffer.getvalue()


def hash_text(text, salt=""):
    # Gotcha G-10: In Py2, hashlib accepts unicode and str interchangeably if ASCII-decodable
    # In Py3, text must be explicitly encoded to bytes (hashlib.md5(b"...")).
    try:
        combined = text + salt
        hasher = hashlib.md5()
        hasher.update(combined)
        return hasher.hexdigest()
    except TypeError, e:
        # Gotcha G-02: except Type, var syntax
        print "Error hashing text:", e
        return None


def format_error_message(err):
    # Gotcha G-02: comma exception unpacking & G-01 print
    try:
        raise ValueError(err)
    except ValueError, exc:
        msg = "Captured: " + str(exc)
        return msg


def safe_decode(raw_bytes, encoding="utf-8"):
    # Gotcha G-10: type checking against basestring/unicode in Py2
    if isinstance(raw_bytes, unicode):
        return raw_bytes
    return raw_bytes.decode(encoding)
