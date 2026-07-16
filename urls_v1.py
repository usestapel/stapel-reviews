"""v1 URL set for stapel-reviews (api-versioning.md §2, §6).

No global prefix here — the root ``urls.py`` mounts this module under
``api/v1/`` and the host mounts that under ``reviews/``:

    path("reviews/", include("stapel_reviews.urls"))   # -> /reviews/api/v1/...
"""
from typing import NamedTuple

from django.urls import path

from .views import (
    AggregateView,
    ReviewListCreateView,
    ReviewModerateView,
    ReviewRespondView,
)

urlpatterns = [
    path("reviews", ReviewListCreateView.as_view(), name="reviews-list-create"),
    path("reviews/aggregate", AggregateView.as_view(), name="reviews-aggregate"),
    path(
        "reviews/<uuid:review_id>/moderate",
        ReviewModerateView.as_view(),
        name="reviews-moderate",
    ),
    path(
        "reviews/<uuid:review_id>/response",
        ReviewRespondView.as_view(),
        name="reviews-respond",
    ),
]


class GateEntry(NamedTuple):
    """One gated URL block: which flags gate which url patterns (capability-config.md §2 p.2).

    ``flags`` compose with OR — the block is mounted while ANY flag is on,
    and disappears only when ALL of them are off. Empty flags = always on.
    """

    name: str
    flags: tuple
    patterns: tuple


#: Gate registry (capability-config.md §2 p.2): reviews has no per-method
#: config gates — its axes (MODERATION_DEFAULT, RESPONSES) are behavioral and
#: TARGET_TYPES is a merge-registry; none unmounts an endpoint, so the whole
#: URL surface is a single always-on block. Declared as a registry entry (rather
#: than left implicit) so the capabilities.json emitter has a uniform mechanism.
GATE_REGISTRY: dict = {
    "reviews.api": GateEntry("reviews.api", (), tuple(urlpatterns)),
}
