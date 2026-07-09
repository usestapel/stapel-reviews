"""GDPR data handler for stapel-reviews.

This module holds user PII: ``Review.author`` and ``Response.author``. Per the
Stapel standard, a data-holding module subscribes to ``user.deleted`` and
erases that data.

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
        from .models import Response, Review

        # Authored reviews cascade to their response row.
        Review.objects.filter(author_id=user_id).delete()
        # Responses this user wrote on other people's reviews are their PII.
        Response.objects.filter(author_id=user_id).delete()

    def anonymize(self, user_id) -> None:
        # Review content is the author's own; nothing must be retained after
        # deletion.
        pass


def _serialize(rows: list[dict]) -> list[dict]:
    return [
        {k: str(v) for k, v in row.items()}
        for row in rows
    ]
