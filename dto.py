"""Dataclass DTOs — the API models of stapel-reviews (never ORM instances)."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ResponseResponse:
    """A target owner's reply to a review.

    Attributes:
        author_id: The responder's user id (the target owner).
        body: Reply text.
        created_at: When the reply was written.
    """

    author_id: str
    body: str
    created_at: datetime


@dataclass
class ReviewResponse:
    """A review of an opaque target.

    Attributes:
        id: Review id (UUID).
        target_type: The host-registered target-type key.
        target_key: Opaque host-owned target identifier.
        author_id: Reviewer's user id.
        rating: Numeric rating (RATING_MIN..RATING_MAX).
        body: Free-text review body.
        status: pending/published/hidden.
        created_at: Creation time.
        response: The owner's reply, if any.
    """

    id: str
    target_type: str
    target_key: str
    author_id: str
    rating: int
    body: str
    status: str
    created_at: datetime
    response: Optional[ResponseResponse] = None


@dataclass
class AggregateResponse:
    """Rating aggregate for a target over published reviews.

    Attributes:
        target_type: The host-registered target-type key.
        target_key: Opaque host-owned target identifier.
        avg: Mean rating (0.0 when count is 0).
        count: Number of published reviews.
    """

    target_type: str
    target_key: str
    avg: float
    count: int


# ── Request DTOs ────────────────────────────────────────────────────────


@dataclass
class ReviewCreateRequest:
    """Create a review.

    Attributes:
        target_type: The host-registered target-type key.
        target_key: Opaque host-owned target identifier.
        rating: Numeric rating (RATING_MIN..RATING_MAX).
        body: Optional review body.
    """

    target_type: str
    target_key: str
    rating: int
    body: str = ""


@dataclass
class ModerateRequest:
    """Moderate a review.

    Attributes:
        action: hide or publish.
        reason: Optional moderation reason (carried in the emitted fact).
    """

    action: str
    reason: str = ""


@dataclass
class RespondRequest:
    """Attach the target owner's reply to a review.

    Attributes:
        body: Reply text.
    """

    body: str = ""
