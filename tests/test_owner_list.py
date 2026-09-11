"""The owner axis of `GET /reviews`: a whole owner's reviews as ROWS.

0.6.0 gave the module an owner without giving it a concept of one — the type
policy's ``owner_key_for`` resolver stamps ``Review.owner_key`` at write time —
but the only thing that could be asked along that axis was a number
(``POST /reviews/aggregates/by-owner``). A seller page needs the rows: every
review of everything that seller owns, across many targets, newest first.

So the list endpoint takes ``owner_key`` INSTEAD of the ``(target_type,
target_key)`` pair — exactly one of the two addressings, both is a 400 — and
everything else about the endpoint (visibility, ordering, anchor pagination,
the item serializer) is the one it already had.
"""
from datetime import datetime, timedelta, timezone

import pytest

from stapel_reviews import services
from stapel_reviews.models import Review, ReviewStatus


LIST_URL = "/reviews/api/v1/reviews"


@pytest.fixture
def staff_user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="dana", email="dana@example.com", password="x", is_staff=True
    )


def _configure(settings, owners, *, moderation="post"):
    """Register the target types under test with an owner resolver reading
    ``owners`` ({target_key: owner_key})."""
    policy = {
        "moderation": moderation,
        "owner_key_for": lambda target_key: owners.get(target_key),
    }
    settings.STAPEL_REVIEWS = {
        "TARGET_TYPES": {"listing": dict(policy), "shop": dict(policy)}
    }


@pytest.mark.django_db
class TestOwnerAddressing:
    def test_owner_list_returns_that_owners_reviews_across_targets(
        self, settings, api_client, user, other_user
    ):
        """Several targets, one owner: one list, and nothing owned by anyone
        else."""
        _configure(settings, {"l1": "seller-1", "l2": "seller-1", "l9": "seller-2"})
        services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        services.create_review(
            target_type="listing", target_key="l2", author=user, rating=4
        )
        services.create_review(
            target_type="listing", target_key="l9", author=other_user, rating=1
        )

        resp = api_client.get(LIST_URL, {"owner_key": "seller-1"})

        assert resp.status_code == 200
        assert resp.data["count"] == 2
        assert {item["target_key"] for item in resp.data["items"]} == {"l1", "l2"}

    def test_target_type_narrows_an_owner_list(self, settings, api_client, user):
        _configure(settings, {"l1": "seller-1", "sh1": "seller-1"})
        services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        services.create_review(
            target_type="shop", target_key="sh1", author=user, rating=3
        )

        resp = api_client.get(
            LIST_URL, {"owner_key": "seller-1", "target_type": "shop"}
        )

        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["items"][0]["target_key"] == "sh1"

    def test_an_unstamped_review_belongs_to_no_owner(self, settings, api_client, user):
        """A type that registers no resolver stamps nothing, and "" is not an
        owner — the same rule the owner aggregate applies."""
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"listing": {}}}
        services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )

        resp = api_client.get(LIST_URL, {"owner_key": ""})
        assert resp.status_code == 400

        resp = api_client.get(LIST_URL, {"owner_key": "seller-1"})
        assert resp.status_code == 200
        assert resp.data["count"] == 0

    def test_both_addressings_is_refused(self, settings, api_client, user):
        _configure(settings, {"l1": "seller-1"})
        services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )

        resp = api_client.get(
            LIST_URL,
            {"owner_key": "seller-1", "target_type": "listing", "target_key": "l1"},
        )

        assert resp.status_code == 400
        assert resp.data["localizable_error"] == "error.400.reviews_ambiguous_addressing"

    def test_neither_addressing_is_refused_as_before(self, settings, api_client):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"listing": {}}}

        resp = api_client.get(LIST_URL)

        assert resp.status_code == 400
        assert resp.data["localizable_error"] == "error.400.reviews_unknown_target_type"


@pytest.mark.django_db
class TestOwnerListVisibility:
    def _seed_pending_and_published(self, settings, user, other_user):
        _configure(settings, {"l1": "seller-1", "l2": "seller-1"}, moderation="pre")
        published = services.create_review(
            target_type="listing", target_key="l1", author=user, rating=5
        )
        Review.objects.filter(id=published.id).update(status=ReviewStatus.PUBLISHED)
        services.create_review(
            target_type="listing", target_key="l2", author=other_user, rating=1
        )
        return published

    def test_unpublished_is_excluded_for_a_member(
        self, settings, api_client, user, other_user
    ):
        published = self._seed_pending_and_published(settings, user, other_user)
        api_client.force_authenticate(user=user)

        resp = api_client.get(LIST_URL, {"owner_key": "seller-1", "include": "all"})

        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["items"][0]["id"] == str(published.id)

    def test_unpublished_is_included_for_staff(
        self, settings, api_client, user, other_user, staff_user
    ):
        self._seed_pending_and_published(settings, user, other_user)
        api_client.force_authenticate(user=staff_user)

        resp = api_client.get(LIST_URL, {"owner_key": "seller-1", "include": "all"})

        assert resp.status_code == 200
        assert resp.data["count"] == 2
        assert {item["status"] for item in resp.data["items"]} == {
            "published",
            "pending",
        }

    def test_staff_without_include_all_sees_published_only(
        self, settings, api_client, user, other_user, staff_user
    ):
        published = self._seed_pending_and_published(settings, user, other_user)
        api_client.force_authenticate(user=staff_user)

        resp = api_client.get(LIST_URL, {"owner_key": "seller-1"})

        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["items"][0]["id"] == str(published.id)


@pytest.mark.django_db
class TestOwnerListPagination:
    def _seed(self, settings, user, n=5):
        _configure(settings, {f"l{i}": "seller-1" for i in range(n)})
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ids = []
        for i in range(n):
            review = services.create_review(
                target_type="listing",
                target_key=f"l{i}",
                author=user,
                rating=(i % 5) + 1,
            )
            # Distinct, ordered created_at so the anchor cursor is stable.
            Review.objects.filter(id=review.id).update(
                created_at=base + timedelta(hours=i)
            )
            ids.append(str(review.id))
        return ids

    def test_anchor_windows_walk_the_whole_owner_list(
        self, settings, api_client, user
    ):
        seeded = self._seed(settings, user, n=5)
        collected = []
        anchor = None
        for _ in range(10):  # safety bound
            params = {"owner_key": "seller-1", "limit": 2}
            if anchor:
                params["anchor"] = anchor
            resp = api_client.get(LIST_URL, params)
            assert resp.status_code == 200
            collected.extend(item["id"] for item in resp.data["items"])
            if not resp.data["has_next"]:
                break
            anchor = resp.data["next_anchor"]

        assert set(collected) == set(seeded)

    def test_windows_do_not_overlap(self, settings, api_client, user):
        self._seed(settings, user, n=5)

        first = api_client.get(LIST_URL, {"owner_key": "seller-1", "limit": 2})
        assert first.data["has_next"] is True
        second = api_client.get(
            LIST_URL,
            {
                "owner_key": "seller-1",
                "limit": 2,
                "anchor": first.data["next_anchor"],
            },
        )

        assert second.data["count"] == 2
        assert {i["id"] for i in first.data["items"]}.isdisjoint(
            {i["id"] for i in second.data["items"]}
        )
