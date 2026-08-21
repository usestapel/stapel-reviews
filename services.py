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

import base64
import json
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from stapel_core.comm import emit

from .models import Review, ReviewStatus, VISIBLE_STATUSES, Response
from .registry import (
    check_can_moderate,
    check_can_review,
    resolve_policy,
)

logger = logging.getLogger(__name__)

EVENT_PUBLISHED = "reviews.review.published"
EVENT_HIDDEN = "reviews.review.hidden"

MODERATION_ACTIONS = ("hide", "publish")

#: How a platform moderation verdict maps onto a review's visibility.
#:
#: The keys are the verdict vocabulary of the moderation module
#: (``approved | rejected | needs_review | dismissed``); the first three are
#: the words ``stapel-listings`` already consumes, so no translation dictionary
#: sits between a verdict and its targets. ``None`` means "this verdict does
#: not move the review": ``needs_review`` says the automation abstained and a
#: human still owes a decision, and ``dismissed`` speaks about a *report*, not
#: about the content.
VERDICT_ACTIONS: dict[str, Optional[str]] = {
    "approved": "publish",
    "rejected": "hide",
    "needs_review": None,
    "dismissed": None,
}

#: Page size of :func:`aggregates_export` when the caller names none, and the
#: ceiling it clamps to (a snapshot reader must not be able to ask for the
#: whole table in one response).
EXPORT_DEFAULT_LIMIT = 500
EXPORT_MAX_LIMIT = 2000


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


class InvalidVerdictDecision(Exception):
    """A moderation verdict carried a decision outside VERDICT_ACTIONS."""


class ReviewNotFound(Exception):
    """No review exists for the given id (comm-Function callers get this
    instead of ``None`` — a read that silently returns nothing is how a stale
    moderator card gets built)."""


class InvalidExportCursor(Exception):
    """The opaque cursor handed to aggregates_export was not one we issued."""


class ResponseNotAllowed(Exception):
    """The target type's policy disables responses."""


class AlreadyResponded(Exception):
    """The review already carries a response (one per review)."""


# ── The system actor ───────────────────────────────────────────────────────


class _SystemActor:
    """The platform acting on its own moderation verdict.

    ``can_moderate`` is deliberately **fail-closed**: a target type that names
    no callback lets nobody hide or publish anything through the API. That is
    the right default for humans and exactly the wrong one for the platform's
    own verdict — a moderation module that has already authorized a moderator,
    recorded a Verdict and emitted ``moderation.completed`` would find its
    decision refused by the very module it is deciding about.

    So the bypass is a *named actor*, not a boolean flag threaded through the
    call. :func:`moderate_review` recognizes this singleton **by identity**
    (``actor is SYSTEM_ACTOR``), which no host object can forge by growing an
    ``is_system`` attribute, and skips the policy gate for it alone. It carries
    no ``pk`` because it is not a user: anything that would persist it (a
    Response author) must refuse it rather than invent a row.
    """

    pk = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<stapel-reviews SYSTEM_ACTOR>"

    def __str__(self) -> str:
        return "system"


#: The one system actor (see :class:`_SystemActor`). Compared by identity.
SYSTEM_ACTOR = _SystemActor()


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


def aggregates_by_keys(
    keys: Iterable[str], *, target_type: str = ""
) -> dict[str, dict]:
    """Batch form of :func:`aggregate` — the keyed read a host projection uses.

    One query for many targets: ``{target_key: {"avg", "count"}}``. A key with
    no published review is **absent** from the result rather than present with
    zeros, which is the contract ``stapel_core.comm.projections.read()`` states
    for a ``live_query`` ("absent keys are simply missing"), and it lets a
    caller tell "nobody has reviewed this" from "everyone rated it 0".

    ``target_type`` narrows the lookup and should be passed by anything that
    knows it. The core Projection primitive calls a ``live_query`` with
    ``{"keys": [...]}`` and nothing else, so omitting it is legal: the counts
    then span every target type sharing that key. For a host whose keys are
    UUIDs that is the same answer; for one that keys targets by short slugs it
    is not, which is why the parameter exists.
    """
    key_list = [str(k) for k in keys]
    if not key_list:
        return {}
    qs = Review.objects.filter(target_key__in=key_list, status__in=VISIBLE_STATUSES)
    if target_type:
        qs = qs.filter(target_type=target_type)
    rows = qs.values("target_key").annotate(avg=Avg("rating"), count=Count("id"))
    return {
        row["target_key"]: {
            "avg": round(row["avg"], 3) if row["avg"] is not None else 0.0,
            "count": row["count"],
        }
        for row in rows
    }


def _encode_cursor(target_type: str, target_key: str) -> str:
    """Opaque continuation token — the last (type, key) pair of a page."""
    raw = json.dumps([target_type, target_key], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        target_type, target_key = json.loads(base64.urlsafe_b64decode(cursor))
        return str(target_type), str(target_key)
    except Exception as exc:  # noqa: BLE001 - any malformed token is one error
        raise InvalidExportCursor(cursor) from exc


def aggregates_export(
    *, cursor: Optional[str] = None, limit: int = EXPORT_DEFAULT_LIMIT
) -> dict:
    """Cursor-paged snapshot of every target's aggregate — the rebuild half.

    This is the ``source_of_truth`` a host Projection re-derives its whole
    table from when it has drifted or has just been created; the keyed
    :func:`aggregates_by_keys` answers live reads, this one answers "give me
    everything".

    Response shape is the one ``stapel_core.comm.projections._iter_snapshot``
    reads: ``{"rows": [...], "cursor": <next|None>, "total": <int|None>}``,
    each row carrying the projection's ``source_key`` (``target_key``) and a
    ``seq``. ``seq`` is the newest ``updated_at`` among the target's published
    reviews in **unix milliseconds** — the same unit and clock as an Event's
    timestamp, which is what makes a live fact arriving mid-rebuild correctly
    supersede the snapshot row instead of racing it.

    Paging is keyset, ordered by ``(target_type, target_key)``: a cursor names
    the last pair of the previous page, so a snapshot stays stable under
    concurrent writes in a way an OFFSET never is. ``total`` is reported on the
    first page only (it costs a distinct count) and is ``None`` afterwards,
    which the core reader treats as "owner does not report it".
    """
    limit = max(1, min(int(limit or EXPORT_DEFAULT_LIMIT), EXPORT_MAX_LIMIT))
    base = Review.objects.filter(status__in=VISIBLE_STATUSES)

    total = None
    if cursor is None:
        total = base.values("target_type", "target_key").distinct().count()
    else:
        last_type, last_key = _decode_cursor(cursor)
        base = base.filter(
            Q(target_type__gt=last_type)
            | Q(target_type=last_type, target_key__gt=last_key)
        )

    groups = (
        base.values("target_type", "target_key")
        .annotate(avg=Avg("rating"), count=Count("id"), last=Max("updated_at"))
        .order_by("target_type", "target_key")[: limit + 1]
    )
    page = list(groups)
    has_more = len(page) > limit
    page = page[:limit]

    rows = [
        {
            "target_key": row["target_key"],
            "target_type": row["target_type"],
            "avg": round(row["avg"], 3) if row["avg"] is not None else 0.0,
            "count": row["count"],
            "seq": int(row["last"].timestamp() * 1000) if row["last"] else 0,
        }
        for row in page
    ]
    next_cursor = (
        _encode_cursor(page[-1]["target_type"], page[-1]["target_key"])
        if has_more and page
        else None
    )
    return {"rows": rows, "cursor": next_cursor, "total": total}


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
    changes (idempotent re-moderation is a no-op).

    Passing :data:`SYSTEM_ACTOR` skips the policy gate — see
    :class:`_SystemActor` for why, and :func:`apply_verdict` for the one caller
    that is allowed to. The skip covers ``resolve_policy`` as well: a review
    whose target type the host has since de-registered must still be takedown-
    able, and an ``UnknownTargetType`` raised at that moment would leave the
    platform's own verdict stuck against a type nobody reviews any more.
    """
    if action not in MODERATION_ACTIONS:
        raise InvalidModerationAction(action)

    if actor is SYSTEM_ACTOR:
        logger.info(
            "system verdict on review %s: %s (%s)", review.id, action, reason or "-"
        )
    else:
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


def apply_verdict(review: Review, *, decision: str, reason: str = "") -> Review:
    """Apply a platform moderation verdict to ``review`` as :data:`SYSTEM_ACTOR`.

    ``decision`` is the moderation module's verdict word; :data:`VERDICT_ACTIONS`
    maps it onto ``hide`` / ``publish`` / nothing. Unknown words raise
    :class:`InvalidVerdictDecision` rather than being read as "do nothing" —
    a decision we do not understand is contract drift, and drift that looks
    like a no-op is the expensive kind.

    **Idempotent by state, not by event id.** Re-delivery of the same verdict
    (at-least-once is the only delivery there is) finds the review already in
    the decided status and returns without a write and without a fact, because
    :func:`moderate_review` no-ops when the status does not change. No table of
    processed event ids is needed for that, and none is kept: state is the one
    carrier of truth that already exists.
    """
    if decision not in VERDICT_ACTIONS:
        raise InvalidVerdictDecision(decision)
    action = VERDICT_ACTIONS[decision]
    if action is None:
        return review
    return moderate_review(review, actor=SYSTEM_ACTOR, action=action, reason=reason)


def moderation_content(review_id) -> dict:
    """Return ``review_id``'s content for an external moderation card/screening.

    The read half of the moderation seam: identifiers travel on the bus, the
    content is fetched by an authorized call at the moment it is looked at, so
    a moderator opening a case six hours later reads the review as it is now
    rather than as it was when the case opened.

    The shape follows the ``*.moderation_content`` family
    (``text``/``title``/``language``/``media``/``author_id``/``url``); a review
    has no title, no declared language, no media and no public per-review URL,
    so those four come back empty rather than being invented. The review's own
    context (rating, status, target) rides along because a rating is half of
    what a reviews moderator is judging.

    Unguarded on purpose: this is an in-process comm Function, and who may look
    at a case's content is the moderation module's ``can_view_content`` policy
    to answer, not a second gate here that would fail closed against it.
    """
    review = Review.objects.filter(pk=review_id).first()
    if review is None:
        raise ReviewNotFound(str(review_id))
    return {
        "text": review.body,
        "title": "",
        "language": "",
        "media": [],
        "author_id": str(review.author_id),
        "url": "",
        "rating": review.rating,
        "status": review.status,
        "target_type": review.target_type,
        "target_key": review.target_key,
        "created_at": review.created_at.isoformat(),
    }


def respond(review: Review, *, author, body: str) -> Response:
    """Attach the target owner's single response to ``review``.

    Allowed only when the type policy's ``allow_response`` is on and the type's
    ``can_moderate`` callback authorizes ``author`` (the owner uses the same
    ownership gate as moderation). At most one response per review.

    :data:`SYSTEM_ACTOR` is refused here: a Response is authored by a real user
    row, and the moderation bypass buys the platform a verdict on visibility,
    not a voice in the conversation.
    """
    if author is SYSTEM_ACTOR:
        raise NotAllowedToModerate(review.target_type)
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
