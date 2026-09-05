# CONFIG.MD — stapel-reviews

Config registry for **stapel-reviews** (`static-scaffold-and-config.md` §2). One
row per configuration key the module reads, its **source** (`env` = the process
environment / the `STAPEL_REVIEWS` settings dict), what it is for, whether it is
required, and its default.

Every key is read through `reviews_settings` (`stapel_reviews.conf.AppSettings`,
namespace `STAPEL_REVIEWS`). Resolution order per key: `settings.STAPEL_REVIEWS`
dict → a flat Django setting of the same name → environment variable → the
default below. `MODERATION_DEFAULT` and `RESPONSES` are CTO-facing config axes
(capability-config.md §16).

## stapel-reviews

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| TARGET_TYPES | env | flagship seam: {type_name: policy} merged OVER the (empty) built-ins; None removes a type. Policies name comm-Function callbacks for eligibility/moderation, and an optional `owner_key_for` resolver (see below). | no | {} |
| MODERATION_DEFAULT | env | axis: `post` (published on creation, hideable later) or `pre` (held pending until published). A type policy may override. | no | post |
| RESPONSES | env | axis: whether a target owner may attach a single Response to a review. A type policy may override. | no | true |
| RATING_MIN | env | inclusive minimum rating (out-of-range is a 400). | no | 1 |
| RATING_MAX | env | inclusive maximum rating (out-of-range is a 400). | no | 5 |
| LIST_THROTTLE | env | rate limit for `GET /reviews` (anonymously readable — storefront F5). DRF rate string. | no | 120/min |
| AGGREGATE_THROTTLE | env | rate limit for `GET /reviews/aggregate` and `POST /aggregates/by-owner/` (both anonymously readable). DRF rate string. | no | 300/min |
| ALLOW_ANONYMOUS_WRITES | env | whether a guest (anonymous account) may leave a review. Gates `POST /reviews` only; the reads stay anonymous either way. | no | false |

### ALLOW_ANONYMOUS_WRITES

A storefront may mint an anonymous account silently — press "save this listing"
as a stranger and you get a real user row with `is_anonymous=True` and a valid
session. That session is genuinely authenticated, so `IsAuthenticated` cannot
tell it from a registered one and a "sign up to review" wall drawn only in the
frontend is decoration. This key is the wall server-side: with it off, a guest
gets `error.403.reviews_anonymous_not_allowed` on `POST /reviews`, while
`GET /reviews` and `GET /reviews/aggregate` stay open to it. The default is
CLOSED because that is the cheap answer either way — a deployment that mints no
anonymous users has none to reject, and one that does gets the wall it already
believed it had. Setting it to `true` is the explicit statement "guests may
review here"; a review from an account nobody can trace is worth little as
social proof, so it should be said out loud. Moderation and owner responses are
NOT gated by it — both already fail closed behind the target type's
`can_moderate` callback.

### TARGET_TYPES → `owner_key_for`

An optional third callback inside a type policy, next to `can_review` and
`can_moderate`: **who owns this target?** — written either as a callable
`owner_key_for(target_key) -> str | None`, or (for a policy that must stay
JSON-shaped) as a comm Function name called with `{target_type, target_key}`
and answering a bare string or `{"owner_key": ...}`.

It is asked once, when the review is written, and the answer is denormalised
onto `Review.owner_key`. That single column is what lets the module answer
"what is this **seller** rated, over everything they own"
(`reviews.aggregates_by_owner_keys`, `POST /reviews/api/v1/aggregates/by-owner/`)
without ever learning what a listing or a seller is. A resolver that raises is
not swallowed on the write path — an unstamped review is a seller rating that
is quietly missing reviews.

Registering none is the default and costs nothing: owner keys stay empty, the
owner aggregate answers `{}`, and every other behaviour is unchanged. Reviews
written before a resolver was registered are stamped by
`python manage.py reviews_backfill_owner_keys` (idempotent, `--target-type`,
`--limit`, `--dry-run`).
