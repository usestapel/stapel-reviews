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
