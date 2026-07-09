"""Serializers for the stapel-reviews API (dataclass-DTO backed).

Every view exposes request/response serializer seams (SerializerSeamMixin);
these are the defaults.
"""
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
