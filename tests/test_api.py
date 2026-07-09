"""HTTP surface: create/list/aggregate/moderate/respond + anchor windows."""
from datetime import datetime, timedelta, timezone

import pytest
from stapel_core.comm import register_function

from stapel_reviews import services
from stapel_reviews.models import Review, ReviewStatus


@pytest.mark.django_db
class TestCreateAndList:
    def test_create_review(self, settings, auth_client, user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        resp = auth_client.post(
            "/reviews/api/reviews",
            {"target_type": "seller", "target_key": "s1", "rating": 5, "body": "great"},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["status"] == "published"
        assert resp.data["rating"] == 5

    def test_unknown_type_400(self, settings, auth_client):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {}}
        resp = auth_client.post(
            "/reviews/api/reviews",
            {"target_type": "ghost", "target_key": "x", "rating": 5},
            format="json",
        )
        assert resp.status_code == 400

    def test_list_published_only_for_non_owner(self, settings, auth_client, user, other_user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {"moderation": "pre"}}}
        # published one
        services.create_review(
            target_type="seller", target_key="s1", author=other_user, rating=4
        )
        Review.objects.filter(author=other_user).update(status=ReviewStatus.PUBLISHED)
        # pending one (pre-moderation) — must not show to a non-moderator
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=1
        )
        resp = auth_client.get(
            "/reviews/api/reviews", {"target_type": "seller", "target_key": "s1"}
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["items"][0]["status"] == "published"

    def test_list_requires_target(self, settings, auth_client):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        resp = auth_client.get("/reviews/api/reviews")
        assert resp.status_code == 400

    def test_moderator_include_all(self, settings, api_client, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"moderation": "pre", "can_moderate": "fake.can_mod"}}
        }
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=1
        )
        api_client.force_authenticate(user=owner_user)
        resp = api_client.get(
            "/reviews/api/reviews",
            {"target_type": "seller", "target_key": "s1", "include": "all"},
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["items"][0]["status"] == "pending"


@pytest.mark.django_db
class TestAnchorWindows:
    def _seed(self, settings, user, n=5):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        ids = []
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(n):
            r = services.create_review(
                target_type="seller", target_key="s1", author=user, rating=(i % 5) + 1
            )
            # Force distinct, ordered created_at so the anchor cursor is stable.
            Review.objects.filter(id=r.id).update(created_at=base + timedelta(hours=i))
            ids.append(str(r.id))
        return ids

    def test_first_window_and_next(self, settings, auth_client, user):
        self._seed(settings, user, n=5)
        resp = auth_client.get(
            "/reviews/api/reviews",
            {"target_type": "seller", "target_key": "s1", "limit": 2},
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 2
        assert resp.data["has_next"] is True
        anchor = resp.data["next_anchor"]
        assert anchor

        resp2 = auth_client.get(
            "/reviews/api/reviews",
            {"target_type": "seller", "target_key": "s1", "limit": 2, "anchor": anchor},
        )
        assert resp2.data["count"] == 2
        # No overlap between the two windows.
        first_ids = {i["id"] for i in resp.data["items"]}
        second_ids = {i["id"] for i in resp2.data["items"]}
        assert first_ids.isdisjoint(second_ids)

    def test_walks_all_items(self, settings, auth_client, user):
        self._seed(settings, user, n=5)
        collected = []
        anchor = None
        for _ in range(10):  # safety bound
            params = {"target_type": "seller", "target_key": "s1", "limit": 2}
            if anchor:
                params["anchor"] = anchor
            resp = auth_client.get("/reviews/api/reviews", params)
            collected.extend(i["id"] for i in resp.data["items"])
            if not resp.data["has_next"]:
                break
            anchor = resp.data["next_anchor"]
        assert len(set(collected)) == 5


@pytest.mark.django_db
class TestAggregateEndpoint:
    def test_aggregate(self, settings, auth_client, user, other_user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        services.create_review(
            target_type="seller", target_key="s1", author=other_user, rating=1
        )
        resp = auth_client.get(
            "/reviews/api/reviews/aggregate",
            {"target_type": "seller", "target_key": "s1"},
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 2
        assert resp.data["avg"] == 3.0


@pytest.mark.django_db
class TestModerateAndRespondEndpoints:
    def test_moderate_hide(self, settings, api_client, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.can_mod"}}
        }
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        api_client.force_authenticate(user=owner_user)
        resp = api_client.post(
            f"/reviews/api/reviews/{review.id}/moderate",
            {"action": "hide", "reason": "spam"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "hidden"

    def test_moderate_forbidden(self, settings, api_client, user, other_user):
        register_function("fake.deny", lambda p: False)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.deny"}}
        }
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        api_client.force_authenticate(user=other_user)
        resp = api_client.post(
            f"/reviews/api/reviews/{review.id}/moderate",
            {"action": "hide"},
            format="json",
        )
        assert resp.status_code == 403

    def test_moderate_404(self, settings, auth_client):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        import uuid

        resp = auth_client.post(
            f"/reviews/api/reviews/{uuid.uuid4()}/moderate",
            {"action": "hide"},
            format="json",
        )
        assert resp.status_code == 404

    def test_respond(self, settings, api_client, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.can_mod", "allow_response": True}}
        }
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        api_client.force_authenticate(user=owner_user)
        resp = api_client.post(
            f"/reviews/api/reviews/{review.id}/response",
            {"body": "thank you"},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["response"]["body"] == "thank you"

    def test_respond_disabled_400(self, settings, api_client, user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.can_mod", "allow_response": False}}
        }
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        api_client.force_authenticate(user=owner_user)
        resp = api_client.post(
            f"/reviews/api/reviews/{review.id}/response",
            {"body": "no"},
            format="json",
        )
        assert resp.status_code == 400

    def test_requires_auth(self, settings, api_client):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        resp = api_client.get(
            "/reviews/api/reviews", {"target_type": "seller", "target_key": "s1"}
        )
        assert resp.status_code in (401, 403)
