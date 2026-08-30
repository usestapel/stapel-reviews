"""Action subscriptions of stapel-reviews.

Handlers must be idempotent: delivery is at-least-once (outbox retries,
broker redelivery). Consumes contracts live in ``schemas/consumes/``.

- ``user.deleted`` (from stapel-auth/gdpr) — erase the account's authored
  reviews and responses.
- ``user.merged`` (from stapel-auth) — an anonymous guest was absorbed into
  an existing account; carry the guest's authored reviews and responses over
  to it.
- ``moderation.completed`` (from stapel-moderation) — apply the verdict.
"""
import logging

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)


class MergeTargetNotReady(RuntimeError):
    """A ``user.merged`` arrived before the surviving account exists here.

    Transient, not a bug: the guest has reviews to carry over but there is no
    local user row to point ``Review.author`` at yet. Raising is the comm
    layer's retry signal — ``deliver()`` wraps a failing handler in
    ``ActionDeliveryError`` and the outbox redelivers — so the transfer
    completes once the survivor's user projection lands. An operator seeing
    this in a redelivery loop is looking at an ordering lag, not a defect.
    """


@on_action("user.deleted")
def handle_user_deleted(event):
    """Erase this module's PII when an account deletion is executed: the
    user's authored reviews (and any responses on them) and the user's authored
    responses to others' reviews."""
    from .gdpr import ReviewsGDPRProvider

    user_id = event.payload.get("user_id")
    if not user_id:
        logger.error("user.deleted event without user_id: %s", event.event_id)
        return
    ReviewsGDPRProvider().delete(user_id)
    logger.info("reviews data erased for deleted user %s", user_id)


@on_action("user.merged")
def handle_user_merged(event):
    """Carry a merged-away account's reviews and responses to the survivor.

    stapel-auth absorbs an anonymous guest into an existing account and then
    DELETES the guest row. Both user columns this module owns —
    ``Review.author`` and ``Response.author`` — are ``on_delete=CASCADE``, so
    without this handler a visitor who rated a seller before signing in does
    not merely lose the attribution: the review is destroyed, and the
    seller's aggregate silently loses a rating nobody asked to remove.
    Reassignment happens here, in one transaction, before that deletion can
    cascade.

    **The one-per-author collision.** ``one_per_author`` is a per-target-type
    policy, not a database constraint, so a blind reassignment cannot raise —
    it can only leave a state ``create_review`` would have refused: two
    reviews by one author on one target. Where the policy is on and BOTH
    accounts reviewed the same ``(target_type, target_key)``, **the
    survivor's review wins** — it is the one that carries the account's own
    history — and the guest's duplicate is dropped, taking its ``Response``
    with it by cascade. A dropped review that was *published* leaves the
    visible set, so it emits ``reviews.review.hidden`` (reason
    ``merged_duplicate``) with the recomputed aggregate: a host projecting
    ``avg_rating`` must not be left counting a row this module deleted. A
    target type whose policy is off, or one the host has since
    de-registered, keeps both rows — that is what "no constraint" means.

    Two different "unknown id" situations, and conflating them loses data:

    * the guest owns nothing here (never reviewed anything, or a previous
      delivery already moved it all) — a genuine no-op, returned quietly;
    * the guest owns rows but the survivor has no user row here yet — NOT a
      no-op. :class:`MergeTargetNotReady` is raised so the event is
      redelivered, because returning success would let the outbox mark it
      delivered and lose the reviews for good.
    """
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError
    from django.db import transaction

    from .models import Response, Review

    payload = event.payload or {}
    from_user_id = payload.get("from_user_id")
    into_user_id = payload.get("into_user_id")
    if not from_user_id or not into_user_id:
        logger.error("user.merged without from/into user id: %s", event.event_id)
        return
    if str(from_user_id) == str(into_user_id):
        return

    with transaction.atomic():
        # Every read, and the decision they feed, happens inside the
        # transaction and before the first write, so the "not yet" path below
        # can never leave half the rows moved.
        try:
            owns_something = (
                Review.objects.filter(author_id=from_user_id).exists()
                or Response.objects.filter(author_id=from_user_id).exists()
            )
            # The survivor probe is read here, under the same guard, because a
            # malformed *into* id must not escape as a poison pill either.
            survivor_exists = get_user_model().objects.filter(
                pk=into_user_id
            ).exists()
        except (ValidationError, ValueError, TypeError):
            # Django raises ValidationError (not ValueError) for a malformed
            # UUID; an id that cannot address a row here names nothing.
            logger.warning("user.merged with unusable user ids: %s", event.event_id)
            return
        if not owns_something:
            # Nothing to carry: the guest never reviewed anything here, or a
            # previous delivery already moved everything. Quiet by design —
            # this is also the at-least-once idempotency path.
            return
        if not survivor_exists:
            raise MergeTargetNotReady(
                f"user.merged {from_user_id} -> {into_user_id}: the surviving "
                f"account has no user row in stapel-reviews yet; redeliver "
                f"once its projection has landed"
            )

        dropped = _drop_duplicate_reviews(from_user_id, into_user_id)
        moved_reviews = Review.objects.filter(author_id=from_user_id).update(
            author_id=into_user_id
        )
        moved_responses = Response.objects.filter(author_id=from_user_id).update(
            author_id=into_user_id
        )

    logger.info(
        "user.merged %s -> %s: %s reviews, %s responses carried over, "
        "%s duplicate review(s) dropped",
        from_user_id,
        into_user_id,
        moved_reviews,
        moved_responses,
        dropped,
    )


def _drop_duplicate_reviews(from_user_id, into_user_id) -> int:
    """Drop the guest's reviews that would break ``one_per_author``.

    The survivor's review is the one that stays. Returns how many rows were
    removed, so a redelivery reports ``0`` rather than claiming the work
    twice. Called inside the caller's transaction.
    """
    from . import services
    from .models import VISIBLE_STATUSES, Review, ReviewStatus
    from .registry import UnknownTargetType, resolve_policy

    guest_reviews = list(Review.objects.filter(author_id=from_user_id))
    if not guest_reviews:
        return 0

    survivor_targets = set(
        Review.objects.filter(author_id=into_user_id).values_list(
            "target_type", "target_key"
        )
    )
    dropped = 0
    for review in guest_reviews:
        target = (review.target_type, review.target_key)
        if target not in survivor_targets:
            continue
        try:
            policy = resolve_policy(review.target_type)
        except UnknownTargetType:
            # The host de-registered the type. No policy to uphold, and
            # guessing one would delete a person's review on a hunch.
            continue
        if not policy["one_per_author"]:
            continue
        was_visible = review.status in VISIBLE_STATUSES
        # Deleted through the queryset, not ``review.delete()``: the instance
        # keeps its id and is still the fact's subject afterwards.
        Review.objects.filter(pk=review.pk).delete()  # cascades to its Response
        dropped += 1
        if was_visible:
            # Emitted after the delete so the helper's recomputed aggregate is
            # the one a host must project, and inside the caller's transaction
            # so the fact and the removal commit together.
            review.status = ReviewStatus.HIDDEN
            services._emit_review_fact(
                review, "reviews.review.hidden", reason="merged_duplicate"
            )
    return dropped


@on_action("moderation.completed")
def handle_moderation_completed(event):
    """Apply a platform moderation verdict to one review.

    The moderation module is target-generic and its queue carries every kind of
    target, so the first thing this handler does is decide whether the verdict
    is even about us: only ``target_type == MODERATION_TARGET_TYPE`` (default
    ``"review"``) is ours, and the ``target_key`` is then the review's id.
    Everything else belongs to listings, profiles or chat and is dropped
    silently — a shared topic is not a delivery error.

    Authorization is **not** re-done here. It already happened in the
    moderation module, which authorized a moderator (or an automated screener
    under an explicit policy), recorded an append-only Verdict and only then
    emitted this fact. Re-asking this module's ``can_moderate`` gate would ask
    the wrong question of the wrong party and, being fail-closed for any target
    type with no callback, would answer "no" to the platform's own decision —
    so the verdict is applied as ``services.SYSTEM_ACTOR``.

    Idempotency is by state (moderation spec §5.3): a redelivered verdict finds
    the review already hidden/published and returns without a write and without
    a fact. Nothing here keeps a table of seen event ids.

    Failures that would repeat on every redelivery — a review we do not have, a
    malformed key, a decision word outside the contract — are logged and
    swallowed, because raising would only poison the subscription with a
    message that can never succeed. Everything else propagates and is retried.
    """
    from django.core.exceptions import ValidationError

    from . import services
    from .conf import reviews_settings
    from .models import Review

    payload = event.payload or {}
    if payload.get("target_type") != reviews_settings.MODERATION_TARGET_TYPE:
        return

    target_key = payload.get("target_key")
    decision = payload.get("decision")
    if not target_key or not decision:
        logger.error(
            "moderation.completed without target_key/decision: %s", event.event_id
        )
        return

    try:
        review = Review.objects.filter(pk=target_key).first()
    except (ValidationError, ValueError):
        logger.error(
            "moderation.completed carried a target_key that is not a review id: %r",
            target_key,
        )
        return
    if review is None:
        # Erased by GDPR, or a case about a review this deployment never had.
        logger.warning("moderation.completed for unknown review %s", target_key)
        return

    # reason_code is the machine word a host can branch on; note is the human
    # sentence. The visibility fact carries one free-text `reason`, so prefer
    # the code and fall back to the note.
    reason = payload.get("reason_code") or payload.get("note") or ""
    try:
        services.apply_verdict(review, decision=decision, reason=reason)
    except services.InvalidVerdictDecision:
        logger.error(
            "moderation.completed for review %s carried unknown decision %r",
            review.id,
            decision,
        )
