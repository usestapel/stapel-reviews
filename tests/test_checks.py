"""System checks for STAPEL_REVIEWS configuration."""
from stapel_reviews.checks import (
    check_moderation_default,
    check_target_types,
)


def test_valid_config_passes(settings):
    settings.STAPEL_REVIEWS = {
        "MODERATION_DEFAULT": "post",
        "TARGET_TYPES": {"seller": {"moderation": "pre"}, "listing": None},
    }
    assert check_moderation_default(None) == []
    assert check_target_types(None) == []


def test_bad_moderation_default(settings):
    settings.STAPEL_REVIEWS = {"MODERATION_DEFAULT": "sometimes"}
    errors = check_moderation_default(None)
    assert [e.id for e in errors] == ["stapel_reviews.E001"]


def test_target_types_not_a_dict(settings):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": ["seller"]}
    errors = check_target_types(None)
    assert [e.id for e in errors] == ["stapel_reviews.E002"]


def test_policy_not_a_dict(settings):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": "yes"}}
    errors = check_target_types(None)
    assert "stapel_reviews.E003" in [e.id for e in errors]


def test_policy_bad_moderation(settings):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {"moderation": "later"}}}
    errors = check_target_types(None)
    assert "stapel_reviews.E004" in [e.id for e in errors]
