"""Settings namespace for stapel-reviews.

All configuration is read through ``reviews_settings`` (lazily, at call
time) — never via module-level ``os.getenv`` (values would freeze at import).
Resolution order per key: ``settings.STAPEL_REVIEWS`` dict -> flat Django
setting of the same name -> environment variable -> default below.

The module is **target-generic**: it does not know what things get reviewed.
A host registers its own target types (a seller, a listing, a driver, a
course) into the ``TARGET_TYPES`` merge-registry, each with a *policy* — and
the module drives review creation/moderation/response entirely off that
policy. Domain questions ("may this author review this target?", "is this
caller the target owner?") are answered by **comm Function callbacks named in
the policy** — the module *calls* the host by name and never imports a host
model (see ``registry.py``).

Config axes (capability-config.md §16) surfaced in capabilities.json:

- ``MODERATION_DEFAULT`` — the per-type moderation default (``post`` = a review
  is published on creation and can be hidden later; ``pre`` = a review is held
  ``pending`` until a moderator publishes it). A target type may override it in
  its policy.
- ``RESPONSES`` — whether a target owner may attach a single Response to a
  review by default. A target type may override it in its policy.

``TARGET_TYPES`` is itself the flagship extension seam — a merge-registry
(idiom: notifications.TYPES, calendar.PRESETS) whose built-ins are **empty**
(the module ships knowing no targets). Values merge OVER the built-ins; a
policy value of ``None`` removes a type.
"""
from stapel_core.conf import AppSettings

#: AppSettings-shaped literal dict (capability-config.md §2): a top-level
#: DEFAULTS lets the capabilities.json emitter introspect axis keys/kinds
#: without re-parsing the AppSettings() call.
DEFAULTS = {
    # The target-type registry — the flagship seam. {type_name: policy dict}
    # merged OVER the (empty) built-ins; None removes a type. A policy dict:
    #   {
    #     "can_review":   "comm.function.name" | None,  # author eligibility
    #     "can_moderate": "comm.function.name" | None,  # moderate + respond
    #     "moderation":   "pre" | "post",               # overrides MODERATION_DEFAULT
    #     "one_per_author": bool,                        # single review per author
    #     "allow_response": bool,                        # overrides RESPONSES
    #   }
    # Every key but the two callbacks has a module-level default (below);
    # a type may omit them and inherit. See registry.resolve_policy.
    "TARGET_TYPES": {},
    # Moderation axis. "post" (default): a review is published immediately and
    # a moderator may hide it after the fact. "pre": a review is created
    # `pending` and stays invisible until a moderator publishes it.
    "MODERATION_DEFAULT": "post",
    # Responses axis. Whether a target owner may attach a single Response to a
    # review by default (a type policy may override with allow_response).
    "RESPONSES": True,
    # Inclusive rating bounds (tuning knobs — not axes). A rating outside
    # [RATING_MIN, RATING_MAX] is a 400.
    "RATING_MIN": 1,
    "RATING_MAX": 5,
}

reviews_settings = AppSettings(
    "STAPEL_REVIEWS",
    defaults=DEFAULTS,
    import_strings=(),
)

__all__ = ["reviews_settings", "DEFAULTS"]
