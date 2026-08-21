"""moderation.completed — the platform verdict consumer.

Three things are under test here and each one is a seam that has bitten this
fleet before: the system actor really does get past the fail-closed
``can_moderate`` gate (and nothing else does), redelivery of the same verdict
is a no-op down to the emitted fact, and a verdict about somebody else's target
is not ours to apply.
"""
import pytest
from stapel_core.comm import register_function

from stapel_reviews import services
from stapel_reviews.actions import handle_moderation_completed
from stapel_reviews.models import Review, ReviewStatus


class _Event:
    """The envelope shape a subscriber sees (mirrors tests/test_gdpr.py)."""

    def __init__(self, payload, event_id="ev-1"):
        self.payload = payload
        self.event_id = event_id
        self.event_type = "moderation.completed"


def _verdict(review, decision="rejected", **extra):
    payload = {
        "case_id": "11111111-1111-1111-1111-111111111111",
        "target_type": "review",
        "target_key": str(review.id),
        "decision": decision,
    }
    payload.update(extra)
    return _Event(payload)


def _make_review(settings, user, policy=None, target_type="seller"):
    """A review whose target type has NO can_moderate callback — i.e. the
    fail-closed configuration, so any application of a verdict proves the
    bypass rather than riding on a permissive policy."""
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {target_type: policy or {}}}
    return services.create_review(
        target_type=target_type, target_key="s1", author=user, rating=5
    )


@pytest.mark.django_db
class TestSystemActorAuthz:
    def test_verdict_applies_through_the_fail_closed_gate(self, settings, user):
        review = _make_review(settings, user)
        # Sanity: a human actor is refused by this very policy.
        with pytest.raises(services.NotAllowedToModerate):
            services.moderate_review(review, actor=user, action="hide")

        handle_moderation_completed(_verdict(review))
        review.refresh_from_db()
        assert review.status == ReviewStatus.HIDDEN

    def test_bypass_also_survives_a_deregistered_target_type(self, settings, user):
        review = _make_review(settings, user)
        # The host drops the type after the review was written; a human now
        # gets UnknownTargetType, but the platform's verdict must still land.
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {}}
        handle_moderation_completed(_verdict(review))
        review.refresh_from_db()
        assert review.status == ReviewStatus.HIDDEN

    def test_a_denying_callback_still_denies_a_human(self, settings, user, other_user):
        register_function("fake.deny_mod", lambda p: False)
        review = _make_review(settings, user, {"can_moderate": "fake.deny_mod"})
        with pytest.raises(services.NotAllowedToModerate):
            services.moderate_review(review, actor=other_user, action="hide")
        # ...and the bypass is not reachable by looking like the system actor.
        class _Impostor:
            pk = None
            is_system = True

        with pytest.raises(services.NotAllowedToModerate):
            services.moderate_review(review, actor=_Impostor(), action="hide")
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED

    def test_system_actor_may_not_author_a_response(self, settings, user):
        review = _make_review(settings, user, {"allow_response": True})
        with pytest.raises(services.NotAllowedToModerate):
            services.respond(review, author=services.SYSTEM_ACTOR, body="hi")


@pytest.mark.django_db
class TestIdempotency:
    def test_redelivery_writes_nothing_and_emits_nothing(
        self, settings, user, captured_events
    ):
        review = _make_review(settings, user)
        handle_moderation_completed(_verdict(review))
        review.refresh_from_db()
        assert review.status == ReviewStatus.HIDDEN
        assert len([e for e in captured_events if e.event_type == "reviews.review.hidden"]) == 1

        captured_events.clear()
        # Same verdict again — at-least-once delivery is the only kind there is.
        handle_moderation_completed(_verdict(review))
        handle_moderation_completed(_verdict(review))
        review.refresh_from_db()
        assert review.status == ReviewStatus.HIDDEN
        assert captured_events == []

    def test_redelivery_is_by_state_not_by_event_id(self, settings, user, captured_events):
        """A *different* event id carrying the same decision is still a no-op —
        the module keeps no processed-event table, state is the carrier."""
        review = _make_review(settings, user)
        handle_moderation_completed(_verdict(review))
        captured_events.clear()
        again = _verdict(review)
        again.event_id = "ev-completely-different"
        handle_moderation_completed(again)
        assert captured_events == []


@pytest.mark.django_db
class TestDecisionMapping:
    def test_approved_publishes_a_pending_review(self, settings, user):
        review = _make_review(settings, user, {"moderation": "pre"})
        assert review.status == ReviewStatus.PENDING
        handle_moderation_completed(_verdict(review, decision="approved"))
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED

    @pytest.mark.parametrize("decision", ["needs_review", "dismissed"])
    def test_non_acting_decisions_move_nothing(
        self, settings, user, captured_events, decision
    ):
        review = _make_review(settings, user)
        captured_events.clear()
        handle_moderation_completed(_verdict(review, decision=decision))
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED
        assert captured_events == []

    def test_unknown_decision_raises_in_the_service(self, settings, user):
        review = _make_review(settings, user)
        with pytest.raises(services.InvalidVerdictDecision):
            services.apply_verdict(review, decision="banished")

    def test_unknown_decision_is_swallowed_by_the_consumer(self, settings, user, caplog):
        """A word we do not understand must not poison the subscription: it
        would fail identically on every redelivery, forever."""
        review = _make_review(settings, user)
        handle_moderation_completed(_verdict(review, decision="banished"))
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED

    def test_reason_code_rides_into_the_visibility_fact(
        self, settings, user, captured_events
    ):
        review = _make_review(settings, user)
        captured_events.clear()
        handle_moderation_completed(
            _verdict(review, reason_code="spam", note="looks automated")
        )
        hidden = [e for e in captured_events if e.event_type == "reviews.review.hidden"]
        assert len(hidden) == 1
        assert hidden[0].payload["reason"] == "spam"

    def test_note_is_the_fallback_when_no_code_is_sent(
        self, settings, user, captured_events
    ):
        review = _make_review(settings, user)
        captured_events.clear()
        handle_moderation_completed(_verdict(review, note="looks automated"))
        hidden = [e for e in captured_events if e.event_type == "reviews.review.hidden"]
        assert hidden[0].payload["reason"] == "looks automated"


@pytest.mark.django_db
class TestNotOurs:
    def test_a_listing_verdict_is_ignored(self, settings, user, captured_events):
        review = _make_review(settings, user)
        captured_events.clear()
        event = _verdict(review)
        event.payload["target_type"] = "listing"
        handle_moderation_completed(event)
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED
        assert captured_events == []

    def test_the_target_type_name_is_configurable(self, settings, user):
        review = _make_review(settings, user)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {},
            "MODERATION_TARGET_TYPE": "user_review",
        }
        # Under the renamed registration the default word is no longer ours...
        handle_moderation_completed(_verdict(review))
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED
        # ...and the configured one is.
        event = _verdict(review)
        event.payload["target_type"] = "user_review"
        handle_moderation_completed(event)
        review.refresh_from_db()
        assert review.status == ReviewStatus.HIDDEN

    def test_unknown_review_id_is_logged_not_raised(self, settings, user):
        _make_review(settings, user)
        event = _Event(
            {
                "target_type": "review",
                "target_key": "99999999-9999-9999-9999-999999999999",
                "decision": "rejected",
            }
        )
        handle_moderation_completed(event)  # GDPR-erased or never ours

    def test_malformed_target_key_is_logged_not_raised(self, settings, user):
        _make_review(settings, user)
        handle_moderation_completed(
            _Event(
                {"target_type": "review", "target_key": "not-a-uuid", "decision": "rejected"}
            )
        )

    def test_missing_decision_is_logged_not_raised(self, settings, user):
        review = _make_review(settings, user)
        handle_moderation_completed(
            _Event({"target_type": "review", "target_key": str(review.id)})
        )
        review.refresh_from_db()
        assert review.status == ReviewStatus.PUBLISHED


@pytest.mark.django_db
class TestSubscriptionIsWired:
    def test_the_topic_has_a_subscriber(self):
        """The class of defect this fleet keeps shipping is a handler that is
        declared and never subscribed — assert the wiring, not the function."""
        from stapel_core.comm import action_registry

        handlers = action_registry._subscribers.get("moderation.completed", [])
        assert any(h.__name__ == "handle_moderation_completed" for h in handlers)

    def test_the_consumes_schema_is_committed(self):
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "schemas"
            / "consumes"
            / "moderation.completed.json"
        )
        schema = json.loads(path.read_text())
        assert schema["required"] == ["target_type", "target_key", "decision"]
        # The listings trap: a closed consumer schema cannot survive the
        # emitter adding a field, which is what forced a listings release.
        assert schema["additionalProperties"] is True
        assert set(schema["properties"]["decision"]["enum"]) == set(
            services.VERDICT_ACTIONS
        )


@pytest.mark.django_db
class TestModerationContentFunction:
    def test_returns_the_review_content(self, settings, user):
        from stapel_core.comm import call

        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        review = services.create_review(
            target_type="seller",
            target_key="s1",
            author=user,
            rating=4,
            body="rude and late",
        )
        got = call("reviews.moderation_content", {"review_id": str(review.id)})
        assert got["text"] == "rude and late"
        assert got["author_id"] == str(user.pk)
        assert got["rating"] == 4
        assert got["status"] == ReviewStatus.PUBLISHED
        assert got["target_type"] == "seller"
        assert got["target_key"] == "s1"
        # A review has no title/language/media/public URL — empty, not invented.
        assert (got["title"], got["language"], got["media"], got["url"]) == ("", "", [], "")

    def test_missing_review_raises_rather_than_answering_nothing(self, settings, user):
        with pytest.raises(services.ReviewNotFound):
            services.moderation_content("99999999-9999-9999-9999-999999999999")

    def test_content_is_read_fresh_not_snapshotted(self, settings, user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=4, body="first"
        )
        Review.objects.filter(pk=review.pk).update(body="edited")
        assert services.moderation_content(review.id)["text"] == "edited"
