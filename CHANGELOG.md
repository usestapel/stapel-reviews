# Changelog

All notable changes to stapel-reviews are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 semver: **minor = breaking**, patch = compatible.

## [Unreleased]

## [0.1.0] — 2026-07-10

### Added — target-generic review engine (initial release)

First cut of a reusable, domain-blind reviews module.

- **Review / Response** models over an opaque target (`target_type` +
  `target_key`, no FK to any host model). Rating 1..5 (configurable bounds),
  status `pending/published/hidden`.
- **Target-type registry** (`STAPEL_REVIEWS["TARGET_TYPES"]`, merged over empty
  built-ins; `register_target_type()` / `reset_target_types()` runtime API).
- **Per-type policy**: `can_review` / `can_moderate` comm-callback names,
  pre/post moderation, one-review-per-author, `allow_response`. The module
  calls host callbacks by comm name and imports no host model.
- **Module-owned aggregate** (avg/count over published reviews) exposed as the
  `reviews.aggregate` comm Function, and emitted on every visibility change via
  `reviews.review.published` / `reviews.review.hidden` (carrying the fresh
  aggregate) for host-side rating projections (§10).
- **API**: create review, list by target (anchor-paginated, published-only for
  non-moderators), aggregate by target, moderate (hide/publish), owner respond.
  DTO + serializer seams + OpenAPI.
- **Config axes**: `MODERATION_DEFAULT` (post/pre), `RESPONSES` (bool).
- System checks for `MODERATION_DEFAULT` / `TARGET_TYPES`, GDPR erasure on
  `user.deleted`, contract quartet emission (`make contract`), migration-lint
  clean.
