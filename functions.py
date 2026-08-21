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
- ``reviews.aggregates_by_keys`` — the batch form of the above, and the
  ``live_query`` half of a host rating Projection (local mode reads through it
  instead of keeping a table).
- ``reviews.aggregates_export`` — the cursor-paged snapshot, and the
  ``source_of_truth`` half of that same Projection (``rebuild`` /
  ``drift_check`` read through it).
- ``reviews.moderation_content`` — a review's content for an external
  moderation module's screening and moderator card. Identifiers travel on the
  bus; content is fetched at the moment it is looked at.

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


@function("reviews.aggregates_by_keys")
def aggregates_by_keys(payload):
    """Batch-read the aggregate for many targets at once.

    Input: ``{"keys": [str, ...], "target_type": str?}``.
    Output: ``{key: {"avg": number, "count": integer}}`` — keys with no
    published review are absent, per the ``live_query`` contract in
    ``stapel_core.comm.projections.read()``.
    """
    from . import services

    return services.aggregates_by_keys(
        payload["keys"], target_type=payload.get("target_type") or ""
    )


@function("reviews.aggregates_export")
def aggregates_export(payload):
    """Cursor-paged snapshot of every target's aggregate (projection rebuild).

    Input: ``{"cursor": str|null, "limit": integer?}``.
    Output: ``{"rows": [{"target_key", "target_type", "avg", "count", "seq"}],
    "cursor": str|null, "total": integer|null}`` — the shape
    ``stapel_core.comm.projections._iter_snapshot`` pages through.
    """
    from . import services

    return services.aggregates_export(
        cursor=payload.get("cursor"),
        limit=payload.get("limit") or services.EXPORT_DEFAULT_LIMIT,
    )


@function("reviews.moderation_content")
def moderation_content(payload):
    """Return a review's content for an external moderation module.

    Input: ``{"review_id": str}`` — the moderation case's ``target_key`` for a
    ``review`` target.
    Output: ``{"text", "title", "language", "media", "author_id", "url",
    "rating", "status", "target_type", "target_key", "created_at"}``.
    """
    from . import services

    return services.moderation_content(payload["review_id"])
