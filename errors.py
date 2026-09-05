"""i18n error keys of stapel-reviews.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_400_UNKNOWN_TARGET_TYPE = "error.400.reviews_unknown_target_type"
ERR_400_INVALID_RATING = "error.400.reviews_invalid_rating"
ERR_400_DUPLICATE_REVIEW = "error.400.reviews_duplicate_review"
ERR_400_INVALID_MODERATION_ACTION = "error.400.reviews_invalid_moderation_action"
ERR_400_RESPONSE_NOT_ALLOWED = "error.400.reviews_response_not_allowed"
# The batch owner-aggregate endpoint is public and unauthenticated, so the
# number of owner keys one call may name is bounded (services.OWNER_KEYS_MAX).
ERR_400_TOO_MANY_OWNER_KEYS = "error.400.reviews_too_many_owner_keys"
ERR_403_CANNOT_REVIEW = "error.403.reviews_cannot_review"
# A guest (anonymous account) on the review write while ALLOW_ANONYMOUS_WRITES
# is off. Distinct from CANNOT_REVIEW: that one is the host policy's verdict
# about this author and this target, this one is about the account itself, and
# only the second is fixed by signing up.
ERR_403_ANONYMOUS_NOT_ALLOWED = "error.403.reviews_anonymous_not_allowed"
ERR_403_CANNOT_MODERATE = "error.403.reviews_cannot_moderate"
ERR_404_REVIEW_NOT_FOUND = "error.404.reviews_review_not_found"
ERR_409_ALREADY_RESPONDED = "error.409.reviews_already_responded"

STAPEL_REVIEWS_ERRORS = {
    ERR_400_UNKNOWN_TARGET_TYPE: "Unknown review target type",
    ERR_400_INVALID_RATING: "Rating is out of the allowed range",
    ERR_400_DUPLICATE_REVIEW: "You have already reviewed this target",
    ERR_400_INVALID_MODERATION_ACTION: "Moderation action must be one of: hide, publish",
    ERR_400_RESPONSE_NOT_ALLOWED: "Responses are not allowed for this target type",
    ERR_400_TOO_MANY_OWNER_KEYS: "Too many owner keys in one request (max {max})",
    ERR_403_CANNOT_REVIEW: "You are not allowed to review this target",
    ERR_403_ANONYMOUS_NOT_ALLOWED: "A guest account may not leave a review",
    ERR_403_CANNOT_MODERATE: "You are not allowed to moderate reviews of this target",
    ERR_404_REVIEW_NOT_FOUND: "Review not found",
    ERR_409_ALREADY_RESPONDED: "This review already has a response",
}

register_service_errors(STAPEL_REVIEWS_ERRORS)

__all__ = [
    "STAPEL_REVIEWS_ERRORS",
    "ERR_400_UNKNOWN_TARGET_TYPE",
    "ERR_400_INVALID_RATING",
    "ERR_400_DUPLICATE_REVIEW",
    "ERR_400_INVALID_MODERATION_ACTION",
    "ERR_400_RESPONSE_NOT_ALLOWED",
    "ERR_400_TOO_MANY_OWNER_KEYS",
    "ERR_403_CANNOT_REVIEW",
    "ERR_403_ANONYMOUS_NOT_ALLOWED",
    "ERR_403_CANNOT_MODERATE",
    "ERR_404_REVIEW_NOT_FOUND",
    "ERR_409_ALREADY_RESPONDED",
]
