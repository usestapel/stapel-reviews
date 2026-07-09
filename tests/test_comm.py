"""comm surface — reviews.aggregate Function + emit schema validation
(VALIDATE_SCHEMAS on), and the outbox facts (emit-check)."""
import pytest
from stapel_core.comm import call, register_function

from stapel_reviews import services


def _register_type(settings, policy=None):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": policy or {}}}


@pytest.mark.django_db
class TestAggregateFunction:
    def test_call_in_process(self, settings, user, other_user):
        _register_type(settings)
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        services.create_review(
            target_type="seller", target_key="s1", author=other_user, rating=3
        )
        result = call("reviews.aggregate", {"target_type": "seller", "target_key": "s1"})
        assert result == {"avg": 4.0, "count": 2}

    def test_bad_payload_rejected_by_schema(self, settings, user):
        _register_type(settings)
        # `target_key` missing -> schema validation (required) must reject.
        with pytest.raises(Exception):
            call("reviews.aggregate", {"target_type": "seller"})


@pytest.mark.django_db
class TestEmitFacts:
    def test_publish_emits_fact_with_aggregate(self, settings, user, captured_events):
        _register_type(settings, {"moderation": "post"})
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        facts = [e for e in captured_events if e.event_type == "reviews.review.published"]
        assert len(facts) == 1
        payload = facts[0].payload
        assert payload["target_type"] == "seller"
        assert payload["target_key"] == "s1"
        # The projection payload the host catalog builds its avg_rating from.
        assert payload["aggregate"] == {"avg": 5.0, "count": 1}

    def test_pre_moderation_creates_no_fact(self, settings, user, captured_events):
        _register_type(settings, {"moderation": "pre"})
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        assert captured_events == []

    def test_publish_from_pending_emits_fact(self, settings, user, owner_user, captured_events):
        register_function("fake.can_mod", lambda p: True)
        _register_type(settings, {"moderation": "pre", "can_moderate": "fake.can_mod"})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=4
        )
        services.moderate_review(review, actor=owner_user, action="publish")
        published = [e for e in captured_events if e.event_type == "reviews.review.published"]
        assert len(published) == 1
        assert published[0].payload["aggregate"] == {"avg": 4.0, "count": 1}

    def test_hide_emits_fact_with_updated_aggregate(self, settings, user, owner_user, captured_events):
        register_function("fake.can_mod", lambda p: True)
        _register_type(settings, {"can_moderate": "fake.can_mod"})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        captured_events.clear()  # drop the create's published fact
        services.moderate_review(review, actor=owner_user, action="hide", reason="spam")
        hidden = [e for e in captured_events if e.event_type == "reviews.review.hidden"]
        assert len(hidden) == 1
        assert hidden[0].payload["reason"] == "spam"
        # After hiding the only review, the aggregate is empty.
        assert hidden[0].payload["aggregate"] == {"avg": 0.0, "count": 0}
        # Key routes per-target so a host projects by (target_type, target_key).
        assert hidden[0].key == "seller:s1"

    def test_idempotent_moderation_emits_nothing(self, settings, user, owner_user, captured_events):
        register_function("fake.can_mod", lambda p: True)
        _register_type(settings, {"can_moderate": "fake.can_mod"})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        captured_events.clear()
        # Already published — publishing again is a no-op, no fact.
        services.moderate_review(review, actor=owner_user, action="publish")
        assert captured_events == []
