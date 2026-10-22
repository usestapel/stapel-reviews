"""GDPR erasure: user.deleted removes the user's reviews and responses."""
import pytest
from stapel_core.comm import register_function

from stapel_reviews import services
from stapel_reviews.gdpr import ReviewsGDPRProvider
from stapel_reviews.models import Response, Review


@pytest.mark.django_db
class TestGDPR:
    def _seed(self, settings, user, owner_user):
        register_function("fake.can_respond", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_respond": "fake.can_respond", "allow_response": True}}
        }
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        services.respond(review, author=owner_user, body="thanks")
        return review

    def test_delete_removes_authored_reviews(self, settings, user, owner_user):
        self._seed(settings, user, owner_user)
        ReviewsGDPRProvider().delete(user.pk)
        # The review (author=user) is gone, cascading its response.
        assert Review.objects.filter(author=user).count() == 0
        assert Response.objects.count() == 0

    def test_delete_removes_authored_responses(self, settings, user, owner_user, other_user):
        # owner_user authored a response on user's review; deleting owner_user
        # removes the response but leaves the review.
        self._seed(settings, user, owner_user)
        ReviewsGDPRProvider().delete(owner_user.pk)
        assert Review.objects.filter(author=user).count() == 1
        assert Response.objects.filter(author=owner_user).count() == 0

    def test_export_shape(self, settings, user, owner_user):
        self._seed(settings, user, owner_user)
        data = ReviewsGDPRProvider().export(user.pk)
        assert len(data["reviews"]) == 1
        assert data["reviews"][0]["target_type"] == "seller"

    def test_user_deleted_action(self, settings, user, owner_user):
        self._seed(settings, user, owner_user)
        from stapel_reviews.actions import handle_user_deleted

        class _Event:
            event_id = "e1"
            payload = {"user_id": str(user.pk)}

        handle_user_deleted(_Event())
        assert Review.objects.filter(author=user).count() == 0


@pytest.mark.django_db
class TestErasureAnnouncesTheAggregate:
    """An erased published review leaves the visible set: a host projecting
    the rating must hear about it (RV-4)."""

    def test_a_published_review_emits_hidden_with_the_new_aggregate(
        self, settings, user, other_user, captured_events
    ):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        services.create_review(target_type="seller", target_key="s1", author=user, rating=1)
        services.create_review(
            target_type="seller", target_key="s1", author=other_user, rating=5
        )
        captured_events.clear()

        ReviewsGDPRProvider().delete(user.pk)

        hidden = [e for e in captured_events if e.event_type == "reviews.review.hidden"]
        assert len(hidden) == 1
        assert hidden[0].payload["reason"] == "erased"
        assert hidden[0].payload["aggregate"] == {"avg": 5.0, "count": 1}

    def test_a_pending_review_emits_nothing(self, settings, user, captured_events):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {"moderation": "pre"}}}
        services.create_review(target_type="seller", target_key="s1", author=user, rating=1)
        captured_events.clear()

        ReviewsGDPRProvider().delete(user.pk)

        assert captured_events == []

    def test_a_second_erasure_emits_nothing(self, settings, user, captured_events):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        services.create_review(target_type="seller", target_key="s1", author=user, rating=1)
        ReviewsGDPRProvider().delete(user.pk)
        captured_events.clear()

        ReviewsGDPRProvider().delete(user.pk)

        assert captured_events == []
