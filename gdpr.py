"""GDPR data handler for stapel-reviews.

This module holds user PII: ``Review.author`` and ``Response.author``. It
answers the erasure protocol through this provider: in a monolith stapel-gdpr
walks ``gdpr_registry`` directly; in a split fleet core's provider bridge
(stapel-core 0.83.1+) answers ``gdpr.erasure.requested`` on its behalf and
confirms with ``gdpr.section.erased``. The legacy ``user.deleted`` handler in
``actions.py`` runs the same ``delete``.

- Authored reviews are hard-deleted (cascading to any Response on them). A
  review is the author's own content; deletion — not anonymization — is
  correct.
- The user's authored responses to *other* people's reviews are removed (their
  authorship is their PII), leaving those reviews intact.
"""
from stapel_core.gdpr import GDPRProvider


class ReviewsGDPRProvider(GDPRProvider):
    section = "reviews"

    def export(self, user_id) -> dict:
        from .models import Response, Review

        reviews = list(
            Review.objects.filter(author_id=user_id).values(
                "id", "target_type", "target_key", "rating", "body", "status"
            )
        )
        responses = list(
            Response.objects.filter(author_id=user_id).values("review_id", "body")
        )
        return {
            "reviews": _serialize(reviews),
            "responses": _serialize(responses),
        }

    def delete(self, user_id) -> None:
        from django.db import transaction

        from . import services
        from .models import VISIBLE_STATUSES, Response, Review, ReviewStatus

        with transaction.atomic():
            visible = list(
                Review.objects.filter(
                    author_id=user_id, status__in=VISIBLE_STATUSES
                )
            )
            # Authored reviews cascade to their response row.
            Review.objects.filter(author_id=user_id).delete()
            # Responses this user wrote on other people's reviews are their PII.
            Response.objects.filter(author_id=user_id).delete()
            # A published review that left the visible set is the same fact a
            # moderator's hide emits: a host projecting the aggregate must stop
            # counting it. Emitted after the delete so the carried aggregate is
            # the one without it, inside the transaction so fact and removal
            # commit together. One fact per target is enough for a projection,
            # but each review is its own fact for a consumer keyed by review.
            for review in visible:
                review.status = ReviewStatus.HIDDEN
                services._emit_review_fact(
                    review, services.EVENT_HIDDEN, reason="erased"
                )

    def anonymize(self, user_id) -> None:
        # Review content is the author's own; nothing must be retained after
        # deletion.
        pass


def _serialize(rows: list[dict]) -> list[dict]:
    return [
        {k: str(v) for k, v in row.items()}
        for row in rows
    ]
