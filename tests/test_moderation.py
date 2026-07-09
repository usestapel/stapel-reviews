"""moderate_review + respond: can_moderate callback (fail-closed), hide/publish,
response allow/deny/one-per-review."""
import pytest
from stapel_core.comm import register_function

from stapel_reviews import services
from stapel_reviews.models import Response, ReviewStatus


def _make_review(settings, user, policy=None, target_type="seller", target_key="s1"):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {target_type: policy or {}}}
    return services.create_review(
        target_type=target_type, target_key=target_key, author=user, rating=5
    )


@pytest.mark.django_db
class TestModerate:
    def test_no_moderator_callback_is_fail_closed(self, settings, user, owner_user):
        review = _make_review(settings, user, {})  # no can_moderate set
        with pytest.raises(services.NotAllowedToModerate):
            services.moderate_review(review, actor=owner_user, action="hide")

    def test_moderator_can_hide(self, settings, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        review = _make_review(settings, user, {"can_moderate": "fake.can_mod"})
        services.moderate_review(review, actor=owner_user, action="hide", reason="spam")
        review.refresh_from_db()
        assert review.status == ReviewStatus.HIDDEN

    def test_hidden_then_published(self, settings, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        review = _make_review(settings, user, {"can_moderate": "fake.can_mod"})
        services.moderate_review(review, actor=owner_user, action="hide")
        services.moderate_review(review, actor=owner_user, action="publish")
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED

    def test_callback_denies(self, settings, user, other_user):
        register_function("fake.deny_mod", lambda p: False)
        review = _make_review(settings, user, {"can_moderate": "fake.deny_mod"})
        with pytest.raises(services.NotAllowedToModerate):
            services.moderate_review(review, actor=other_user, action="hide")

    def test_invalid_action(self, settings, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        review = _make_review(settings, user, {"can_moderate": "fake.can_mod"})
        with pytest.raises(services.InvalidModerationAction):
            services.moderate_review(review, actor=owner_user, action="delete")

    def test_moderator_sees_opaque_handle(self, settings, user, owner_user):
        seen = {}

        def can_mod(payload):
            seen.update(payload)
            return True

        register_function("fake.mod_seen", can_mod)
        review = _make_review(settings, user, {"can_moderate": "fake.mod_seen"})
        services.moderate_review(review, actor=owner_user, action="hide")
        assert seen == {
            "actor_id": str(owner_user.pk),
            "target_type": "seller",
            "target_key": "s1",
        }


@pytest.mark.django_db
class TestRespond:
    def test_owner_responds(self, settings, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        review = _make_review(
            settings, user, {"can_moderate": "fake.can_mod", "allow_response": True}
        )
        resp = services.respond(review, author=owner_user, body="thanks")
        assert isinstance(resp, Response)
        assert resp.review_id == review.id

    def test_response_disabled(self, settings, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        review = _make_review(
            settings, user, {"can_moderate": "fake.can_mod", "allow_response": False}
        )
        with pytest.raises(services.ResponseNotAllowed):
            services.respond(review, author=owner_user, body="nope")

    def test_non_owner_cannot_respond(self, settings, user, other_user):
        register_function("fake.deny_mod", lambda p: False)
        review = _make_review(
            settings, user, {"can_moderate": "fake.deny_mod", "allow_response": True}
        )
        with pytest.raises(services.NotAllowedToModerate):
            services.respond(review, author=other_user, body="hi")

    def test_only_one_response(self, settings, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        review = _make_review(
            settings, user, {"can_moderate": "fake.can_mod", "allow_response": True}
        )
        services.respond(review, author=owner_user, body="first")
        with pytest.raises(services.AlreadyResponded):
            services.respond(review, author=owner_user, body="second")
