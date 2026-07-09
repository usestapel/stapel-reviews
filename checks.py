"""Django system checks for stapel-reviews configuration.

Policy (docs/library-standard.md §3.7): E-level for configuration the service
cannot run with; W-level for entries that only degrade lazily.

- TARGET_TYPES not a dict, or a non-None policy that is not a dict, or a policy
  with an invalid ``moderation`` value -> E (create/moderate would crash or
  behave surprisingly on the affected type).
- MODERATION_DEFAULT not in {pre, post} -> E (every type with no override would
  resolve to an invalid moderation mode).
"""
from django.core import checks

_VALID_MODERATION = {"pre", "post"}


@checks.register(checks.Tags.compatibility)
def check_moderation_default(app_configs, **kwargs):
    from .conf import reviews_settings

    value = reviews_settings.MODERATION_DEFAULT
    if value not in _VALID_MODERATION:
        return [
            checks.Error(
                "STAPEL_REVIEWS['MODERATION_DEFAULT'] must be 'pre' or 'post', "
                f"got {value!r}.",
                id="stapel_reviews.E001",
            )
        ]
    return []


@checks.register(checks.Tags.compatibility)
def check_target_types(app_configs, **kwargs):
    from .conf import reviews_settings

    types = reviews_settings.TARGET_TYPES
    if not isinstance(types, dict):
        return [
            checks.Error(
                "STAPEL_REVIEWS['TARGET_TYPES'] must be a dict of "
                "{type_name: policy | None}.",
                id="stapel_reviews.E002",
            )
        ]
    errors = []
    for name, policy in types.items():
        if policy is None:
            continue  # a removal marker
        if not isinstance(policy, dict):
            errors.append(
                checks.Error(
                    f"STAPEL_REVIEWS['TARGET_TYPES'][{name!r}] policy must be a "
                    "dict or None.",
                    id="stapel_reviews.E003",
                )
            )
            continue
        moderation = policy.get("moderation")
        if moderation is not None and moderation not in _VALID_MODERATION:
            errors.append(
                checks.Error(
                    f"STAPEL_REVIEWS['TARGET_TYPES'][{name!r}]['moderation'] must "
                    f"be 'pre' or 'post', got {moderation!r}.",
                    id="stapel_reviews.E004",
                )
            )
    return errors
