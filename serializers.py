"""Serializers for the stapel-reviews API (dataclass-DTO backed).

Every view exposes request/response serializer seams (SerializerSeamMixin);
these are the defaults.
"""
from rest_framework import serializers

from stapel_core.django.api.serializers import StapelDataclassSerializer

from .dto import (
    AggregateResponse,
    ModerateRequest,
    OwnerAggregatesRequest,
    ResponseResponse,
    RespondRequest,
    ReviewCreateRequest,
    ReviewResponse,
)


class ResponseResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ResponseResponse


class ReviewResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ReviewResponse


class AggregateResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = AggregateResponse


class OwnerAggregatesRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = OwnerAggregatesRequest


#: The response schema of ``POST /reviews/aggregates/by-owner``: a map keyed by
#: owner key. Deliberately the same shape the
#: ``reviews.aggregates_by_owner_keys`` comm Function returns — an owner key
#: with no published review is simply not a key of the map, so the HTTP reader
#: and the in-process one need no second convention.
#:
#: A raw OpenAPI fragment rather than a Serializer because the keys are host
#: data, not a fixed property set: a Serializer has named properties by
#: definition, and drf-spectacular resolves neither a bare ``DictField`` nor an
#: unnamed map into anything better than a free-form object (which is what a
#: generated client would then have to guess its way through).
OWNER_AGGREGATES_RESPONSE_SCHEMA = {
    "type": "object",
    "description": (
        "Owner key -> the rating aggregate over published reviews of "
        "everything that owner owns. Owner keys with no published review are "
        "absent from the map rather than present with zeros."
    ),
    "additionalProperties": {
        "type": "object",
        "properties": {
            "avg": {
                "type": "number",
                "format": "double",
                "description": "Mean rating, three decimals (0.0 at count 0).",
            },
            "count": {
                "type": "integer",
                "description": "Number of published reviews across the owner's targets.",
            },
        },
        "required": ["avg", "count"],
    },
}


class ReviewCreateRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ReviewCreateRequest


class ModerateRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ModerateRequest


class RespondRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = RespondRequest


class ReviewPageSerializer(serializers.Serializer):
    """The envelope ``GET /reviews`` actually returns — ``AnchorPagination``'s
    keys (``stapel_core.django.api.pagination.AnchorPagination.
    get_paginated_response``/``get_paginated_response_schema``) wrapping
    ``items``. Schema-only: ``ReviewListCreateView`` is a bare ``APIView``
    (views.py), so drf-spectacular's pagination auto-introspection — which
    only fires for ``GenericAPIView.pagination_class`` — never sees
    ``ReviewAnchorPagination``, and the envelope has to be declared by hand
    or spectacular renders the response as a bare array
    (the storefront spec §13.8 note 3)."""

    items = ReviewResponseSerializer(many=True)
    next_anchor = serializers.CharField(allow_null=True)
    prev_anchor = serializers.CharField(allow_null=True)
    has_next = serializers.BooleanField()
    has_prev = serializers.BooleanField()
    count = serializers.IntegerField()
