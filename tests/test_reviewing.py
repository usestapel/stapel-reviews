"""create_review: policy callbacks (FakeFunction), one-per-author, pre/post
moderation, rating bounds."""
import pytest
from stapel_core.comm import register_function

from stapel_reviews import services
from stapel_reviews.models import Review, ReviewStatus


def _register_type(settings, name, policy):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {name: policy}}


@pytest.mark.django_db
class TestCanReviewCallback:
    def test_callback_allows(self, settings, user):
        seen = {}

        def can_review(payload):
            seen.update(payload)
            return True

        register_function("fake.can_review", can_review)
        _register_type(settings, "seller", {"can_review": "fake.can_review"})

        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        assert review.status == ReviewStatus.PUBLISHED
        # The module CALLS the host with the opaque handle — no host import.
        assert seen == {
            "author_id": str(user.pk),
            "target_type": "seller",
            "target_key": "s1",
        }

    def test_callback_denies(self, settings, user):
        register_function("fake.deny", lambda p: False)
        _register_type(settings, "seller", {"can_review": "fake.deny"})

        with pytest.raises(services.NotAllowedToReview):
            services.create_review(
                target_type="seller", target_key="s1", author=user, rating=5
            )
        assert Review.objects.count() == 0

    def test_envelope_result_form(self, settings, user):
        register_function("fake.env", lambda p: {"allowed": True})
        _register_type(settings, "seller", {"can_review": "fake.env"})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=4
        )
        assert review.rating == 4

    def test_no_callback_is_unrestricted(self, settings, user):
        _register_type(settings, "listing", {})
        review = services.create_review(
            target_type="listing", target_key="l1", author=user, rating=3
        )
        assert review.status == ReviewStatus.PUBLISHED


@pytest.mark.django_db
class TestOnePerAuthor:
    def test_second_review_rejected(self, settings, user):
        _register_type(settings, "seller", {"one_per_author": True})
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        with pytest.raises(services.DuplicateReview):
            services.create_review(
                target_type="seller", target_key="s1", author=user, rating=1
            )
        assert Review.objects.filter(target_key="s1").count() == 1

    def test_other_author_still_allowed(self, settings, user, other_user):
        _register_type(settings, "seller", {"one_per_author": True})
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        services.create_review(
            target_type="seller", target_key="s1", author=other_user, rating=4
        )
        assert Review.objects.filter(target_key="s1").count() == 2

    def test_disabled_allows_multiple(self, settings, user):
        _register_type(settings, "listing", {"one_per_author": False})
        services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        services.create_review(
            target_type="listing", target_key="l1", author=user, rating=1
        )
        assert Review.objects.filter(target_key="l1").count() == 2

    def test_different_target_not_blocked(self, settings, user):
        _register_type(settings, "seller", {"one_per_author": True})
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        # Same author, DIFFERENT target — allowed.
        services.create_review(
            target_type="seller", target_key="s2", author=user, rating=4
        )
        assert Review.objects.count() == 2


@pytest.mark.django_db
class TestModerationMode:
    def test_post_publishes_immediately(self, settings, user):
        _register_type(settings, "seller", {"moderation": "post"})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        assert review.status == ReviewStatus.PUBLISHED

    def test_pre_holds_pending(self, settings, user):
        _register_type(settings, "seller", {"moderation": "pre"})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        assert review.status == ReviewStatus.PENDING

    def test_pre_review_not_in_aggregate(self, settings, user):
        _register_type(settings, "seller", {"moderation": "pre"})
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        agg = services.aggregate("seller", "s1")
        assert agg.count == 0


@pytest.mark.django_db
class TestRatingBounds:
    def test_rating_too_high(self, settings, user):
        _register_type(settings, "seller", {})
        with pytest.raises(services.InvalidRating):
            services.create_review(
                target_type="seller", target_key="s1", author=user, rating=6
            )

    def test_rating_too_low(self, settings, user):
        _register_type(settings, "seller", {})
        with pytest.raises(services.InvalidRating):
            services.create_review(
                target_type="seller", target_key="s1", author=user, rating=0
            )

    def test_custom_bounds(self, settings, user):
        settings.STAPEL_REVIEWS = {
            "RATING_MAX": 10,
            "TARGET_TYPES": {"seller": {}},
        }
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=9
        )
        assert review.rating == 9

    def test_unknown_target_type(self, settings, user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {}}
        from stapel_reviews.registry import UnknownTargetType

        with pytest.raises(UnknownTargetType):
            services.create_review(
                target_type="ghost", target_key="x", author=user, rating=5
            )
