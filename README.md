# stapel-reviews

[![CI](https://github.com/usestapel/stapel-reviews/actions/workflows/ci.yml/badge.svg)](https://github.com/usestapel/stapel-reviews/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/usestapel/stapel-reviews/graph/badge.svg)](https://codecov.io/gh/usestapel/stapel-reviews)
[![PyPI](https://img.shields.io/pypi/v/stapel-reviews.svg)](https://pypi.org/project/stapel-reviews/)

Target-generic reviews and ratings for the [Stapel framework](https://github.com/usestapel) —
composable Django apps that deploy as a monolith or as microservices
without changing module code.

A generic review core — **Review** (author + rating + body about an opaque
target) and **Response** (the target owner's reply) — driven entirely by a
**per-target-type policy registry**. The module ships knowing *nothing* about
what gets reviewed: a host registers its target types (a seller, a listing, a
driver, a course), each with a policy, and answers the domain questions —
"may this author review?", "who owns this target?" — through **comm Function
callbacks**, so the module never imports a host model.

## Install

```bash
pip install stapel-reviews
```

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
  callback).
- **Aggregate** — the module owns `avg`/`count` over *published* reviews per
  target, and emits a generic fact carrying it on every visibility change, so a
  host catalog maintains its own rating **projection** (§10) without calling
  back.

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
        "listing": {"moderation": "pre"},
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

## comm surface

| Kind | Name | Contract |
|---|---|---|
| Emit | `reviews.review.published` | A review became visible — carries `{aggregate: {avg, count}}` for the host projection |
| Emit | `reviews.review.hidden` | A review left the visible set — carries the updated aggregate |
| Function | `reviews.aggregate` | `{target_type, target_key}` -> `{avg, count}` |
| Callback (host) | policy `can_review` | `{author_id, target_type, target_key}` -> bool — the host answers |
| Callback (host) | policy `can_moderate` | `{actor_id, target_type, target_key}` -> bool — the host answers |

## Extension points

See [MODULE.md](MODULE.md) — the agent-facing map of every fork-free seam (the
TARGET_TYPES registry and its policy callbacks, the projection emits, the
aggregate Function, serializer seams, settings).

## Development

```bash
pip install -e . && pip install pytest pytest-django ruff
./setup-hooks.sh
pytest tests/
```

## License

MIT
