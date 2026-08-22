"""DRF views for stapel-reviews.

Thin views over :mod:`services`. The reviewable target is opaque
(``target_type`` + ``target_key`` query/body params); all domain authority
(who may review, who owns the target) is delegated to the type policy's comm
callbacks in the service layer.
"""
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelErrorResponse, StapelResponse
from stapel_core.django.api.pagination import AnchorPagination
from stapel_core.django.api.permissions import ANONYMOUS_ALLOWED

from . import services
from .dto import AggregateResponse, ResponseResponse, ReviewResponse
from .errors import (
    ERR_400_DUPLICATE_REVIEW,
    ERR_400_INVALID_MODERATION_ACTION,
    ERR_400_INVALID_RATING,
    ERR_400_RESPONSE_NOT_ALLOWED,
    ERR_400_UNKNOWN_TARGET_TYPE,
    ERR_403_CANNOT_MODERATE,
    ERR_403_CANNOT_REVIEW,
    ERR_404_REVIEW_NOT_FOUND,
    ERR_409_ALREADY_RESPONDED,
)
from .models import Review
from .registry import UnknownTargetType, resolve_policy, check_can_moderate
from .serializers import (
    AggregateResponseSerializer,
    ModerateRequestSerializer,
    RespondRequestSerializer,
    ReviewCreateRequestSerializer,
    ReviewPageSerializer,
    ReviewResponseSerializer,
)


class ReviewAnchorPagination(AnchorPagination):
    """Anchor (cursor) pagination over reviews, newest first — the anchor is
    ``created_at`` (matches the model's default ordering)."""

    page_size = 20
    max_page_size = 100
    anchor_field = "created_at"
    ordering = "-created_at"


class _SettingsRateThrottle(ScopedRateThrottle):
    """Scoped throttle whose rate comes from ``STAPEL_REVIEWS``, not the
    project (mirrors stapel-search's ``_SettingsRateThrottle``): a library
    does not own the project's ``DEFAULT_THROTTLE_RATES``."""

    settings_key = ""

    def get_rate(self):
        from .conf import reviews_settings

        return getattr(reviews_settings, self.settings_key)


class ListThrottle(_SettingsRateThrottle):
    settings_key = "LIST_THROTTLE"


class AggregateThrottle(_SettingsRateThrottle):
    settings_key = "AGGREGATE_THROTTLE"


class SerializerSeamMixin:
    """Overridable serializer seam for every stapel-reviews APIView.

    Host projects can swap the request/response serializer of any view by
    subclassing and setting ``request_serializer_class`` /
    ``response_serializer_class`` — no need to rewrite the HTTP method bodies.
    """

    request_serializer_class = None
    response_serializer_class = None

    def get_request_serializer_class(self):
        return self.request_serializer_class

    def get_response_serializer_class(self):
        return self.response_serializer_class


# ── Mappers ──────────────────────────────────────────────────────────────


def review_to_dto(review: Review) -> ReviewResponse:
    response = getattr(review, "response", None)
    return ReviewResponse(
        id=str(review.id),
        target_type=review.target_type,
        target_key=review.target_key,
        author_id=str(review.author_id),
        rating=review.rating,
        body=review.body,
        status=review.status,
        created_at=review.created_at,
        response=(
            ResponseResponse(
                author_id=str(response.author_id),
                body=response.body,
                created_at=response.created_at,
            )
            if response is not None
            else None
        ),
    )


def _target_params(request):
    """Read (target_type, target_key) from the query string, or (None, None)."""
    return (
        request.query_params.get("target_type"),
        request.query_params.get("target_key"),
    )


#: Shared (target_type, target_key) query parameters — both GET endpoints
#: read them via ``_target_params`` (views.py:92-96) and require them (a
#: missing one is ERR_400_UNKNOWN_TARGET_TYPE, not an empty list/aggregate).
TARGET_QUERY_PARAMETERS = [
    OpenApiParameter(
        "target_type",
        str,
        OpenApiParameter.QUERY,
        required=True,
        description="Host-registered target-type key (registry.py).",
    ),
    OpenApiParameter(
        "target_key",
        str,
        OpenApiParameter.QUERY,
        required=True,
        description="Opaque host-owned target identifier.",
    ),
]

#: Extra query parameter accepted only by the list endpoint (views.py:118-132).
INCLUDE_QUERY_PARAMETER = OpenApiParameter(
    "include",
    str,
    OpenApiParameter.QUERY,
    required=False,
    description=(
        "Omit for published-only. `all` additionally asks for pending/hidden "
        "reviews, but is honored only for a moderator/owner of the target "
        "(the type's can_moderate callback) — a non-moderator asking for "
        "`all` is silently narrowed to published, never an error."
    ),
)

#: ``ReviewAnchorPagination``'s own query params (views.py:38-45), declared by
#: hand for the same reason ``ReviewPageSerializer`` declares the envelope by
#: hand: ``ReviewListCreateView`` is a bare ``APIView``, so drf-spectacular's
#: paginator introspection (which reads ``GenericAPIView.pagination_class``)
#: never runs, and these three params would otherwise be invisible to a
#: generated client (the storefront spec §13.8 note 3).
ANCHOR_QUERY_PARAMETERS = [
    OpenApiParameter(
        "anchor",
        str,
        OpenApiParameter.QUERY,
        required=False,
        description=(
            "Opaque cursor: a `next_anchor`/`prev_anchor` from a previous "
            "page (the review's `created_at`). Omit for the first page."
        ),
    ),
    OpenApiParameter(
        "limit",
        int,
        OpenApiParameter.QUERY,
        required=False,
        description=(
            f"Page size, default {ReviewAnchorPagination.page_size}, max "
            f"{ReviewAnchorPagination.max_page_size}."
        ),
    ),
    OpenApiParameter(
        "direction",
        str,
        OpenApiParameter.QUERY,
        required=False,
        enum=["next", "prev", "center"],
        description="Which side of `anchor` to page toward. Default `next`.",
    ),
]


# ── Views ────────────────────────────────────────────────────────────────


@extend_schema(tags=["Reviews"])
class ReviewListCreateView(SerializerSeamMixin, APIView):
    """List a target's reviews (anchor-paginated, published-only for
    non-moderators), or create a review.

    ``GET`` is anonymously readable (storefront F5 verdict,
    the storefront spec §13.8 note 2) — published-only filtering
    already guarantees a guest sees nothing a moderator would need to hide.
    ``POST`` still requires a real identity (there is an author to attribute
    the review to), so the class gate is DRF's own
    ``IsAuthenticatedOrReadOnly`` (mirrors ``ListingViewSet`` in
    stapel-listings — the fleet's other read-open/write-authenticated view in
    a single class) rather than a per-method override.
    """

    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    throttle_classes = [ListThrottle]
    throttle_scope = "reviews-list"
    request_serializer_class = ReviewCreateRequestSerializer
    response_serializer_class = ReviewResponseSerializer

    @extend_schema(
        parameters=[
            *TARGET_QUERY_PARAMETERS,
            INCLUDE_QUERY_PARAMETER,
            *ANCHOR_QUERY_PARAMETERS,
        ],
        responses={200: ReviewPageSerializer},
    )
    def get(self, request):  # noqa: R007
        target_type, target_key = _target_params(request)
        if not target_type or not target_key:
            return StapelErrorResponse(400, ERR_400_UNKNOWN_TARGET_TYPE)

        include_all = False
        if request.query_params.get("include") == "all":
            # Only a moderator/owner of the target may see pending/hidden. A
            # non-moderator asking for "all" is silently narrowed to published
            # (no leak, no error) — the type's can_moderate callback decides.
            try:
                policy = resolve_policy(target_type)
                include_all = check_can_moderate(
                    policy,
                    actor_id=request.user.pk,
                    target_type=target_type,
                    target_key=target_key,
                )
            except UnknownTargetType:
                include_all = False

        qs = services.list_reviews(
            target_type, target_key, include_all=include_all
        ).select_related("response")

        paginator = ReviewAnchorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        response_cls = self.get_response_serializer_class()
        data = response_cls([review_to_dto(r) for r in page], many=True).data
        return paginator.get_paginated_response(data)

    @extend_schema(
        request=ReviewCreateRequestSerializer,
        responses={201: ReviewResponseSerializer},
    )
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            review = services.create_review(
                target_type=data.target_type,
                target_key=data.target_key,
                author=request.user,
                rating=data.rating,
                body=data.body,
            )
        except UnknownTargetType:
            return StapelErrorResponse(400, ERR_400_UNKNOWN_TARGET_TYPE)
        except services.InvalidRating:
            return StapelErrorResponse(400, ERR_400_INVALID_RATING)
        except services.DuplicateReview:
            return StapelErrorResponse(400, ERR_400_DUPLICATE_REVIEW)
        except services.NotAllowedToReview:
            return StapelErrorResponse(403, ERR_403_CANNOT_REVIEW)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(review_to_dto(review)), status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Reviews"])
class ReviewModerateView(SerializerSeamMixin, APIView):
    """Hide or publish a review (moderation; owner/moderator-only via the
    target type's can_moderate callback)."""

    permission_classes = [permissions.IsAuthenticated]
    request_serializer_class = ModerateRequestSerializer
    response_serializer_class = ReviewResponseSerializer

    @extend_schema(
        request=ModerateRequestSerializer, responses={200: ReviewResponseSerializer}
    )
    def post(self, request, review_id):  # noqa: R007
        review = Review.objects.select_related("response").filter(id=review_id).first()
        if review is None:
            return StapelErrorResponse(404, ERR_404_REVIEW_NOT_FOUND)
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            review = services.moderate_review(
                review, actor=request.user, action=data.action, reason=data.reason
            )
        except services.InvalidModerationAction:
            return StapelErrorResponse(400, ERR_400_INVALID_MODERATION_ACTION)
        except services.NotAllowedToModerate:
            return StapelErrorResponse(403, ERR_403_CANNOT_MODERATE)
        review = Review.objects.select_related("response").filter(id=review.id).first()
        response_cls = self.get_response_serializer_class()
        return StapelResponse(response_cls(review_to_dto(review)))


@extend_schema(tags=["Reviews"])
class ReviewRespondView(SerializerSeamMixin, APIView):
    """Attach the target owner's single reply to a review (owner-only via the
    target type's can_moderate callback; gated by the allow_response policy)."""

    permission_classes = [permissions.IsAuthenticated]
    request_serializer_class = RespondRequestSerializer
    response_serializer_class = ReviewResponseSerializer

    @extend_schema(
        request=RespondRequestSerializer, responses={201: ReviewResponseSerializer}
    )
    def post(self, request, review_id):  # noqa: R007
        review = Review.objects.select_related("response").filter(id=review_id).first()
        if review is None:
            return StapelErrorResponse(404, ERR_404_REVIEW_NOT_FOUND)
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            services.respond(review, author=request.user, body=data.body)
        except services.ResponseNotAllowed:
            return StapelErrorResponse(400, ERR_400_RESPONSE_NOT_ALLOWED)
        except services.NotAllowedToModerate:
            return StapelErrorResponse(403, ERR_403_CANNOT_MODERATE)
        except services.AlreadyResponded:
            return StapelErrorResponse(409, ERR_409_ALREADY_RESPONDED)
        review = Review.objects.select_related("response").filter(id=review.id).first()
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(review_to_dto(review)), status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Reviews"])
class AggregateView(SerializerSeamMixin, APIView):
    """The module-owned rating aggregate (avg/count over published reviews)
    for a target.

    Anonymously readable for the same reason as the list's ``GET``
    (storefront F5 verdict, the storefront spec §13.8 note 2): the
    aggregate is computed over published reviews only, so a guest learns
    nothing a moderator would need withheld.
    """

    permission_classes = [permissions.AllowAny]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    throttle_classes = [AggregateThrottle]
    throttle_scope = "reviews-aggregate"
    response_serializer_class = AggregateResponseSerializer

    @extend_schema(
        parameters=TARGET_QUERY_PARAMETERS,
        responses={200: AggregateResponseSerializer},
    )
    def get(self, request):  # noqa: R007
        target_type, target_key = _target_params(request)
        if not target_type or not target_key:
            return StapelErrorResponse(400, ERR_400_UNKNOWN_TARGET_TYPE)
        agg = services.aggregate(target_type, target_key)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(
                AggregateResponse(
                    target_type=target_type,
                    target_key=target_key,
                    avg=agg.avg,
                    count=agg.count,
                )
            )
        )
