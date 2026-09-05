"""Fill ``Review.owner_key`` on rows written before the owner resolver existed.

    python manage.py reviews_backfill_owner_keys [--target-type seller]
        [--batch-size N] [--limit N] [--dry-run]

``Review.owner_key`` is denormalised at write time from the target type's
``owner_key_for`` resolver (``STAPEL_REVIEWS["TARGET_TYPES"][<type>]``), so
every review written *before* a host registered that resolver carries an empty
one — and an owner aggregate that quietly omits a seller's first hundred
reviews is worse than no owner aggregate at all. This command is the one-time
(or rerunnable) pass over those rows.

Idempotent and resumable: the candidate set is exactly the rows whose
``owner_key`` is still empty, so a second full run is a no-op and a crashed run
resumes where it stopped. The resolver is asked once per DISTINCT target, not
once per review. A target the resolver cannot answer for — it raised, it
returned nothing, its type is unregistered or registers no resolver — is
counted and stepped over, never fatal; fix the resolver and rerun, the rows are
still candidates.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Backfill Review.owner_key from the target type's owner_key_for "
        "resolver, for reviews that predate stamp-on-write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--target-type",
            default="",
            help="Only backfill reviews of this target type (default: all).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
            help="Distinct targets resolved per keyset page (default 500).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Stop after this many distinct candidate targets — for "
                "running the backfill in bounded slices on a large table."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Resolve and count, write nothing.",
        )

    def handle(self, *args, **options):
        from ...services import BACKFILL_BATCH_SIZE, backfill_owner_keys

        stats = backfill_owner_keys(
            target_type=options["target_type"] or "",
            batch_size=options["batch_size"] or BACKFILL_BATCH_SIZE,
            limit=options["limit"],
            dry_run=options["dry_run"],
        )
        verb = "would stamp" if options["dry_run"] else "stamped"
        self.stdout.write(
            f"reviews_backfill_owner_keys: {stats['targets']} candidate "
            f"target(s), {verb} {stats['stamped_rows']} review(s) across "
            f"{stats['stamped_targets']} target(s); {stats['unresolved']} "
            f"target(s) left unresolved, {stats['no_resolver']} with no "
            f"owner_key_for resolver."
        )
        if stats["targets"] and not stats["stamped_targets"]:
            self.stdout.write(
                self.style.WARNING(
                    "Nothing was stamped. Check that the target types you are "
                    'backfilling declare an "owner_key_for" resolver in '
                    'STAPEL_REVIEWS["TARGET_TYPES"] and that it can answer — '
                    "an empty owner_key is a review no owner aggregate counts."
                )
            )
