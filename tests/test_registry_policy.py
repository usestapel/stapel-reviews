"""Target-type registry: merge-over-builtins, policy resolution, per-type
differences, unknown types."""
import pytest

from stapel_reviews.registry import (
    BUILTIN_TARGET_TYPES,
    UnknownTargetType,
    get_target_types,
    register_target_type,
    resolve_policy,
)


def test_builtins_are_empty():
    """The module ships knowing NO target types — the host supplies them."""
    assert BUILTIN_TARGET_TYPES == {}
    assert get_target_types() == {}


def test_settings_register_types(settings):
    settings.STAPEL_REVIEWS = {
        "TARGET_TYPES": {"seller": {"one_per_author": True}, "listing": {}}
    }
    types = get_target_types()
    assert set(types) == {"seller", "listing"}


def test_runtime_registration_merges_over_settings(settings):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {"moderation": "post"}}}
    register_target_type("driver", {"moderation": "pre"})
    assert set(get_target_types()) == {"seller", "driver"}


def test_runtime_none_removes_a_type(settings):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
    register_target_type("seller", None)
    assert "seller" not in get_target_types()


def test_resolve_fills_module_defaults(settings):
    settings.STAPEL_REVIEWS = {
        "MODERATION_DEFAULT": "post",
        "RESPONSES": True,
        "TARGET_TYPES": {"seller": {}},
    }
    policy = resolve_policy("seller")
    assert policy["moderation"] == "post"
    assert policy["allow_response"] is True
    assert policy["one_per_author"] is False
    assert policy["can_review"] is None
    assert policy["can_moderate"] is None


def test_per_type_overrides_defaults(settings):
    settings.STAPEL_REVIEWS = {
        "MODERATION_DEFAULT": "post",
        "RESPONSES": True,
        "TARGET_TYPES": {
            "seller": {"moderation": "pre", "allow_response": False, "one_per_author": True},
            "listing": {},
        },
    }
    seller = resolve_policy("seller")
    listing = resolve_policy("listing")
    # Per-type policy differs — a review on a seller ≠ a review on a listing.
    assert seller["moderation"] == "pre"
    assert seller["allow_response"] is False
    assert seller["one_per_author"] is True
    assert listing["moderation"] == "post"
    assert listing["allow_response"] is True
    assert listing["one_per_author"] is False


def test_unknown_type_raises(settings):
    settings.STAPEL_REVIEWS = {"TARGET_TYPES": {"seller": {}}}
    with pytest.raises(UnknownTargetType):
        resolve_policy("nope")
