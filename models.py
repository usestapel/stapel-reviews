"""Models for stapel-reviews.

The generic review core: ``Review`` (an author's rating + body about some
opaque target) and ``Response`` (the target owner's single reply).

House rules (docs/library-standard.md §3.8):
- the review target is **opaque**: ``target_type`` (a host-registered registry
  key) + ``target_key`` (an opaque host-owned string — a UUID, a slug, a
  composite — the module never parses it). There is NO FK to any host model;
  the module is domain-blind by construction.
- the only FK to a host model is the author/actor, and only via
  ``settings.AUTH_USER_MODEL``.
"""
import uuid

from django.conf import settings
from django.db import models


class ReviewStatus(models.TextChoices):
    """Lifecycle of a review's visibility.

    Members:
        PENDING: Created under pre-moderation; invisible until published.
        PUBLISHED: Visible to everyone; counts toward the aggregate.
        HIDDEN: Moderated out; invisible and excluded from the aggregate.
    """

    PENDING = "pending", "Pending"
    PUBLISHED = "published", "Published"
    HIDDEN = "hidden", "Hidden"


#: Review statuses that count toward the public aggregate and the
#: published-only list surface.
VISIBLE_STATUSES = (ReviewStatus.PUBLISHED,)


class Review(models.Model):
    """A single review of an opaque target by an author.

    ``(target_type, target_key)`` identifies the reviewed thing without the
    module knowing what it is. ``target_type`` must be a key the host has
    registered in ``STAPEL_REVIEWS["TARGET_TYPES"]``; ``target_key`` is an
    opaque host string the module stores and groups by but never interprets.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The opaque target. Neither field is an FK — the module is domain-blind.
    target_type = models.CharField(max_length=64, db_index=True)
    target_key = models.CharField(max_length=255, db_index=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="authored_reviews",
    )
    # Inclusive 1..5 by default; bounds are the RATING_MIN/RATING_MAX knobs.
    rating = models.PositiveSmallIntegerField()
    body = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.PUBLISHED
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["target_type", "target_key", "status"],
                name="rev_target_status",
            ),
            models.Index(
                fields=["target_type", "target_key", "-created_at"],
                name="rev_target_created",
            ),
            models.Index(fields=["author"], name="rev_author"),
        ]

    def __str__(self):
        return f"{self.author_id} → {self.target_type}:{self.target_key} ({self.rating})"


class Response(models.Model):
    """The target owner's single reply to a review.

    Enabled per target type by the ``allow_response`` policy (default
    ``RESPONSES``). Exactly one response per review (OneToOne). The responder
    is whoever the type's ``can_moderate`` callback authorizes (the target
    owner), recorded as ``author``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.OneToOneField(
        Review, on_delete=models.CASCADE, related_name="response"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="authored_review_responses",
    )
    body = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"response to {self.review_id}"
