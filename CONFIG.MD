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
| TARGET_TYPES | env | flagship seam: {type_name: policy} merged OVER the (empty) built-ins; None removes a type. Policies name comm-Function callbacks for eligibility/moderation. | no | {} |
| MODERATION_DEFAULT | env | axis: `post` (published on creation, hideable later) or `pre` (held pending until published). A type policy may override. | no | post |
| RESPONSES | env | axis: whether a target owner may attach a single Response to a review. A type policy may override. | no | true |
| RATING_MIN | env | inclusive minimum rating (out-of-range is a 400). | no | 1 |
| RATING_MAX | env | inclusive maximum rating (out-of-range is a 400). | no | 5 |
