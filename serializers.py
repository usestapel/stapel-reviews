"""Serializers for the stapel-reviews API (dataclass-DTO backed).

Every view exposes request/response serializer seams (SerializerSeamMixin);
these are the defaults.
"""
from rest_framework import serializers

from stapel_core.django.api.serializers import StapelDataclassSerializer

from .dto import (
    AggregateResponse,
    ModerateRequest,
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
