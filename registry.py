"""Target-type registry — the flagship extension seam of stapel-reviews.

The module is target-generic: it ships knowing **no** target types
(``BUILTIN_TARGET_TYPES`` is empty). A host declares what may be reviewed by
merging its types over the built-ins via ``STAPEL_REVIEWS["TARGET_TYPES"]``
(and/or the runtime ``register_target_type()`` API) — the same open-registry
idiom as ``calendar.PRESETS`` / ``notifications.TYPES``.

A *policy* is a plain dict per type:

    {
        "can_review":     "comm.function.name" | None,   # author eligibility
        "can_moderate":   "comm.function.name" | None,   # moderate + respond
        "moderation":     "pre" | "post",                # else MODERATION_DEFAULT
        "one_per_author": bool,                          # default False
        "allow_response": bool,                          # else RESPONSES
    }

The two ``can_*`` entries are **comm Function names** — the module *calls* the
host (``stapel_core.comm.call(name, {...})``) and reads a boolean out of the
result. It never imports a host model: authorization and ownership are the
host's to answer, off an opaque ``(target_type, target_key)`` handle. A policy
with ``can_review=None`` means "anyone authenticated may review"; a policy with
``can_moderate=None`` means "no one may moderate or respond via the API"
(fail-closed — an unset moderator gate never silently opens).
"""
from __future__ import annotations

from typing import Optional

#: Built-ins are EMPTY — the module knows no targets. A host supplies them.
BUILTIN_TARGET_TYPES: dict[str, Optional[dict]] = {}

#: Runtime overrides (register_target_type / reset_target_types). Kept separate
#: from the settings layer so tests can reset without touching Django settings.
_runtime_target_types: dict[str, Optional[dict]] = {}


class UnknownTargetType(Exception):
    """Raised when a target_type is not registered by the host."""


def register_target_type(name: str, policy: Optional[dict]) -> None:
    """Register/override a target type at runtime. ``policy=None`` removes a
    type that a lower layer (built-ins / settings) provided."""
    _runtime_target_types[name] = policy


def reset_target_types() -> None:
    """Tests only: drop runtime target-type overrides."""
    _runtime_target_types.clear()


def get_target_types() -> dict[str, dict]:
    """Effective registry: built-ins <- settings TARGET_TYPES <- runtime, with
    ``None`` removing a key. Only live (non-None) entries are returned."""
    from .conf import reviews_settings

    merged: dict[str, Optional[dict]] = dict(BUILTIN_TARGET_TYPES)
    for source in (reviews_settings.TARGET_TYPES or {}, _runtime_target_types):
        for name, policy in source.items():
            merged[name] = policy
    return {name: policy for name, policy in merged.items() if policy is not None}


def resolve_policy(target_type: str) -> dict:
    """Return the fully-resolved policy for ``target_type`` (module-level
    defaults filled in), or raise :class:`UnknownTargetType`.

    Every key the services rely on is guaranteed present on the returned dict:
    ``can_review``, ``can_moderate``, ``moderation``, ``one_per_author``,
    ``allow_response``.
    """
    from .conf import reviews_settings

    types = get_target_types()
    if target_type not in types:
        raise UnknownTargetType(target_type)
    raw = types[target_type] or {}
    return {
        "can_review": raw.get("can_review"),
        "can_moderate": raw.get("can_moderate"),
        "moderation": raw.get("moderation", reviews_settings.MODERATION_DEFAULT),
        "one_per_author": bool(raw.get("one_per_author", False)),
        "allow_response": bool(raw.get("allow_response", reviews_settings.RESPONSES)),
    }


def _read_bool(result) -> bool:
    """Normalize a callback result to a boolean. A host callback may return a
    bare bool or an envelope ``{"allowed": bool}`` (the richer form leaves room
    for a reason later); anything else is treated as its truthiness."""
    if isinstance(result, dict) and "allowed" in result:
        return bool(result["allowed"])
    return bool(result)


def check_can_review(policy: dict, *, author_id, target_type: str, target_key: str) -> bool:
    """Ask the type's ``can_review`` callback whether ``author_id`` may review
    the target. No callback (``None``) means unrestricted (any authenticated
    author). The call is synchronous and its failure is **not** swallowed into
    a fail-open default — a broken gate blocks the write."""
    name = policy.get("can_review")
    if not name:
        return True
    from stapel_core.comm import call

    return _read_bool(
        call(
            name,
            {
                "author_id": str(author_id),
                "target_type": target_type,
                "target_key": target_key,
            },
        )
    )


def check_can_moderate(policy: dict, *, actor_id, target_type: str, target_key: str) -> bool:
    """Ask the type's ``can_moderate`` callback whether ``actor_id`` may
    moderate (hide/publish) or respond to reviews of the target. No callback
    (``None``) is **fail-closed** — moderation is denied rather than open."""
    name = policy.get("can_moderate")
    if not name:
        return False
    from stapel_core.comm import call

    return _read_bool(
        call(
            name,
            {
                "actor_id": str(actor_id),
                "target_type": target_type,
                "target_key": target_key,
            },
        )
    )
