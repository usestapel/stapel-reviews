"""The reply right is not the moderation right, and an owner cannot rate
their own target (RV-1, RV-3, SO-2)."""
import uuid

import pytest
from stapel_core.comm import register_function

from stapel_reviews import services
from stapel_reviews.models import Review


def _types(settings, **policies):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": policies}


@pytest.mark.django_db
class TestReplyIsNotModeration:
    def test_a_seller_allowed_to_reply_cannot_hide(self, settings, user, owner_user):
        # The host lets sellers reply. Hiding stays with its own (fail-closed)
        # gate, which this type does not open to anybody.
        register_function("fake.is_seller", lambda p: True)
        _types(settings, seller={"can_respond": "fake.is_seller", "allow_response": True})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=1
        )

        services.respond(review, author=owner_user, body="sorry")
        with pytest.raises(services.NotAllowedToModerate):
            services.moderate_review(review, actor=owner_user, action="hide")
        review.refresh_from_db()
        assert review.status == "published"

    def test_a_moderator_is_not_a_replier(self, settings, user, other_user):
        register_function("fake.is_mod", lambda p: True)
        _types(settings, seller={"can_moderate": "fake.is_mod", "allow_response": True})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=1
        )
        with pytest.raises(services.NotAllowedToRespond):
            services.respond(review, author=other_user, body="as the platform")

    def test_can_respond_is_asked_with_the_owner_key(self, settings, user, owner_user):
        seen = {}

        def can_respond(payload):
            seen.update(payload)
            return True

        register_function("fake.can_respond_seen", can_respond)
        _types(
            settings,
            seller={
                "can_respond": "fake.can_respond_seen",
                "owner_key_for": lambda key: "shop-9",
            },
        )
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=4
        )
        services.respond(review, author=owner_user, body="thanks")
        assert seen == {
            "actor_id": str(owner_user.pk),
            "target_type": "seller",
            "target_key": "s1",
            "owner_key": "shop-9",
        }


@pytest.mark.django_db
class TestOwnerRepliesByDefault:
    def _policy(self, owner):
        return {"owner_key_for": lambda key: str(owner.pk), "allow_response": True}

    def test_the_target_owner_replies_without_a_callback(self, settings, user, owner_user):
        _types(settings, listing=self._policy(owner_user))
        review = services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        response = services.respond(review, author=owner_user, body="thanks")
        assert response.author_id == owner_user.pk

    def test_a_stranger_does_not(self, settings, user, owner_user, other_user):
        _types(settings, listing=self._policy(owner_user))
        review = services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        with pytest.raises(services.NotAllowedToRespond):
            services.respond(review, author=other_user, body="me too")

    def test_no_owner_means_no_replier(self, settings, user, owner_user):
        _types(settings, listing={"allow_response": True})
        review = services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        with pytest.raises(services.NotAllowedToRespond):
            services.respond(review, author=owner_user, body="thanks")

    def test_api_refusal_names_the_reply_right(self, settings, api_client, user, other_user):
        _types(settings, listing={"allow_response": True})
        review = services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        api_client.force_authenticate(user=other_user)
        resp = api_client.post(
            f"/reviews/api/v1/reviews/{review.id}/response",
            {"body": "hi"},
            format="json",
        )
        assert resp.status_code == 403
        assert resp.data["localizable_error"] == "error.403.reviews_cannot_respond"


@pytest.mark.django_db
class TestProfileTypeShape:
    """The social composite's profile type: the target key is a user id, and
    ``reviews.owner_is_target`` makes that user the owner."""

    POLICY = {
        "moderation": "post",
        "one_per_author": True,
        "allow_response": True,
        "owner_key_for": "reviews.owner_is_target",
    }

    def test_the_profile_owner_replies(self, settings, user, owner_user):
        _types(settings, profile=dict(self.POLICY))
        review = services.create_review(
            target_type="profile", target_key=str(owner_user.pk), author=user, rating=5
        )
        assert review.owner_key == str(owner_user.pk)
        services.respond(review, author=owner_user, body="thank you")

    def test_a_user_cannot_review_their_own_profile(self, settings, user):
        _types(settings, profile=dict(self.POLICY))
        with pytest.raises(services.SelfReview):
            services.create_review(
                target_type="profile", target_key=str(user.pk), author=user, rating=5
            )


@pytest.mark.django_db
class TestSelfReview:
    def test_owner_cannot_rate_own_listing(self, settings, user):
        _types(settings, listing={"owner_key_for": lambda key: str(user.pk)})
        with pytest.raises(services.SelfReview):
            services.create_review(
                target_type="listing", target_key="l1", author=user, rating=5
            )
        assert Review.objects.count() == 0

    def test_policy_can_allow_it(self, settings, user):
        _types(
            settings,
            listing={"owner_key_for": lambda key: str(user.pk), "allow_self_review": True},
        )
        services.create_review(target_type="listing", target_key="l1", author=user, rating=5)

    def test_api_answers_403_self_review(self, settings, auth_client, user):
        _types(settings, listing={"owner_key_for": lambda key: str(user.pk)})
        resp = auth_client.post(
            "/reviews/api/v1/reviews",
            {"target_type": "listing", "target_key": "l1", "rating": 5},
            format="json",
        )
        assert resp.status_code == 403
        assert resp.data["localizable_error"] == "error.403.reviews_self_review"

    def test_a_non_user_owner_key_never_matches(self, settings, user):
        _types(settings, listing={"owner_key_for": lambda key: str(uuid.uuid4())})
        services.create_review(target_type="listing", target_key="l1", author=user, rating=5)
