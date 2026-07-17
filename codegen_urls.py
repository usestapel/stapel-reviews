"""Canonical-prefix URLconf for contract emission (contract-pipeline.md §2).

stapel-reviews's own ``urls.py`` already bakes ``api/v1/`` into every path
(``api/v1/reviews``, ``api/v1/reviews/aggregate``, ...) and documents its own
expected host mount::

    path("reviews/", include("stapel_reviews.urls"))

That is the canonical public API prefix (``reviews/api/v1/...``) — the same
``<mod>/api/v1/`` shape every other pair-backend uses. This harness urlconf
reproduces exactly that documented mount, so drf-spectacular emits
``/reviews/api/v1/...`` paths.

reviews is validated standalone (no monolith slice exists yet to diff against;
contract-pipeline.md §9 fallback path applies until reviews is mounted there).
"""
from django.conf.urls import include
from django.urls import path

urlpatterns = [
    path("reviews/", include("stapel_reviews.urls")),
]
