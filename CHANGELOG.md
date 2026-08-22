# Changelog

All notable changes to stapel-reviews are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 semver: **minor = breaking**, patch = compatible.

## [0.3.0] — 2026-08-22

### Changed

- **`GET /reviews` and `GET /reviews/aggregate` are now anonymously
  readable** (storefront F5 verdict, darom-storefront-design.md §13.8 note
  2): both views were gated on bare `IsAuthenticated`, so a guest on a
  public listing page got a 401 on the review list and the aggregate alike
  — indistinguishable from "this seller has no reviews," which is exactly
  the state a not-yet-registered visitor should never be shown. Both
  endpoints only ever return published reviews to a non-moderator, so
  nothing becomes visible to a guest that a moderator would need withheld.
  `ReviewListCreateView` (list + create in one class) now gates on DRF's
  `IsAuthenticatedOrReadOnly` — mirrors `stapel-listings`' `ListingViewSet`,
  the fleet's other single-class read-open/write-authenticated view — so
  `POST` (create) is unaffected and still needs a real identity.
  `AggregateView` (GET-only) now gates on `AllowAny`, mirroring
  `stapel-search`'s public endpoints. Both declare
  `stapel_anonymous_access = ANONYMOUS_ALLOWED` per the core adoption
  check's canon (silence reads as unintended, not "guests are fine here").
  `POST .../moderate` and `POST .../response` are untouched and still
  `IsAuthenticated`-only. Both anonymous reads are now throttled from the
  module's own settings namespace (`STAPEL_REVIEWS["LIST_THROTTLE"]`
  default `120/min`, `STAPEL_REVIEWS["AGGREGATE_THROTTLE"]` default
  `300/min`) — a library does not own the project's
  `DEFAULT_THROTTLE_RATES`, matching `stapel-search`'s
  `QUERY_THROTTLE`/`SUGGEST_THROTTLE` convention. **Minor bump because this
  is a permission-surface change**, even though it only widens what a
  guest may read.

### Fixed

- **`docs/schema.json` now declares `GET /reviews`'s real response
  envelope and its anchor-pagination query params.** The endpoint has
  always returned `AnchorPagination`'s envelope
  (`{items, next_anchor, prev_anchor, has_next, has_prev, count}`,
  `views.py:176-180`) and always read `anchor`/`limit`/`direction` off the
  query string (`ReviewAnchorPagination`), but the schema declared a bare
  `ReviewResponse[]` and said nothing about the three pagination params —
  `ReviewListCreateView` is a bare `APIView`, so drf-spectacular's paginator
  introspection (which only fires for `GenericAPIView.pagination_class`)
  never ran (darom-storefront-design.md §13.8 note 3). A generated client
  had no declared way to page past the first window, and its response type
  was simply wrong. Fixed by hand-declaring both: a new
  `ReviewPageSerializer` (serializers.py) for the envelope and
  `ANCHOR_QUERY_PARAMETERS` (views.py) for `anchor`/`limit`/`direction`,
  the same pattern `TARGET_QUERY_PARAMETERS`/`INCLUDE_QUERY_PARAMETER`
  already used for `target_type`/`target_key`/`include` (0.2.2). No
  runtime behavior change — view and pagination code are untouched; only
  the emitted contract now matches what the endpoint has always done.

## [0.2.2] — 2026-08-22

### Fixed

- **`docs/schema.json` now declares `target_type`/`target_key` (both
  required) and `include` as query parameters on `GET /reviews` and
  `GET /reviews/aggregate`** (`target_type`/`target_key` only — `include` is
  list-only). These were always read from the query string
  (`views.py:_target_params`, `views.py:118-132`) but never declared via
  `extend_schema`, so a pure codegen client (darom-storefront-design.md §1.8,
  A2) produced a list/aggregate call with no way to pass them — an unusable
  client. `POST /reviews` (create) was unaffected: its body fields already
  came from `ReviewCreateRequestSerializer`/`ReviewCreateRequest`, which
  always carried `target_type`/`target_key`. No runtime behavior change —
  view code is untouched; only the emitted contract gained the parameters.

## [0.2.1] — 2026-08-21

### Changed

- **`services.ReviewNotFound` now subclasses `LookupError`** instead of bare
  `Exception`. `stapel-moderation`'s integration found the two upstreams of
  the `*.moderation_content` comm-Function family disagree on how they
  report "target not found": `stapel-listings` raises `LookupError`,
  `services.moderation_content` here raised a bare `ReviewNotFound`. A
  consumer wanting to catch "not found" across both targets could not catch
  one exception class (stapel-moderation-design.md §23.2). This is the
  honest fix named there: `ReviewNotFound` now *is* a `LookupError`, so
  `except LookupError` catches it from either module, while `except
  ReviewNotFound` still works unchanged for callers that want the specific
  type. No in-repo `except` site catches `ReviewNotFound` or `LookupError`,
  so this is a pure MRO widening with no behavior change here.

## [0.2.0] — 2026-08-21

### Added — the moderation seam (stapel-moderation upstream, §16.2)

`stapel-moderation` (tasks/stapel-moderation-design.md) is a target-generic
moderation queue whose "mandatory upstreams" section names three things this
module owed it. All three ship here; no producer of `moderation.completed`
lives in this package, so the consumer is inert until a moderation module is
installed beside it.

- **`moderation.completed` consumer** (`actions.py`). A verdict whose
  `target_type` matches the new `MODERATION_TARGET_TYPE` knob (default
  `"review"`) carries a review id in `target_key`: `rejected` hides the review,
  `approved` publishes it, and `needs_review` / `dismissed` deliberately move
  nothing — the first says the automation abstained, the second speaks about a
  report rather than about content. Verdicts about listings, profiles or chat
  are dropped silently; a shared topic is not a delivery error.

  Idempotency is **by state**, not by event id: a redelivered verdict finds the
  review already in the decided status and returns without a write and without
  a fact. Failures that would repeat on every redelivery — a review we do not
  have, a malformed key, a decision word outside the contract — are logged and
  swallowed rather than poisoning the subscription forever.

  `schemas/consumes/moderation.completed.json` requires only what the consumer
  reads (`target_type`, `target_key`, `decision`) and stays open to extra
  fields. A consumer schema that demanded everything the emitter happens to
  send is precisely the `additionalProperties: false` trap that made the same
  integration physically impossible for `stapel-listings`.

- **`services.SYSTEM_ACTOR`** — a named actor that gets past the fail-closed
  `can_moderate` gate, and the only thing that does. That gate is right for
  humans and exactly wrong for the platform's own decision: authorization
  already happened in the moderation module, which authorized a moderator,
  recorded an append-only Verdict and only then emitted the fact — while a
  target type with no `can_moderate` callback denies everyone by design. The
  bypass is recognized by object identity (nothing can imitate it by growing an
  `is_system` attribute), it skips `resolve_policy` too (a takedown must still
  work after the host de-registers a target type), and it buys visibility only:
  `respond()` refuses the system actor, because a Response is authored by a
  real user row.

- **`reviews.moderation_content`** Function — `{review_id}` -> `{text, title,
  language, media, author_id, url, rating, status, target_type, target_key,
  created_at}`. Identifiers travel on the bus, content is fetched when it is
  looked at, so a moderator opening a case hours later reads the review as it
  is now. A review has no title, declared language, media or public per-review
  URL: those four come back empty rather than invented.

### Added — the two aggregate Functions `stapel-shop` was already declared against

`stapel-shop/projections.py` has named `reviews.aggregates_by_keys` and
`reviews.aggregates_export` as its rating Projection's `live_query` and
`source_of_truth` since 0.1.0, with a docstring recording that neither existed
— local-mode reads raised `FunctionNotRegistered` and remote-mode `rebuild`
failed loudly. Both exist now, in the shapes
`stapel_core.comm.projections` actually calls:

- **`reviews.aggregates_by_keys`** — `{keys, target_type?}` ->
  `{key: {avg, count}}`, one query for many targets. Keys with no published
  review are **absent** from the answer rather than present with zeros, per the
  `live_query` contract ("absent keys are simply missing"), which is also the
  difference between "nobody rated it" and "everyone rated it 0". The core
  primitive sends only `keys`; `target_type` narrows the lookup for callers
  that know it and matters when two target types share a key.
- **`reviews.aggregates_export`** — `{cursor?, limit?}` ->
  `{rows, cursor, total}`, the cursor-paged snapshot `rebuild()` and
  `drift_check()` read. Each row carries `target_key`, `target_type`, `avg`,
  `count` and a `seq` in **unix milliseconds** — an Event's clock — so a live
  fact arriving mid-rebuild outranks the snapshot row instead of racing it.
  Paging is keyset over `(target_type, target_key)`, stable under concurrent
  writes in a way an OFFSET is not; `total` is reported on the first page only.

### Added

- `MODERATION_TARGET_TYPE` (default `"review"`) — which verdict `target_type`
  means "this is about a review". A knob rather than a literal in the handler,
  so a composite that spells the type differently configures it instead of
  forking the consumer.

### Fixed

- `docs/errors.json` regenerated: stapel-core 0.31.0 registers an error key
  this module's committed artifact predates, so the drift gate was red on
  `main` for reasons unrelated to any change here.
- The empty `[Unreleased]` heading that had been sitting at the top of this
  file since 0.1.0 is gone — it never accumulated an entry, and a permanently
  empty section teaches a reader to skip the top of the changelog.

## [0.1.9] — 2026-08-15

### Changed — `stapel-core` floor raised to 0.26.0

`docs/errors.json` carries an `owner` per entry, and only stapel-core 0.26.0
emits it. The floor lagged behind, so a consumer resolving an older core
regenerated an artifact without `owner` and the drift gate went red — the
field was declared but never required. The floor now matches the artifact
that is committed.

## [0.1.8] - 2026-08-02

Packaging/docs catch-up, no behavior change:

- CI tests the Python the stand actually runs.
- Contract documents ship in the wheel (`package-data`) (#184).
- Badge canon + Python 3.14 classifier.
- `docs/llms.txt` — the fifth contract artifact (badge-canon §3), emitted
  by `stapel_tools.llms_txt` and checked by the `make contract-check`
  drift gate.

## [0.1.7] - 2026-07-22

Backfilled entry — this release shipped as tag `v0.1.7` but was never written
down here, leaving a hole between 0.1.8 and 0.1.6.

### Changed
- `stapel-core` ceiling widened `<0.13` → `<1.0` (fleet-wide ecosystem
  cap-widen), so a consumer resolving a modern core is not blocked by this
  module's upper bound.

## [0.1.6] - 2026-07-17

Fix-up #2: 0.1.5's regen still baked the old version into
`docs/capabilities.json` (`make contract` ran before the version bump
landed). Re-ran with 0.1.6 already in `pyproject.toml`; verified match,
suite green.

## [0.1.5] - 2026-07-17

Fix-up: 0.1.4's CI/publish failed on contract drift — `docs/capabilities.json`
embeds the package version and wasn't regenerated for the 0.1.4 bump.
Regenerated via `make contract`; no other diff.

## [0.1.4] - 2026-07-17

Fleet follow-up to stapel-core 0.12.0 (legacy shim sweep). No source
changes needed. Full suite green against core 0.12.0.

### Changed
- `stapel-core` dependency ceiling `<0.12` → `<0.13`.

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
