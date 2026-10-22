"""one_per_author holds under a concurrent create (RV-2)."""
import pytest
from django.db import IntegrityError

from stapel_reviews import services
from stapel_reviews.models import Review


def _types(settings, **policies):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": policies}


@pytest.mark.django_db(transaction=True)
class TestConcurrentCreate:
    def test_a_create_that_passed_the_check_is_refused_by_the_database(
        self, settings, user, monkeypatch
    ):
        _types(settings, seller={"one_per_author": True})
        # The concurrent request: its row lands after our existence check
        # answered "none yet".
        real_has_review = services._has_review
        calls = {"n": 0}

        def check_then_race(target_type, target_key, author):
            calls["n"] += 1
            answer = real_has_review(target_type, target_key, author)
            if calls["n"] == 1:
                Review.objects.create(
                    target_type=target_type,
                    target_key=target_key,
                    author=author,
                    rating=5,
                    unique_per_author=True,
                )
            return answer

        monkeypatch.setattr(services, "_has_review", check_then_race)
        with pytest.raises(services.DuplicateReview):
            services.create_review(
                target_type="seller", target_key="s1", author=user, rating=1
            )
        assert Review.objects.filter(target_key="s1").count() == 1

    def test_the_api_answers_400_not_500(self, settings, auth_client, user, monkeypatch):
        _types(settings, seller={"one_per_author": True})
        # Both requests pass the read before either inserts; the re-check
        # after the refused insert sees the winner, as it would in production.
        real_has_review = services._has_review
        calls = {"n": 0}

        def stale_read(*args):
            calls["n"] += 1
            return False if calls["n"] <= 2 else real_has_review(*args)

        monkeypatch.setattr(services, "_has_review", stale_read)
        body = {"target_type": "seller", "target_key": "s1", "rating": 4}
        assert auth_client.post("/reviews/api/v1/reviews", body, format="json").status_code == 201
        resp = auth_client.post("/reviews/api/v1/reviews", body, format="json")
        assert resp.status_code == 400
        assert resp.data["localizable_error"] == "error.400.reviews_duplicate_review"


@pytest.mark.django_db
class TestConstraintScope:
    def test_types_without_the_policy_keep_many_reviews(self, settings, user):
        _types(settings, listing={})
        services.create_review(target_type="listing", target_key="l1", author=user, rating=5)
        services.create_review(target_type="listing", target_key="l1", author=user, rating=4)
        assert Review.objects.filter(target_key="l1").count() == 2

    def test_rows_written_under_the_policy_carry_the_flag(self, settings, user):
        _types(settings, seller={"one_per_author": True})
        review = services.create_review(
            target_type="seller", target_key="s1", author=user, rating=5
        )
        assert review.unique_per_author is True

    def test_two_flagged_rows_cannot_coexist(self, user):
        Review.objects.create(
            target_type="seller", target_key="s1", author=user, rating=5,
            unique_per_author=True,
        )
        with pytest.raises(IntegrityError):
            Review.objects.create(
                target_type="seller", target_key="s1", author=user, rating=4,
                unique_per_author=True,
            )
