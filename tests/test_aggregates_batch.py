"""The two aggregate Functions a host rating Projection is declared against.

``stapel-shop``'s ListingReviewSummary Projection names
``reviews.aggregates_by_keys`` as its ``live_query`` and
``reviews.aggregates_export`` as its ``source_of_truth`` — until this release
both raised FunctionNotRegistered. The contracts under test are the ones
``stapel_core.comm.projections`` actually calls, not the ones a docstring
remembers: ``{"keys": [...]}`` -> ``{key: fields}`` for the first, and
``{"cursor", "limit"}`` -> ``{"rows", "cursor", "total"}`` with a per-row
``seq`` for the second.
"""
import pytest
from stapel_core.comm import call

from stapel_reviews import services


def _seed(settings, users, entries):
    """entries: [(target_type, target_key, rating)] — returns nothing."""
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}, "listing": {}}}
    for i, (target_type, target_key, rating) in enumerate(entries):
        services.create_review(
            target_type=target_type,
            target_key=target_key,
            author=users[i % len(users)],
            rating=rating,
        )


@pytest.mark.django_db
class TestAggregatesByKeys:
    def test_batches_many_targets_in_one_answer(self, settings, user, other_user):
        _seed(
            settings,
            [user, other_user],
            [("seller", "s1", 5), ("seller", "s1", 3), ("seller", "s2", 4)],
        )
        got = call("reviews.aggregates_by_keys", {"keys": ["s1", "s2"]})
        assert got == {"s1": {"avg": 4.0, "count": 2}, "s2": {"avg": 4.0, "count": 1}}

    def test_unreviewed_keys_are_absent_not_zeroed(self, settings, user):
        _seed(settings, [user], [("seller", "s1", 5)])
        got = call("reviews.aggregates_by_keys", {"keys": ["s1", "nobody-reviewed-me"]})
        assert set(got) == {"s1"}

    def test_agrees_with_the_single_target_function(self, settings, user, other_user):
        _seed(settings, [user, other_user], [("seller", "s1", 5), ("seller", "s1", 2)])
        one = call("reviews.aggregate", {"target_type": "seller", "target_key": "s1"})
        many = call("reviews.aggregates_by_keys", {"keys": ["s1"]})
        assert many["s1"] == one

    def test_hidden_reviews_do_not_count(self, settings, user, other_user):
        from stapel_core.comm import register_function

        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.can_mod"}}
        }
        keep = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        drop = services.create_review(
            target_type="seller", target_key="s1", author=other_user, rating=1
        )
        services.moderate_review(drop, actor=user, action="hide")
        got = call("reviews.aggregates_by_keys", {"keys": ["s1"]})
        assert got["s1"] == {"avg": 5.0, "count": 1}
        assert keep.status == "published"

    def test_target_type_narrows_a_shared_key(self, settings, user, other_user):
        """Two target types can legitimately share a key. Core's Projection
        sends only `keys`, so the unnarrowed answer spans both — that is the
        documented behavior, and the narrowing parameter is the way out."""
        _seed(settings, [user, other_user], [("seller", "x", 5), ("listing", "x", 1)])
        both = call("reviews.aggregates_by_keys", {"keys": ["x"]})
        assert both["x"] == {"avg": 3.0, "count": 2}
        narrowed = call(
            "reviews.aggregates_by_keys", {"keys": ["x"], "target_type": "listing"}
        )
        assert narrowed["x"] == {"avg": 1.0, "count": 1}

    def test_empty_keys_is_an_empty_answer(self, settings, user):
        assert call("reviews.aggregates_by_keys", {"keys": []}) == {}

    def test_schema_rejects_a_payload_without_keys(self, settings, user):
        with pytest.raises(Exception):
            call("reviews.aggregates_by_keys", {"target_type": "seller"})


@pytest.mark.django_db
class TestAggregatesExport:
    def test_exports_every_target_with_the_core_row_shape(
        self, settings, user, other_user
    ):
        _seed(
            settings,
            [user, other_user],
            [("seller", "s1", 5), ("seller", "s1", 3), ("listing", "l1", 2)],
        )
        got = call("reviews.aggregates_export", {})
        assert got["cursor"] is None
        assert got["total"] == 2
        rows = {r["target_key"]: r for r in got["rows"]}
        assert set(rows) == {"s1", "l1"}
        assert rows["s1"]["avg"] == 4.0
        assert rows["s1"]["count"] == 2
        assert rows["s1"]["target_type"] == "seller"
        # seq is unix MILLISECONDS — the same clock as an Event timestamp, so a
        # live fact arriving mid-rebuild outranks the snapshot row.
        import time

        assert rows["s1"]["seq"] > (time.time() - 3600) * 1000

    def test_paging_walks_every_row_exactly_once(self, settings, user, other_user):
        _seed(
            settings,
            [user, other_user],
            [("seller", f"s{i}", 3) for i in range(7)],
        )
        seen, cursor, pages = [], None, 0
        while True:
            page = call("reviews.aggregates_export", {"cursor": cursor, "limit": 2})
            seen.extend(r["target_key"] for r in page["rows"])
            pages += 1
            cursor = page["cursor"]
            if not cursor:
                break
            assert pages < 20, "cursor did not terminate"
        assert sorted(seen) == sorted(f"s{i}" for i in range(7))
        assert len(seen) == len(set(seen))

    def test_total_is_first_page_only(self, settings, user, other_user):
        _seed(settings, [user, other_user], [("seller", f"s{i}", 3) for i in range(4)])
        first = call("reviews.aggregates_export", {"limit": 2})
        assert first["total"] == 4
        second = call("reviews.aggregates_export", {"cursor": first["cursor"], "limit": 2})
        assert second["total"] is None

    def test_hidden_reviews_are_not_exported(self, settings, user):
        from stapel_core.comm import register_function

        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.can_mod"}}
        }
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        services.moderate_review(review, actor=user, action="hide")
        assert call("reviews.aggregates_export", {})["rows"] == []

    def test_export_agrees_with_the_keyed_read(self, settings, user, other_user):
        _seed(
            settings,
            [user, other_user],
            [("seller", "s1", 5), ("seller", "s1", 4), ("listing", "l1", 1)],
        )
        rows = {r["target_key"]: r for r in call("reviews.aggregates_export", {})["rows"]}
        keyed = call("reviews.aggregates_by_keys", {"keys": list(rows)})
        for key, row in rows.items():
            assert keyed[key] == {"avg": row["avg"], "count": row["count"]}

    def test_limit_is_clamped(self, settings, user):
        _seed(settings, [user], [("seller", "s1", 5)])
        assert call("reviews.aggregates_export", {"limit": 10_000})["rows"]
        assert services.aggregates_export(limit=10_000)["rows"]

    def test_a_forged_cursor_is_refused(self, settings, user):
        with pytest.raises(services.InvalidExportCursor):
            services.aggregates_export(cursor="not-a-cursor-we-issued")
