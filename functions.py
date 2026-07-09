"""comm surface of stapel-reviews.

Every Function/emit carries a JSON schema in ``schemas/`` — tests run with
``VALIDATE_SCHEMAS`` on, so a payload drifting from its schema fails loudly.
Registration happens on import from ``apps.py:ready()``.

Emits (see schemas/emits/):
- ``reviews.review.published`` — a review became visible; carries the fresh
  per-target aggregate {avg, count} so a host catalog can maintain its OWN
  projection (§10) without calling back.
- ``reviews.review.hidden`` — a review left the visible set; carries the fresh
  aggregate.

Functions (see schemas/functions/):
- ``reviews.aggregate`` — the module-owned rating aggregate for a target, a
  read primitive other services can call synchronously (the host projection is
  a cache of exactly this).

NOTE: the *policy* callbacks a host registers (``can_review`` / ``can_moderate``
named in a TARGET_TYPES entry) are the host's own comm Functions — the module
CALLS them by name and ships no schema for them (they are not part of this
module's contract).
"""
from stapel_core.comm import function


@function("reviews.aggregate")
def aggregate(payload):
    """Return the published-review aggregate for a target.

    Input: ``{"target_type": str, "target_key": str}``.
    Output: ``{"avg": number, "count": integer}`` — ``avg`` is 0.0 when
    ``count`` is 0.
    """
    from . import services

    agg = services.aggregate(payload["target_type"], payload["target_key"])
    return {"avg": agg.avg, "count": agg.count}
