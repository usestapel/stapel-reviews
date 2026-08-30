"""``user.merged`` — a guest's reviews survive signing in.

stapel-auth absorbs an anonymous guest into an existing account and then
DELETES the guest row. Both user columns here are ``on_delete=CASCADE``, so
without this handler the guest's review is not orphaned — it is destroyed,
and the seller's aggregate silently loses a rating nobody asked to remove.
What is pinned here:

* both user columns move — ``Review.author`` and ``Response.author`` —
  whatever the review's status;
* the ``one_per_author`` collision folds the survivor's way, and the dropped
  duplicate takes its response with it and announces the new aggregate;
* a type without that policy, and a type the host de-registered, keep both;
* the handler is idempotent, and a no-op for ids it has never seen;
* a guest with rows to carry and a survivor this service has not projected
  yet RAISES rather than reporting success, so the outbox redelivers instead
  of silently discarding the transfer;
* a malformed id is swallowed — an escaping exception is a poison pill on an
  at-least-once bus, and no redelivery can fix a typo.
"""
import types
import uuid

import pytest

from stapel_reviews.actions import MergeTargetNotReady, handle_user_merged
from stapel_reviews.models import Response, Review, ReviewStatus

pytestmark = pytest.mark.django_db


def _register_type(settings, policy=None, name="seller"):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {name: policy or {}}}


def _event(from_user_id, into_user_id, event_id="evt-merge"):
    return types.SimpleNamespace(
        payload={
            "from_user_id": str(from_user_id),
            "into_user_id": str(into_user_id),
            "reason": "anonymous_promotion",
        },
        event_id=event_id,
    )


def _merge(from_user, into_user):
    handle_user_merged(
        _event(getattr(from_user, "pk", from_user), getattr(into_user, "pk", into_user))
    )


def _review(author, target_key="s1", target_type="seller", rating=5, status=None):
    review = Review.objects.create(
        target_type=target_type,
        target_key=target_key,
        author=author,
        rating=rating,
    )
    if status is not None:
        review.status = status
        review.save(update_fields=["status"])
    return review


def _response(author, review, body="thanks"):
    return Response.objects.create(review=review, author=author, body=body)


# ── the happy path ──────────────────────────────────────────────────────


def test_both_user_columns_move_to_the_survivor(settings, user, other_user, owner_user):
    _register_type(settings)
    review = _review(user, target_key="s1")
    someone_elses = _review(other_user, target_key="s2")
    reply = _response(user, someone_elses)

    _merge(user, owner_user)

    review.refresh_from_db()
    reply.refresh_from_db()
    assert review.author_id == owner_user.pk
    assert reply.author_id == owner_user.pk
    assert not Review.objects.filter(author_id=user.pk).exists()
    assert not Response.objects.filter(author_id=user.pk).exists()


def test_a_pending_review_moves_too(settings, user, owner_user):
    """``pending`` is a review awaiting pre-moderation, not one that stopped
    being the author's."""
    _register_type(settings, {"moderation": "pre"})
    review = _review(user, status=ReviewStatus.PENDING)

    _merge(user, owner_user)

    review.refresh_from_db()
    assert review.author_id == owner_user.pk
    assert review.status == ReviewStatus.PENDING


def test_second_delivery_changes_nothing(settings, user, owner_user):
    _register_type(settings)
    review = _review(user)

    _merge(user, owner_user)
    _merge(user, owner_user)  # at-least-once delivery

    review.refresh_from_db()
    assert review.author_id == owner_user.pk
    assert Review.objects.count() == 1


def test_guest_owning_nothing_is_a_clean_no_op(settings, user, owner_user):
    _register_type(settings)
    _merge(user, owner_user)
    assert Review.objects.count() == 0


def test_merge_into_self_is_a_no_op(settings, user):
    _register_type(settings)
    review = _review(user)
    _merge(user, user)
    review.refresh_from_db()
    assert review.author_id == user.pk


def test_an_event_naming_users_with_nothing_here_does_nothing(
    settings, user, owner_user
):
    _register_type(settings)
    untouched = _review(user)

    handle_user_merged(_event(uuid.uuid4(), owner_user.pk))

    untouched.refresh_from_db()
    assert untouched.author_id == user.pk


# ── the one_per_author collision ────────────────────────────────────────


def test_survivor_review_wins_and_the_guest_duplicate_is_dropped(
    settings, user, owner_user
):
    _register_type(settings, {"one_per_author": True})
    survivor_review = _review(owner_user, target_key="s1", rating=2)
    guest_review = _review(user, target_key="s1", rating=5)
    _response(owner_user, guest_review)

    _merge(user, owner_user)

    survivor_review.refresh_from_db()
    assert survivor_review.rating == 2
    assert not Review.objects.filter(pk=guest_review.pk).exists()
    # The response on the dropped review went with it.
    assert Response.objects.count() == 0
    assert Review.objects.filter(author_id=owner_user.pk).count() == 1


def test_the_dropped_duplicate_announces_the_new_aggregate(
    settings, user, owner_user, captured_events
):
    """A host projecting avg_rating must not be left counting a deleted row."""
    _register_type(settings, {"one_per_author": True})
    _review(owner_user, target_key="s1", rating=2)
    _review(user, target_key="s1", rating=5)
    captured_events.clear()

    _merge(user, owner_user)

    hidden = [e for e in captured_events if e.event_type == "reviews.review.hidden"]
    assert len(hidden) == 1
    assert hidden[0].payload["reason"] == "merged_duplicate"
    assert hidden[0].payload["aggregate"] == {"avg": 2.0, "count": 1}


def test_a_dropped_duplicate_that_was_never_visible_announces_nothing(
    settings, user, owner_user, captured_events
):
    _register_type(settings, {"one_per_author": True, "moderation": "pre"})
    _review(owner_user, target_key="s1", rating=2, status=ReviewStatus.PENDING)
    _review(user, target_key="s1", rating=5, status=ReviewStatus.PENDING)
    captured_events.clear()

    _merge(user, owner_user)

    assert captured_events == []
    assert Review.objects.count() == 1


def test_without_the_policy_both_reviews_survive(settings, user, owner_user):
    """No constraint, no fold: deleting a person's review needs a rule that
    says so, and ``one_per_author`` off is that rule saying nothing."""
    _register_type(settings, {"one_per_author": False})
    _review(owner_user, target_key="s1", rating=2)
    _review(user, target_key="s1", rating=5)

    _merge(user, owner_user)

    assert Review.objects.filter(author_id=owner_user.pk).count() == 2


def test_a_deregistered_target_type_keeps_both(settings, user, owner_user):
    """The host removed the type, so there is no policy to uphold — guessing
    one would delete a person's review on a hunch."""
    _register_type(settings, {"one_per_author": True})
    _review(owner_user, target_key="s1", rating=2)
    _review(user, target_key="s1", rating=5)
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {}}

    _merge(user, owner_user)

    assert Review.objects.filter(author_id=owner_user.pk).count() == 2


def test_a_duplicate_on_another_target_is_not_a_duplicate(
    settings, user, owner_user
):
    _register_type(settings, {"one_per_author": True})
    _review(owner_user, target_key="s1", rating=2)
    _review(user, target_key="s2", rating=5)

    _merge(user, owner_user)

    assert Review.objects.filter(author_id=owner_user.pk).count() == 2


def test_a_second_delivery_after_a_fold_drops_nothing_more(
    settings, user, owner_user, captured_events
):
    _register_type(settings, {"one_per_author": True})
    _review(owner_user, target_key="s1", rating=2)
    _review(user, target_key="s1", rating=5)

    _merge(user, owner_user)
    captured_events.clear()
    _merge(user, owner_user)  # at-least-once delivery

    assert Review.objects.count() == 1
    assert captured_events == []


# ── malformed and missing payloads ──────────────────────────────────────


def test_missing_ids_are_reported_and_ignored(settings, user, owner_user):
    _register_type(settings)
    review = _review(user)

    handle_user_merged(
        types.SimpleNamespace(payload={"into_user_id": str(owner_user.pk)}, event_id="e1")
    )
    handle_user_merged(
        types.SimpleNamespace(payload={"from_user_id": str(user.pk)}, event_id="e2")
    )
    handle_user_merged(types.SimpleNamespace(payload={}, event_id="e3"))

    review.refresh_from_db()
    assert review.author_id == user.pk


def test_unusable_user_ids_are_a_clean_no_op(settings, user, owner_user):
    """``not-a-uuid`` raises ``ValidationError`` — which is NOT a
    ``ValueError`` — from a UUID pk filter. Catching only ``ValueError``
    would make this a poison pill."""
    _register_type(settings)
    review = _review(user)

    handle_user_merged(_event("not-a-uuid", owner_user.pk))
    handle_user_merged(_event(user.pk, "not-a-uuid"))

    review.refresh_from_db()
    assert review.author_id == user.pk


# ── the survivor has not been projected here yet ────────────────────────


def test_unknown_survivor_raises_and_moves_nothing(settings, user):
    _register_type(settings)
    review = _review(user)
    survivor_id = uuid.uuid4()

    with pytest.raises(MergeTargetNotReady) as excinfo:
        handle_user_merged(_event(user.pk, survivor_id))

    assert str(user.pk) in str(excinfo.value)
    assert str(survivor_id) in str(excinfo.value)
    review.refresh_from_db()
    assert review.author_id == user.pk


def test_unknown_survivor_with_an_empty_guest_stays_quiet(settings, user):
    """No rows to carry — a genuine no-op, and the retry loop must not start."""
    _register_type(settings)
    handle_user_merged(_event(user.pk, uuid.uuid4()))
    assert Review.objects.count() == 0


def test_second_delivery_after_a_completed_merge_never_raises(
    settings, user, owner_user
):
    """Post-merge the guest owns nothing, so redelivery takes the quiet path
    even though the guest row itself may be long gone."""
    _register_type(settings)
    review = _review(user)

    _merge(user, owner_user)
    _merge(user, owner_user)  # must not raise MergeTargetNotReady

    review.refresh_from_db()
    assert review.author_id == owner_user.pk


# ── wiring ──────────────────────────────────────────────────────────────


def test_the_subscription_is_registered():
    from stapel_core.comm import action_registry

    assert handle_user_merged in action_registry.handlers("user.merged")


def test_the_lifecycle_pair_check_is_green():
    """``stapel_core.lifecycle.E001`` — one half of an account's life cycle
    answered and not the other is an ERROR as of core 0.52.x."""
    from stapel_core.comm.lifecycle_checks import check_lifecycle_pairs

    assert check_lifecycle_pairs() == []


def test_the_consumes_schema_is_committed():
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "schemas" / "consumes" / "user.merged.json"
    )
    schema = json.loads(path.read_text())
    assert schema["title"] == "user.merged"
    assert set(schema["required"]) == {"from_user_id", "into_user_id"}


def test_only_two_columns_here_name_a_user():
    """The handler moves ``Review.author`` and ``Response.author``. A third
    user column would be silently stranded — fail here, not in production."""
    from django.apps import apps
    from django.conf import settings as django_settings

    found = set()
    for model in apps.get_app_config("reviews").get_models():
        for field in model._meta.get_fields():
            remote = getattr(field, "related_model", None)
            if remote is not None and remote._meta.label == django_settings.AUTH_USER_MODEL:
                found.add(f"{model.__name__}.{field.name}")
    assert found == {"Review.author", "Response.author"}
