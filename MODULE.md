# stapel-reviews — MODULE.md

> Agent-facing map of this module: what it provides, where to extend it
> without forking, and what not to do. Kept in the same PR as any change
> to a seam. See also README.md and CHANGELOG.md.

## What this module provides

- **Review / Response** — the generic review core. `Review` carries an opaque
  target (`target_type` + `target_key`, **no FK to any host model**), an
  `author`, a `rating` (1..5 by default), a `body` and a `status`
  (`pending/published/hidden`). `Response` is the target owner's single reply
  to a review (OneToOne).
- **Target-type registry** — the module ships knowing **no** target types
  (`registry.BUILTIN_TARGET_TYPES == {}`). A host declares what may be reviewed
  by merging its types over the built-ins via `STAPEL_REVIEWS["TARGET_TYPES"]`
  (and/or the runtime `register_target_type()` API). This is the flagship seam.
- **Per-type policy** — each type's policy decides: who may review
  (`can_review` comm callback), pre/post moderation, one-review-per-author,
  whether owner responses are allowed (`allow_response`), and who may
  moderate/respond (`can_moderate` comm callback). A review on a *seller* is not
  a review on a *listing* — the policy is where that difference lives.
- **Module-owned aggregate + projection emits** — the module owns `avg`/`count`
  over *published* reviews per `(target_type, target_key)`, and on every
  visibility change emits a generic FACT (`reviews.review.published` /
  `reviews.review.hidden`) carrying the fresh aggregate, so a host catalog can
  maintain its OWN rating projection (§10) without ever calling back. The same
  aggregate is exposed synchronously as the `reviews.aggregate` comm Function.
- **Owner-wide aggregate (optional)** — a target type may also declare
  `owner_key_for`, the host's answer to "who owns this target". It is asked at
  write time and denormalised onto `Review.owner_key`, which is what lets the
  module answer "what is this *seller* rated, over everything they own"
  (`reviews.aggregates_by_owner_keys`) while still knowing nothing about
  sellers or listings. Registering none leaves the column empty and changes
  nothing (seam #5a).
- **API** — create review, list by target (anchor-paginated, published-only for
  non-moderators), aggregate by target, batch aggregate by owner, moderate
  (hide/publish), respond. DTO + serializer seams + OpenAPI (drf-spectacular).

**Why target-generic.** "We don't know what will be reviewed." Folding reviews
into a catalog couples them to one domain (a SellerProfile) and creates
recompute loops. Instead the target is opaque and the domain authority lives
in the host, reached by comm name — the module is a reusable review engine.

## Extension points (fork-free)

### 1. Target-type registry + policy — `STAPEL_REVIEWS["TARGET_TYPES"]` (open registry, MERGE)

`{type_name: policy}` merged OVER the empty built-ins (`None` removes a type),
plus the runtime `register_target_type()` API (`reset_target_types()` for
tests). A policy is a plain dict:

```python
{
    "can_review":     "comm.function.name" | None,   # author eligibility
    "can_moderate":   "comm.function.name" | None,   # moderate + respond
    "owner_key_for":  callable | "comm.function.name" | None,  # who owns the target
    "moderation":     "pre" | "post",                # else MODERATION_DEFAULT
    "one_per_author": bool,                          # default False
    "allow_response": bool,                          # else RESPONSES
}
```

`registry.resolve_policy(target_type)` fills the module-level defaults and
raises `UnknownTargetType` for an unregistered type (a 400 at the API).

### 2. Domain callbacks — `can_review` / `can_moderate` (host comm Functions)

The two `can_*` policy entries are **comm Function names the host registers**.
The module *calls* the host and reads a boolean out:

- `can_review(author_id, target_type, target_key) -> bool | {"allowed": bool}`
  — no callback means **unrestricted** (any authenticated author).
- `can_moderate(actor_id, target_type, target_key) -> bool | {"allowed": bool}`
  — no callback is **fail-closed** (moderation/response denied).

The module never imports a host model — ownership and eligibility are the
host's to answer off the opaque `(target_type, target_key)` handle. The call is
synchronous and its failure is **not** swallowed into a fail-open default.

### 3. Aggregate projection — `reviews.review.published` / `reviews.review.hidden` (comm emits)

On every visibility change the module recomputes the target aggregate and emits
the matching fact carrying `{aggregate: {avg, count}}`, keyed
`"{target_type}:{target_key}"` so a host projects per target. The host catalog
subscribes and maintains its own `avg_rating` projection — the module never
reaches into the catalog, the catalog never recomputes from raw reviews. This
is the §10 projection pattern, made first-class here. Schemas:
`schemas/emits/reviews.review.{published,hidden}.json`.

Caveat: with `OUTBOX_ENABLED=False` (synchronous in-process delivery, the test
default) the emit fires *inside* the status-change transaction; with the outbox
enabled, delivery is transactional (the fact and the state it describes commit
together). Idempotent re-moderation (publishing an already-published review) is
a no-op and emits nothing.

### 4. Aggregate Function — `reviews.aggregate` (comm Function)

`{target_type, target_key} -> {avg, count}` over published reviews — a
synchronous read primitive other services can call by name. The host projection
is a cache of exactly this. Schema: `schemas/functions/reviews.aggregate.json`.

### 5. Batch aggregate Functions — the two halves of a host `Projection`

A host rating projection is declared against two owner Functions, and both are
here:

- `reviews.aggregates_by_keys` — `{keys, target_type?} -> {key: {avg, count}}`.
  The **`live_query`** half: in local mode `stapel_core.comm.projections.read()`
  calls it and hands the answer straight to the caller, so the host keeps no
  table. Keys nobody has reviewed are *absent* from the answer, never zeroed.
  The core primitive sends only `keys`; `target_type` exists for callers that
  know it and matters when two target types share a key.
- `reviews.aggregates_export` — `{cursor?, limit?} -> {rows, cursor, total}`.
  The **`source_of_truth`** half, read by `rebuild()` and `drift_check()`. Rows
  carry `target_key`, `target_type`, `avg`, `count` and a `seq` in **unix
  milliseconds** — an Event's clock, so a live fact arriving mid-rebuild
  outranks the snapshot row. Paging is keyset over `(target_type, target_key)`;
  `total` is reported on the first page only.

Schemas: `schemas/functions/reviews.aggregates_{by_keys,export}.json`.

### 5a. Owner-wide aggregate — `owner_key_for` + `reviews.aggregates_by_owner_keys`

A marketplace needs the rating of a **seller**, not only of each listing, and
the module must not learn what a listing is to produce it. The whole mechanism
is one optional resolver plus one denormalised column:

- **`owner_key_for`** in a type policy — a callable
  `owner_key_for(target_key) -> str | None`, or a comm Function name called
  with `{target_type, target_key}` (answering a bare string or
  `{"owner_key": ...}`) for a policy that must stay JSON-shaped. Asked **once,
  at write time**, by `registry.resolve_owner_key`; a resolver that raises
  blocks the write rather than stamping nothing, because an unstamped review is
  a seller rating quietly missing reviews.
- **`Review.owner_key`** (CharField 255, blank, indexed) — the answer, stored
  beside the review. Empty is the norm: a type that registers no resolver
  stamps nothing and every other behaviour is identical.
- **`reviews.aggregates_by_owner_keys`** — `{owner_keys, target_type?} ->
  {owner_key: {avg, count}}` over published reviews, same rounding as
  `reviews.aggregate`, unreviewed owners absent, reviews with no owner key
  excluded rather than pooled under `""`. Exposed publicly as
  `POST /reviews/api/v1/reviews/aggregates/by-owner` (≤ 100 owner keys per request;
  over that is `error.400.reviews_too_many_owner_keys`).
- **`services.list_reviews_by_owner`** — the ROWS along the same axis, for a
  seller page's reviews tab: every review of everything an owner owns, newest
  first, `target_type` narrowing optional, an empty owner key matching nothing.
  Exposed on the list endpoint as `GET /reviews/api/v1/reviews?owner_key=…`,
  used INSTEAD of `target_type` + `target_key` (naming both is
  `error.400.reviews_ambiguous_addressing`). Index: `rev_owner_created`
  (`owner_key`, `-created_at`), the ordering the anchor cursor pages on.
- **`manage.py reviews_backfill_owner_keys`** — the pass over rows written
  before a resolver existed. Idempotent (candidates are exactly the rows whose
  owner key is still empty), keyset-paged, one resolver call per distinct
  target, `--target-type` / `--batch-size` / `--limit` / `--dry-run`. Here a
  resolver that raises is counted and stepped over, not fatal.

Schema: `schemas/functions/reviews.aggregates_by_owner_keys.json`.

### 6. Moderation seam — `moderation.completed` (consume) + `reviews.moderation_content`

An external moderation module owns the *decision*; this module owns *applying*
it. Its verdict arrives as `moderation.completed`; when `target_type` matches
`MODERATION_TARGET_TYPE` (default `"review"`) the `target_key` is a review id
and the review is hidden (`rejected`) or published (`approved`). `needs_review`
and `dismissed` deliberately move nothing — the first says the automation
abstained, the second speaks about a report rather than about content.

The verdict is applied as `services.SYSTEM_ACTOR`, which is the **only** way
past the fail-closed `can_moderate` gate (seam #2). That gate is right for
humans and exactly wrong for the platform's own decision: authorization already
happened in the moderation module, and a target type with no `can_moderate`
callback would otherwise deny the platform a verdict about its own content. The
bypass is recognized by object identity, so nothing can imitate it, and it
buys visibility only — `respond()` refuses the system actor outright.

Idempotency is **by state**, not by event id: a redelivered verdict finds the
review already in the decided status and returns without a write and without a
fact. No processed-event table exists or is needed.

`reviews.moderation_content` — `{review_id} -> {text, title, language, media,
author_id, url, rating, status, target_type, target_key, created_at}` — is the
read half: identifiers travel on the bus, content is fetched when it is looked
at, so a moderator opening a case hours later reads the review as it is now. A
review has no title, declared language, media or public per-review URL, so
those four come back empty rather than invented. Schemas:
`schemas/consumes/moderation.completed.json`,
`schemas/functions/reviews.moderation_content.json`.

### 7. Account life cycle — `user.deleted` + `user.merged` (consume)

Both halves are answered in `actions.py`, and core 0.52.x makes answering only
one of them a system-check ERROR (`stapel_core.lifecycle.E001`).

| Event | What this module does |
|---|---|
| `user.deleted` | Erase the account's authored reviews (cascading to their responses) and its responses on other people's reviews — `gdpr.ReviewsGDPRProvider` |
| `user.merged` | Re-parent `Review.author` and `Response.author` from `from_user_id` to `into_user_id`, in one transaction |

Merge policy, in full: both columns are `on_delete=CASCADE`, so a guest's
review is not orphaned by the guest row's deletion — it is destroyed. Where a
target type sets `one_per_author` and **both** accounts reviewed the same
`(target_type, target_key)`, the **survivor's review wins** and the guest's
duplicate is dropped (its `Response` goes with it); if that duplicate was
published, `reviews.review.hidden` is emitted with `reason:
"merged_duplicate"` and the recomputed aggregate, so a host's `avg_rating`
projection shrinks with it. A type without the policy — or one the host has
de-registered — keeps both rows. A guest with rows to carry and a survivor
this deployment has not projected yet raises `actions.MergeTargetNotReady`, so
the outbox redelivers rather than marking the transfer done. Schema:
`schemas/consumes/user.merged.json`.

### Settings — `STAPEL_REVIEWS` namespace (`conf.py`)

| Key | Default | Meaning |
|---|---|---|
| `TARGET_TYPES` | `{}` | The target-type registry (seam #1) |
| `MODERATION_DEFAULT` | `"post"` | Default moderation mode (`post`/`pre`) — **config axis** |
| `RESPONSES` | `True` | Owner responses allowed by default — **config axis** |
| `RATING_MIN` | `1` | Inclusive minimum rating (tuning knob) |
| `RATING_MAX` | `5` | Inclusive maximum rating (tuning knob) |
| `MODERATION_TARGET_TYPE` | `"review"` | The verdict `target_type` that means "a review" (seam #6, tuning knob) |

`MODERATION_DEFAULT` and `RESPONSES` are the two CTO-facing config axes
(capability-config.md §16 — behavioral, not gating). `TARGET_TYPES` is the
merge-registry seam. See `docs/capabilities.json`.

### Serializer seams (`views.py`)

Every view declares `request_serializer_class` / `response_serializer_class`
via `SerializerSeamMixin` — subclass the view, override the attribute, remount
the URL. No need to rewrite HTTP method bodies.

### API contract notes

- List is **anchor-paginated** (`ReviewAnchorPagination`, cursor over
  `created_at`, newest first): `?target_type=&target_key=&limit=&anchor=`.
  Non-moderators see published only; a moderator may pass `?include=all`
  (silently narrowed to published if the `can_moderate` callback denies —
  no leak, no error).
- List is addressed along **exactly one** axis: a target
  (`target_type` + `target_key`) or an owner (`owner_key`, with `target_type`
  free to narrow it). Both is `error.400.reviews_ambiguous_addressing`,
  neither is `error.400.reviews_unknown_target_type` — the 400 it always was.
  On the owner axis `?include=all` is gated on core's staff predicate rather
  than on `can_moderate`: that callback answers about ONE target, and this
  list spans every target an owner owns. A non-staff caller asking for `all`
  is silently narrowed to published, exactly as a non-moderator is on the
  target axis.
- Reads (`list`, `aggregate`) are permissive on unknown target types (empty
  result); **writes** require the type to be registered.
- `POST /reviews/api/v1/reviews/aggregates/by-owner` is a **read** despite the verb —
  public, `reviews-aggregate`-throttled, body `{owner_keys: [...],
  target_type?}` with at most 100 keys, answering the same
  `{owner_key: {avg, count}}` map as the comm Function. A POST because owner
  keys are opaque host strings and a page showing twenty sellers wants one
  request.

### Contract emission — the `schema` + `flows` + `errors` + `capabilities` quartet

`make contract` emits `docs/{schema,flows,errors,capabilities}.json` from a
single-module `{reviews + core}` Django instance mounted at the canonical
`/reviews/api/v1/` prefix. Regenerate after any serializer/view/url/error-key
change and commit; `tests/test_contract.py` is the drift gate (Python 3.12
only — drf-spectacular renders differently across minors). `flows.json` is `[]`
(no `@flow_step` annotations).

## Anti-patterns

- **Do not** teach the module a concrete target (a Seller/Listing FK, a
  domain enum of types). The target is opaque; types are the host's registry.
- **Do not** import a host model to answer "may this author review?" — that is
  the `can_review` / `can_moderate` comm callback's job.
- **Do not** recompute the aggregate in the host from raw reviews — subscribe
  to the published/hidden facts and project.
- **Do not** derive a seller's rating by fetching their listings and averaging
  the per-listing aggregates. That re-implements the visibility rule, costs an
  N+1, and drops whatever the catalog forgot to return; register
  `owner_key_for` and read `reviews.aggregates_by_owner_keys`.
- **Do not** treat an empty `owner_key` as an owner. Rows nobody answered for
  are excluded from the owner aggregate, never pooled under `""` — and after
  registering a resolver, run the backfill, or the seller rating silently
  counts only reviews written since.
- **Do not** fail *open* on a missing `can_moderate` callback — an unset
  moderator gate denies, it never silently authorizes.
- **Do not** widen `SYSTEM_ACTOR` into a general "trusted caller" flag. It has
  exactly one caller (`apply_verdict`, on a verdict another module already
  authorized); a boolean threaded through `moderate_review` would make the
  bypass reachable from anywhere a `True` can be typed.
- **Do not** dedupe the verdict consumer by `event_id`. Idempotency is by
  state; a processed-event table is a second source of truth that has to be
  retained, indexed and reconciled with the state that already answers.
