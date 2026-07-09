"""GDPR erasure: user.deleted removes the user's reviews and responses."""
import pytest
from stapel_core.comm import register_function

from stapel_reviews import services
from stapel_reviews.gdpr import ReviewsGDPRProvider
from stapel_reviews.models import Response, Review


@pytest.mark.django_db
class TestGDPR:
    def _seed(self, settings, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.can_mod", "allow_response": True}}
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
