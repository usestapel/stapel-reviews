"""stapel-reviews capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli


def main(argv=None):
    from stapel_reviews._codegen import _configure

    _configure()
    from stapel_reviews.conf import DEFAULTS
    from stapel_reviews.urls import GATE_REGISTRY

    # Two CTO-facing config axes: MODERATION_DEFAULT (pre|post) and RESPONSES
    # (bool). Both are behavioral, not gating — they change how the review
    # engine behaves, not which endpoints exist, so gates.operations stays
    # empty and the semantics live in docs/capabilities.meta.json. TARGET_TYPES
    # is the flagship merge-registry extension seam (curated as an extension
    # point, not an axis); RATING_MIN/RATING_MAX are tuning knobs.
    axes = {"MODERATION_DEFAULT", "RESPONSES"}
    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/reviews",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in axes,
        axis_group=axis_group_rules(
            exact={
                "MODERATION_DEFAULT": "reviews.moderation",
                "RESPONSES": "reviews.responses",
            }
        ),
        prog="stapel-reviews-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
