"""Service layer of stapel-reviews — the target-generic review engine.

Every write goes through a target-type *policy* (``registry.resolve_policy``):
who may review (a comm callback), pre/post moderation, one-review-per-author,
whether responses are allowed. The module owns the per-target aggregate
(avg/count over published reviews) and, on every visibility change, emits a
generic FACT — ``reviews.review.published`` / ``reviews.review.hidden`` —
carrying the fresh aggregate, so a host catalog can maintain its OWN projection
of ``(target_type, target_key) -> {avg, count}`` (§10 projection pattern)
without ever calling back into this module.

Emits (schemas/emits/):
- ``reviews.review.published`` — a review became visible (created under
  post-moderation, or published by a moderator). Carries the new aggregate.
- ``reviews.review.hidden`` — a review left the visible set (hidden by a
  moderator). Carries the new aggregate.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Avg, Count
from stapel_core.comm import emit

from .models import Review, ReviewStatus, VISIBLE_STATUSES, Response
from .registry import (
    check_can_moderate,
    check_can_review,
    resolve_policy,
)

EVENT_PUBLISHED = "reviews.review.published"
EVENT_HIDDEN = "reviews.review.hidden"

MODERATION_ACTIONS = ("hide", "publish")


# ── Service-layer exceptions (views map these to error keys) ───────────────


class NotAllowedToReview(Exception):
    """The type's can_review callback denied this author."""


class NotAllowedToModerate(Exception):
    """The type's can_moderate callback denied this actor (or none is set)."""


class DuplicateReview(Exception):
    """one_per_author is on and the author already reviewed this target."""


class InvalidRating(Exception):
    """Rating outside [RATING_MIN, RATING_MAX]."""


class InvalidModerationAction(Exception):
    """Moderation action other than hide/publish."""


class ResponseNotAllowed(Exception):
    """The target type's policy disables responses."""


class AlreadyResponded(Exception):
    """The review already carries a response (one per review)."""


# ── Aggregate (module-owned) ───────────────────────────────────────────────


@dataclass
class Aggregate:
    """Public rating aggregate for a target: mean and count over *published*
    reviews. ``avg`` is 0.0 when ``count`` is 0."""

    avg: float
    count: int


def aggregate(target_type: str, target_key: str) -> Aggregate:
    """Compute the live aggregate over published reviews of a target."""
    row = (
        Review.objects.filter(
            target_type=target_type,
            target_key=target_key,
            status__in=VISIBLE_STATUSES,
        )
        .aggregate(avg=Avg("rating"), count=Count("id"))
    )
    count = row["count"] or 0
    avg = round(row["avg"], 3) if row["avg"] is not None else 0.0
    return Aggregate(avg=avg, count=count)


def _emit_review_fact(review: Review, event_name: str, reason: str = "") -> None:
    """Emit the generic visibility-change fact for ``review``, carrying the
    freshly recomputed target aggregate (the projection payload). Called inside
    the same atomic block as the status write, so the fact and the state it
    describes commit together (outbox guarantee)."""
    agg = aggregate(review.target_type, review.target_key)
    emit(
        event_name,
        {
            "review_id": str(review.id),
            "target_type": review.target_type,
            "target_key": review.target_key,
            "author_id": str(review.author_id),
            "rating": review.rating,
            "status": review.status,
            "reason": reason,
            "aggregate": {"avg": agg.avg, "count": agg.count},
        },
        key=f"{review.target_type}:{review.target_key}",
    )


# ── Reviewing ──────────────────────────────────────────────────────────────


def create_review(
    *,
    target_type: str,
    target_key: str,
    author,
    rating: int,
    body: str = "",
) -> Review:
    """Create a review of ``(target_type, target_key)`` by ``author``.

    Enforces, in order: the target type is registered (``resolve_policy``
    raises ``UnknownTargetType``); the rating is in range; one-per-author (if
    the policy sets it); the author is eligible (``can_review`` callback). Under
    pre-moderation the review is created ``pending`` (no emit); under
    post-moderation it is ``published`` and a ``reviews.review.published`` fact
    is emitted with the new aggregate.
    """
    from .conf import reviews_settings

    policy = resolve_policy(target_type)

    lo, hi = reviews_settings.RATING_MIN, reviews_settings.RATING_MAX
    if not isinstance(rating, int) or rating < lo or rating > hi:
        raise InvalidRating(f"rating {rating!r} not in [{lo}, {hi}]")

    if policy["one_per_author"]:
        exists = Review.objects.filter(
            target_type=target_type, target_key=target_key, author=author
        ).exists()
        if exists:
            raise DuplicateReview(target_type)

    if not check_can_review(
        policy, author_id=author.pk, target_type=target_type, target_key=target_key
    ):
        raise NotAllowedToReview(target_type)

    status = (
        ReviewStatus.PENDING
        if policy["moderation"] == "pre"
        else ReviewStatus.PUBLISHED
    )

    with transaction.atomic():
        review = Review.objects.create(
            target_type=target_type,
            target_key=target_key,
            author=author,
            rating=rating,
            body=body,
            status=status,
        )
        if status == ReviewStatus.PUBLISHED:
            _emit_review_fact(review, EVENT_PUBLISHED)
    return review


def moderate_review(review: Review, *, actor, action: str, reason: str = "") -> Review:
    """Hide or publish ``review`` (moderation). ``action`` is one of
    ``hide`` / ``publish``. Requires the type's ``can_moderate`` callback to
    authorize ``actor`` (fail-closed if no callback is set). Emits the matching
    visibility fact with the new aggregate only when the status actually
    changes (idempotent re-moderation is a no-op)."""
    if action not in MODERATION_ACTIONS:
        raise InvalidModerationAction(action)

    policy = resolve_policy(review.target_type)
    if not check_can_moderate(
        policy,
        actor_id=actor.pk,
        target_type=review.target_type,
        target_key=review.target_key,
    ):
        raise NotAllowedToModerate(review.target_type)

    new_status = ReviewStatus.HIDDEN if action == "hide" else ReviewStatus.PUBLISHED
    if review.status == new_status:
        return review  # no-op — no state change, no fact

    with transaction.atomic():
        review.status = new_status
        review.save(update_fields=["status", "updated_at"])
        event = EVENT_HIDDEN if new_status == ReviewStatus.HIDDEN else EVENT_PUBLISHED
        _emit_review_fact(review, event, reason=reason)
    return review


def respond(review: Review, *, author, body: str) -> Response:
    """Attach the target owner's single response to ``review``.

    Allowed only when the type policy's ``allow_response`` is on and the type's
    ``can_moderate`` callback authorizes ``author`` (the owner uses the same
    ownership gate as moderation). At most one response per review."""
    policy = resolve_policy(review.target_type)
    if not policy["allow_response"]:
        raise ResponseNotAllowed(review.target_type)
    if not check_can_moderate(
        policy,
        actor_id=author.pk,
        target_type=review.target_type,
        target_key=review.target_key,
    ):
        raise NotAllowedToModerate(review.target_type)
    if Response.objects.filter(review=review).exists():
        raise AlreadyResponded(str(review.id))
    return Response.objects.create(review=review, author=author, body=body)


def list_reviews(target_type: str, target_key: str, *, include_all: bool = False):
    """Queryset of a target's reviews, newest first. ``include_all=False``
    (the non-owner surface) returns published only; a moderator/owner may pass
    ``include_all=True`` to see pending/hidden too. The view anchor-paginates
    the returned queryset."""
    qs = Review.objects.filter(target_type=target_type, target_key=target_key)
    if not include_all:
        qs = qs.filter(status__in=VISIBLE_STATUSES)
    return qs
