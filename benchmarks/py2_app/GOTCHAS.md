# Python 2 Seeded Gotchas Catalog

This catalog defines the intentional legacy patterns planted across the `py2_app` benchmark modules. Each gotcha has a unique ID, description, category, and difficulty rating (`easy`, `medium`, `hard`).

| Gotcha ID | Category | Description | Python 2 Behavior | Python 3 Failure Mode | Difficulty |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **G-01** | Syntax | `print` statement | `print "msg", val` | `SyntaxError: Missing parentheses in call to 'print'` | **Easy** |
| **G-02** | Syntax | `except Exception, e` syntax | Commas bind exception variable | `SyntaxError: multiple exception types must be parenthesized` | **Easy** |
| **G-03** | Builtins | `xrange()` built-in | Memory-efficient lazy integer range | `NameError: name 'xrange' is not defined` | **Easy** |
| **G-04** | Dict API | `dict.has_key(k)` | Tests membership boolean | `AttributeError: 'dict' object has no attribute 'has_key'` | **Easy** |
| **G-05** | Arithmetic | Integer division `/` | Truncates towards negative infinity (`sum/len`) | Produces float true division (silent semantic drift!) | **Medium** |
| **G-06** | Iterators | `map()`, `filter()`, `zip()` | Return concrete `list` instances (indexable, sliceable) | Return iterators (subscripting throws `TypeError`) | **Medium** |
| **G-07** | Dict API | `dict.iteritems()`, `.keys()` | `.iteritems()` returns iterator, `.keys()` returns list | `AttributeError` for `.iteritems()`; `.keys()` is view | **Medium** |
| **G-08** | Data Types | `long` literal and type | Integer overflow to `123456789L`, `long(x)` | `SyntaxError` on `L` suffix; `NameError: name 'long' is not defined` | **Medium** |
| **G-09** | Magic Methods | `__nonzero__` method | Evaluates truthiness of class instance | Ignored in Py3; `bool(obj)` uses `__bool__` (defaults to `True`) | **Medium** |
| **G-10** | Strings/Encoding | `str` vs `unicode` vs `bytes` | `str` is 8-bit bytes; implicit string coercion works | `TypeError: must be str, not bytes`; hash/encode differences | **Hard** |
| **G-11** | Sorting/Comparison | Mixed-type comparison (`cmp`) | Unrelated types sortable (e.g. `None < int < str`) | `TypeError: '<' not supported between instances of ...` | **Hard** |
| **G-12** | Standard Lib | Legacy modules (`StringIO`, `urllib2`) | `import StringIO`, `import urllib2` | `ModuleNotFoundError` (moved to `io`, `urllib.request`) | **Hard** |
