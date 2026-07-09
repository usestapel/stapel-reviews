"""stapel-reviews — Target-generic reviews and ratings for the Stapel framework.

Public API (lazily exported, PEP 562 — importing this package never pulls
in Django or requires configured settings):

- ``reviews_settings`` — resolved app settings (``stapel_reviews.conf``).
"""

__all__ = [
    "reviews_settings",
]

# name -> submodule that defines it. Resolution is deferred until first
# attribute access so that `import stapel_reviews` stays Django-free.
_LAZY_EXPORTS = {
    "reviews_settings": ".conf",
}


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        from importlib import import_module

        value = getattr(import_module(_LAZY_EXPORTS[name], __name__), name)
        globals()[name] = value  # cache for subsequent lookups
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
