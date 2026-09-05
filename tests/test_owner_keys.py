"""The seller-wide rating: an owner key stamped at write time, aggregated later.

A marketplace needs "what is this SELLER rated" out of a module that knows only
opaque targets. The mechanism under test is the one denormalised column
(``Review.owner_key``), filled by the target type's optional ``owner_key_for``
resolver when the review is written, read back by
``reviews.aggregates_by_owner_keys`` / ``POST .../aggregates/by-owner/``, and
retro-filled for older rows by ``manage.py reviews_backfill_owner_keys``.

The property that matters most is the *absence* of the mechanism: a host that
registers no resolver must be byte-for-byte where it was — empty owner keys,
an owner aggregate that finds nothing, and no other behaviour changed.
"""
import io

import pytest
from django.core.management import call_command
from stapel_core.comm import call, register_function

from stapel_reviews import services
from stapel_reviews.models import Review


# ── Resolver wiring ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestResolverWiring:
    def test_callable_resolver_stamps_the_owner_at_write_time(self, settings, user):
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {
                "listing": {"owner_key_for": lambda target_key: f"seller-of-{target_key}"}
            }
        }
        review = services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        assert review.owner_key == "seller-of-l1"
        assert Review.objects.get(pk=review.pk).owner_key == "seller-of-l1"

    def test_a_comm_function_name_is_the_data_form_of_the_same_resolver(
        self, settings, user
    ):
        """A policy that has to stay JSON-shaped names a comm Function, exactly
        as can_review/can_moderate do."""
        seen = {}

        def _owner(payload):
            seen.update(payload)
            return {"owner_key": "shop-7"}

        register_function("catalog.owner_of_listing", _owner)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": "catalog.owner_of_listing"}}
        }
        review = services.create_review(
            target_type="listing", target_key="l9", author=user, rating=4
        )
        assert review.owner_key == "shop-7"
        assert seen == {"target_type": "listing", "target_key": "l9"}

    def test_a_bare_string_answer_is_accepted_too(self, settings, user):
        register_function("catalog.owner_flat", lambda p: "shop-8")
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": "catalog.owner_flat"}}
        }
        review = services.create_review(
            target_type="listing", target_key="l10", author=user, rating=4
        )
        assert review.owner_key == "shop-8"

    def test_no_resolver_registered_changes_nothing(self, settings, user):
        """The default position: the column stays empty and no owner exists."""
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"listing": {}}}
        review = services.create_review(
            target_type="listing", target_key="l2", author=user, rating=3
        )
        assert review.owner_key == ""
        assert services.aggregates_by_owner_keys(["l2", ""]) == {}

    def test_a_resolver_returning_none_means_no_owner(self, settings, user):
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": lambda target_key: None}}
        }
        review = services.create_review(
            target_type="listing", target_key="l3", author=user, rating=3
        )
        assert review.owner_key == ""

    def test_a_broken_resolver_blocks_the_write_rather_than_stamping_nothing(
        self, settings, user
    ):
        """Fail loud: a silently unstamped review is a seller rating that is
        quietly missing reviews, and nothing ever reports it."""

        def _boom(target_key):
            raise RuntimeError("catalog is down")

        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"listing": {"owner_key_for": _boom}}}
        with pytest.raises(RuntimeError):
            services.create_review(
                target_type="listing", target_key="l4", author=user, rating=3
            )
        assert not Review.objects.filter(target_key="l4").exists()


# ── Aggregate math ─────────────────────────────────────────────────────────


def _seed_owned(settings, users, entries, *, owners):
    """entries: [(target_type, target_key, rating)]; owners: {target_key: owner}."""
    settings.STAPEL_REVIEWS = {
        "TARGET_TYPES": {
            "listing": {"owner_key_for": lambda target_key: owners.get(target_key)},
            "shop": {"owner_key_for": lambda target_key: owners.get(target_key)},
        }
    }
    for i, (target_type, target_key, rating) in enumerate(entries):
        services.create_review(
            target_type=target_type,
            target_key=target_key,
            author=users[i % len(users)],
            rating=rating,
        )


@pytest.mark.django_db
class TestOwnerAggregate:
    def test_sums_every_target_the_owner_owns(self, settings, user, other_user):
        _seed_owned(
            settings,
            [user, other_user],
            [("listing", "l1", 5), ("listing", "l2", 3), ("listing", "l3", 1)],
            owners={"l1": "s1", "l2": "s1", "l3": "s2"},
        )
        got = call("reviews.aggregates_by_owner_keys", {"owner_keys": ["s1", "s2"]})
        assert got == {"s1": {"avg": 4.0, "count": 2}, "s2": {"avg": 1.0, "count": 1}}

    def test_rounds_like_the_single_target_aggregate(self, settings, user, other_user):
        _seed_owned(
            settings,
            [user, other_user],
            [("listing", "l1", 5), ("listing", "l2", 4), ("listing", "l3", 4)],
            owners={"l1": "s1", "l2": "s1", "l3": "s1"},
        )
        got = call("reviews.aggregates_by_owner_keys", {"owner_keys": ["s1"]})
        assert got["s1"]["avg"] == 4.333
        one = call("reviews.aggregate", {"target_type": "listing", "target_key": "l1"})
        assert one["avg"] == 5.0

    def test_unknown_owner_is_absent_not_zeroed(self, settings, user):
        _seed_owned(
            settings, [user], [("listing", "l1", 5)], owners={"l1": "s1"}
        )
        got = call(
            "reviews.aggregates_by_owner_keys", {"owner_keys": ["s1", "nobody"]}
        )
        assert set(got) == {"s1"}

    def test_reviews_with_no_owner_are_excluded_not_pooled_under_empty(
        self, settings, user, other_user
    ):
        """The empty-owner case: rows a resolver never answered for must not
        become a phantom owner whose rating is 'everything unattributed'."""
        _seed_owned(
            settings,
            [user, other_user],
            [("listing", "l1", 5), ("listing", "l2", 1)],
            owners={"l1": "s1"},  # l2 resolves to None -> owner_key ""
        )
        assert Review.objects.filter(owner_key="").count() == 1
        assert call("reviews.aggregates_by_owner_keys", {"owner_keys": [""]}) == {}
        got = call("reviews.aggregates_by_owner_keys", {"owner_keys": ["s1", ""]})
        assert got == {"s1": {"avg": 5.0, "count": 1}}

    def test_hidden_reviews_do_not_count(self, settings, user, other_user):
        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {
                "listing": {
                    "can_moderate": "fake.can_mod",
                    "owner_key_for": lambda target_key: "s1",
                }
            }
        }
        services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        drop = services.create_review(
            target_type="listing", target_key="l2", author=other_user, rating=1
        )
        services.moderate_review(drop, actor=user, action="hide")
        got = call("reviews.aggregates_by_owner_keys", {"owner_keys": ["s1"]})
        assert got == {"s1": {"avg": 5.0, "count": 1}}

    def test_target_type_narrows_the_owner_aggregate(self, settings, user, other_user):
        _seed_owned(
            settings,
            [user, other_user],
            [("listing", "l1", 5), ("shop", "sh1", 1)],
            owners={"l1": "s1", "sh1": "s1"},
        )
        both = call("reviews.aggregates_by_owner_keys", {"owner_keys": ["s1"]})
        assert both["s1"] == {"avg": 3.0, "count": 2}
        narrowed = call(
            "reviews.aggregates_by_owner_keys",
            {"owner_keys": ["s1"], "target_type": "listing"},
        )
        assert narrowed["s1"] == {"avg": 5.0, "count": 1}

    def test_empty_request_is_an_empty_answer(self, settings, user):
        assert call("reviews.aggregates_by_owner_keys", {"owner_keys": []}) == {}

    def test_schema_rejects_a_payload_without_owner_keys(self, settings, user):
        with pytest.raises(Exception):
            call("reviews.aggregates_by_owner_keys", {"target_type": "listing"})


# ── HTTP surface ───────────────────────────────────────────────────────────

URL = "/reviews/api/v1/aggregates/by-owner/"


@pytest.mark.django_db
class TestOwnerAggregatesEndpoint:
    def test_public_read_returns_the_comm_shape(
        self, api_client, settings, user, other_user
    ):
        _seed_owned(
            settings,
            [user, other_user],
            [("listing", "l1", 5), ("listing", "l2", 3)],
            owners={"l1": "s1", "l2": "s1"},
        )
        response = api_client.post(URL, {"owner_keys": ["s1"]}, format="json")
        assert response.status_code == 200
        assert response.json() == {"s1": {"avg": 4.0, "count": 2}}

    def test_narrowing_by_target_type(self, api_client, settings, user, other_user):
        _seed_owned(
            settings,
            [user, other_user],
            [("listing", "l1", 5), ("shop", "sh1", 1)],
            owners={"l1": "s1", "sh1": "s1"},
        )
        response = api_client.post(
            URL, {"owner_keys": ["s1"], "target_type": "shop"}, format="json"
        )
        assert response.json() == {"s1": {"avg": 1.0, "count": 1}}

    def test_at_most_a_hundred_owner_keys(self, api_client, settings, user):
        assert services.OWNER_KEYS_MAX == 100
        ok = api_client.post(
            URL, {"owner_keys": [f"s{i}" for i in range(100)]}, format="json"
        )
        assert ok.status_code == 200
        too_many = api_client.post(
            URL, {"owner_keys": [f"s{i}" for i in range(101)]}, format="json"
        )
        assert too_many.status_code == 400
        assert (
            too_many.json()["localizable_error"]
            == "error.400.reviews_too_many_owner_keys"
        )

    def test_owner_keys_is_required(self, api_client, settings, user):
        assert api_client.post(URL, {}, format="json").status_code == 400


# ── Backfill ───────────────────────────────────────────────────────────────


def _unowned_review(user, target_type, target_key, rating):
    """A review written before any resolver existed — empty owner_key."""
    return Review.objects.create(
        target_type=target_type, target_key=target_key, author=user, rating=rating
    )


@pytest.mark.django_db
class TestBackfill:
    def test_fills_older_rows_and_a_second_run_is_a_no_op(
        self, settings, user, other_user
    ):
        _unowned_review(user, "listing", "l1", 5)
        _unowned_review(other_user, "listing", "l1", 3)
        _unowned_review(user, "listing", "l2", 4)
        calls = []

        def _owner(target_key):
            calls.append(target_key)
            return f"seller-of-{target_key}"

        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": _owner}}
        }

        first = services.backfill_owner_keys()
        assert first["targets"] == 2
        assert first["stamped_targets"] == 2
        assert first["stamped_rows"] == 3
        # Resolved once per DISTINCT target, not once per review.
        assert sorted(calls) == ["l1", "l2"]
        assert set(Review.objects.values_list("owner_key", flat=True)) == {
            "seller-of-l1",
            "seller-of-l2",
        }

        second = services.backfill_owner_keys()
        assert second == {
            "targets": 0,
            "stamped_targets": 0,
            "stamped_rows": 0,
            "unresolved": 0,
            "no_resolver": 0,
        }
        assert sorted(calls) == ["l1", "l2"]

        # ...and the owner aggregate now sees what predated the resolver.
        assert services.aggregates_by_owner_keys(["seller-of-l1"]) == {
            "seller-of-l1": {"avg": 4.0, "count": 2}
        }

    def test_dry_run_writes_nothing_and_stays_rerunnable(self, settings, user):
        _unowned_review(user, "listing", "l1", 5)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": lambda k: "s1"}}
        }
        stats = services.backfill_owner_keys(dry_run=True)
        assert stats["stamped_rows"] == 1
        assert Review.objects.filter(owner_key="").count() == 1
        assert services.backfill_owner_keys()["stamped_rows"] == 1

    def test_target_type_filter(self, settings, user):
        _unowned_review(user, "listing", "l1", 5)
        _unowned_review(user, "shop", "sh1", 5)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {
                "listing": {"owner_key_for": lambda k: "s1"},
                "shop": {"owner_key_for": lambda k: "s2"},
            }
        }
        stats = services.backfill_owner_keys(target_type="listing")
        assert stats["stamped_rows"] == 1
        assert Review.objects.get(target_type="shop").owner_key == ""

    def test_a_type_with_no_resolver_is_counted_not_fatal(self, settings, user):
        _unowned_review(user, "listing", "l1", 5)
        _unowned_review(user, "shop", "sh1", 5)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": lambda k: "s1"}}
        }  # "shop" is not registered at all
        stats = services.backfill_owner_keys()
        assert stats["stamped_rows"] == 1
        assert stats["no_resolver"] == 1

    def test_a_raising_resolver_is_counted_and_the_walk_continues(
        self, settings, user
    ):
        _unowned_review(user, "listing", "l1", 5)
        _unowned_review(user, "listing", "l2", 5)

        def _flaky(target_key):
            if target_key == "l1":
                raise RuntimeError("catalog is down")
            return "s2"

        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": _flaky}}
        }
        stats = services.backfill_owner_keys()
        assert stats["unresolved"] == 1
        assert stats["stamped_rows"] == 1
        # The unresolved row is still a candidate — fix the resolver, rerun.
        assert Review.objects.filter(target_key="l1", owner_key="").exists()

    def test_batching_walks_every_target_exactly_once(self, settings, user):
        for i in range(7):
            _unowned_review(user, "listing", f"l{i}", 5)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": lambda k: f"s-{k}"}}
        }
        stats = services.backfill_owner_keys(batch_size=2)
        assert stats["targets"] == 7
        assert stats["stamped_rows"] == 7
        assert Review.objects.filter(owner_key="").count() == 0

    def test_limit_bounds_one_slice(self, settings, user):
        for i in range(5):
            _unowned_review(user, "listing", f"l{i}", 5)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": lambda k: f"s-{k}"}}
        }
        stats = services.backfill_owner_keys(limit=2, batch_size=10)
        assert stats["targets"] == 2
        assert Review.objects.filter(owner_key="").count() == 3

    def test_management_command_reports_counts(self, settings, user):
        _unowned_review(user, "listing", "l1", 5)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"listing": {"owner_key_for": lambda k: "s1"}}
        }
        out = io.StringIO()
        call_command("reviews_backfill_owner_keys", "--target-type", "listing", stdout=out)
        printed = out.getvalue()
        assert "1 candidate target(s)" in printed
        assert "stamped 1 review(s)" in printed
        assert Review.objects.get().owner_key == "s1"
