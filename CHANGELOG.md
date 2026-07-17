# Changelog

All notable changes to stapel-reviews are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 semver: **minor = breaking**, patch = compatible.

## [Unreleased]

## [0.1.3] — 2026-07-17

### Fixed
- Legacy-doc sweep: stale pre-v1 `/reviews/api/` prefix references corrected to
  the canonical `/reviews/api/v1/` in `_codegen.py`, `codegen_urls.py`,
  `MODULE.md` and the `Makefile` header (docs/comments only — the URL surface
  has been v1-only since 0.1.1; no code or wire changes, nothing removed).

## [0.1.2] — 2026-07-17

### Changed
- `stapel-core` ceiling raised `>=0.10,<0.11` → `>=0.10,<0.12` (core 0.11
  fleet re-pin: default bus, nav, config-checks, error params/language —
  additive for modules). Contract artifacts regenerated (version bump);
  suite green.

## [0.1.1] — 2026-07-16

### Changed
- **v1 canon sweep §60** (api-versioning.md §2, §6): URL set moved to
  `urls_v1.py`; the new root `urls.py` mounts it under `api/v1/` (the `api/`
  segment historically lives inside this package, so the version slots in
  right after it, per canon). Host mount `reviews/` unchanged: endpoints now
  serve at `/reviews/api/v1/...`; bare `/reviews/api/...` no longer exists
  (sweep lands before the §3 API00x gates are enabled).
- Contract artifacts regenerated (`make contract`): `/v1/` in schema paths.
- `_capabilities.py` canonical_prefix → `/reviews/api/v1`.
- Lint hygiene to a clean `stapel-verify`: explicit `# noqa: R007` on
  pre-existing findings.

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
