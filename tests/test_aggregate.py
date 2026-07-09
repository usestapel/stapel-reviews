"""The module-owned aggregate: avg/count over published reviews only."""
import pytest
from stapel_core.comm import register_function

from stapel_reviews import services


@pytest.mark.django_db
class TestAggregate:
    def test_empty_target(self, settings):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        agg = services.aggregate("seller", "s1")
        assert agg.count == 0
        assert agg.avg == 0.0

    def test_avg_and_count(self, settings, user, other_user, owner_user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        for u, r in ((user, 5), (other_user, 4), (owner_user, 3)):
            services.create_review(
                target_type="seller", target_key="s1", author=u, rating=r
            )
        agg = services.aggregate("seller", "s1")
        assert agg.count == 3
        assert agg.avg == 4.0

    def test_hidden_excluded(self, settings, user, other_user, owner_user):
        register_function("fake.can_mod", lambda p: True)
        settings.STAPEL_REVIEWS = {
            "TARGET_TYPES": {"seller": {"can_moderate": "fake.can_mod"}}
        }
        r1 = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        services.create_review(
            target_type="seller", target_key="s1", author=other_user, rating=1
        )
        services.moderate_review(r1, actor=owner_user, action="hide")
        agg = services.aggregate("seller", "s1")
        # Only the surviving 1-star review counts.
        assert agg.count == 1
        assert agg.avg == 1.0

    def test_pending_excluded(self, settings, user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {"moderation": "pre"}}}
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        assert services.aggregate("seller", "s1").count == 0

    def test_scoped_to_target(self, settings, user, other_user):
        settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
        services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        services.create_review(
            target_type="seller", target_key="s2", author=other_user, rating=1
        )
        assert services.aggregate("seller", "s1").count == 1
        assert services.aggregate("seller", "s2").count == 1
