## What this is

A generic review core — **Review** (author + rating + body about an opaque
target) and **Response** (the target owner's reply) — driven entirely by a
**per-target-type policy registry**. The module ships knowing *nothing* about
what gets reviewed: a host registers its target types (a seller, a listing, a
driver, a course), each with a policy, and answers the domain questions —
"may this author review?", "who owns this target?" — through **comm Function
callbacks**, so the module never imports a host model.

## Quick start

```python
INSTALLED_APPS = [
    # ...
    "stapel_reviews",
]

# urls.py
path("reviews/", include("stapel_reviews.urls"))
```

## Concepts

- **Target** — opaque: `target_type` (a key the host registered) + `target_key`
  (an opaque host string — a UUID, a slug, a composite). No FK to any host
  model; the module is domain-blind.
- **Policy** — per target type: who may review (`can_review` comm callback),
  pre/post moderation, one-review-per-author, whether owner responses are
  allowed (`allow_response`), who may moderate/respond (`can_moderate` comm
  callback), and — optionally — who *owns* a target (`owner_key_for`).
- **Aggregate** — the module owns `avg`/`count` over *published* reviews per
  target, and emits a generic fact carrying it on every visibility change, so a
  host catalog maintains its own rating **projection** (§10) without calling
  back.
- **Owner key** — optional, denormalised: the answer of the type's
  `owner_key_for` resolver, stamped on the review when it is written, so the
  module can also aggregate *everything one owner owns* (a seller-wide rating)
  while still knowing nothing about what a listing or a seller is.

```python
STAPEL_REVIEWS = {
    "TARGET_TYPES": {
        "seller": {
            "can_review": "marketplace.buyer_of_seller",   # host comm Function
            "can_moderate": "marketplace.is_seller_owner",
            "moderation": "post",
            "one_per_author": True,
            "allow_response": True,
        },
        "listing": {
            "moderation": "pre",
            # Optional: who owns this target. A callable, or a comm Function
            # name for a policy that has to stay JSON-shaped. Registering none
            # leaves owner_key empty and changes nothing else.
            "owner_key_for": lambda target_key: seller_id_of(target_key),
        },
    },
}
```

```python
from stapel_reviews import services

review = services.create_review(
    target_type="seller", target_key="s-42", author=user, rating=5, body="great",
)
services.moderate_review(review, actor=owner, action="hide", reason="spam")
services.respond(review, author=owner, body="thanks for the feedback")
agg = services.aggregate("seller", "s-42")   # Aggregate(avg=..., count=...)

# Everything one owner owns, batched (the seller-wide rating):
services.aggregates_by_owner_keys(["s-42", "s-43"])  # {"s-42": {"avg": .., "count": ..}}
```

## Settings

All configuration lives in the `STAPEL_REVIEWS` namespace (dict setting, flat
setting, or env var — resolved lazily):

| Key | Default | Meaning |
|---|---|---|
| `TARGET_TYPES` | `{}` | The target-type registry `{type: policy}`, merged over the (empty) built-ins; `None` removes a type |
| `MODERATION_DEFAULT` | `"post"` | Default moderation mode (`post`/`pre`) for types that don't override it |
| `RESPONSES` | `True` | Whether owner responses are allowed by default |
| `RATING_MIN` | `1` | Inclusive minimum rating |
| `RATING_MAX` | `5` | Inclusive maximum rating |
| `MODERATION_TARGET_TYPE` | `"review"` | The `target_type` on an incoming `moderation.completed` verdict that means "this is about a review" |

## comm surface

| Kind | Name | Contract |
|---|---|---|
| Emit | `reviews.review.published` | A review became visible — carries `{aggregate: {avg, count}}` for the host projection |
| Emit | `reviews.review.hidden` | A review left the visible set — carries the updated aggregate |
| Function | `reviews.aggregate` | `{target_type, target_key}` -> `{avg, count}` |
| Function | `reviews.aggregates_by_keys` | `{keys, target_type?}` -> `{key: {avg, count}}` — a Projection's `live_query` |
| Function | `reviews.aggregates_by_owner_keys` | `{owner_keys, target_type?}` -> `{owner_key: {avg, count}}` — the owner-wide rating |
| Function | `reviews.aggregates_export` | `{cursor?, limit?}` -> `{rows, cursor, total}` — a Projection's `source_of_truth` |
| Function | `reviews.moderation_content` | `{review_id}` -> `{text, title, language, media, author_id, url, …}` |
| Consume | `moderation.completed` | `{target_type, target_key, decision, …}` — a platform verdict, applied to the review as the system actor |
| Callback (host) | policy `can_review` | `{author_id, target_type, target_key}` -> bool — the host answers |
| Callback (host) | policy `can_moderate` | `{actor_id, target_type, target_key}` -> bool — the host answers |
| Callback (host) | policy `owner_key_for` | `target_key` -> `str \| None` (callable), or a comm Function `{target_type, target_key}` -> owner key — optional |

### Host rating projection

The two batch Functions are the halves a host `Projection` over reviews is
declared against — one keyed read for live traffic, one snapshot for rebuild:

```python
class ListingReviewSummaryProjection(Projection):
    consumes = ("reviews.review.published", "reviews.review.hidden")
    source_key = "target_key"
    live_query = "reviews.aggregates_by_keys"      # local mode reads through it
    source_of_truth = "reviews.aggregates_export"  # rebuild() / drift_check()
```

### Owner-wide ratings

A marketplace needs the rating of a **seller**, not only of each listing — and
the module must not learn what a listing is to produce it. The seam is one
optional resolver and one denormalised column:

```python
STAPEL_REVIEWS = {
    "TARGET_TYPES": {
        "listing": {"owner_key_for": "catalog.owner_of_listing"},  # or a callable
    },
}
```

The resolver is asked once, when the review is written, and its answer is
stored on `Review.owner_key`. Reads go through
`reviews.aggregates_by_owner_keys` or `POST /reviews/api/v1/reviews/aggregates/by-owner`
(public, up to 100 owner keys per call), which return `{owner_key: {avg,
count}}` over published reviews with the same rounding as `reviews.aggregate`.

The same column answers the seller page's **rows**, not only its number: the
list endpoint takes `owner_key` *instead of* the target pair —

```
GET /reviews/api/v1/reviews?owner_key=s-42[&target_type=listing]
```

— every review of everything that owner owns, newest first, same visibility
rule, same anchor pagination, same item shape. Exactly one addressing per
request: naming both a target and an owner is
`error.400.reviews_ambiguous_addressing`, naming neither is
`error.400.reviews_unknown_target_type` as before.
Reviews written before the resolver was registered carry an empty owner key —
stamp them once with:

```bash
python manage.py reviews_backfill_owner_keys [--target-type listing] [--dry-run]
```

A host that registers no resolver stamps nothing, and the owner aggregate
simply answers `{}`.

### Moderation verdicts

An external moderation module owns the decision; this module owns applying it.
When a case about a review resolves, `moderation.completed` arrives and the
review is hidden (`rejected`) or published (`approved`) — `needs_review` and
`dismissed` deliberately move nothing. The verdict is applied as
`services.SYSTEM_ACTOR`, the one actor that gets past the fail-closed
`can_moderate` gate: authorization already happened where the verdict was made,
and asking a target type with no `can_moderate` callback would deny the
platform its own decision. Redelivery is a no-op — idempotency is by state, and
no table of processed event ids is kept.

## Extension points

See [MODULE.md](https://github.com/usestapel/stapel-reviews/blob/main/MODULE.md) — the agent-facing map of every fork-free seam (the
TARGET_TYPES registry and its policy callbacks, the projection emits, the
aggregate Function, serializer seams, settings).

## Development

```bash
pip install -e . && pip install pytest pytest-django ruff
./setup-hooks.sh
pytest tests/
```
