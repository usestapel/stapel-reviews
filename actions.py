"""Action subscriptions of stapel-reviews.

Handlers must be idempotent: delivery is at-least-once (outbox retries,
broker redelivery). Consumes contracts live in ``schemas/consumes/``.
"""
import logging

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)


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
