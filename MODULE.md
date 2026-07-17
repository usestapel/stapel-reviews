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
- **API** — create review, list by target (anchor-paginated, published-only for
  non-moderators), aggregate by target, moderate (hide/publish), respond. DTO +
  serializer seams + OpenAPI (drf-spectacular).

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

### Settings — `STAPEL_REVIEWS` namespace (`conf.py`)

| Key | Default | Meaning |
|---|---|---|
| `TARGET_TYPES` | `{}` | The target-type registry (seam #1) |
| `MODERATION_DEFAULT` | `"post"` | Default moderation mode (`post`/`pre`) — **config axis** |
| `RESPONSES` | `True` | Owner responses allowed by default — **config axis** |
| `RATING_MIN` | `1` | Inclusive minimum rating (tuning knob) |
| `RATING_MAX` | `5` | Inclusive maximum rating (tuning knob) |

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
- Reads (`list`, `aggregate`) are permissive on unknown target types (empty
  result); **writes** require the type to be registered.

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
- **Do not** fail *open* on a missing `can_moderate` callback — an unset
  moderator gate denies, it never silently authorizes.
