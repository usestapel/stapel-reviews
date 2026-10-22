# one_per_author held by the database, not only by a read-then-create.
#
# Expand-only (release-management.md §3): a nullable-free column with a
# database default, so an older release that inserts without naming it keeps
# working, plus a PARTIAL unique constraint over rows that carry the flag.
# Every existing row gets False and sits outside the constraint, so historic
# duplicates (written before this guard) cannot make the migration fail; the
# service's existence check still refuses a new review beside them.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0003_review_owner_created_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="unique_per_author",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.AddConstraint(
            model_name="review",
            constraint=models.UniqueConstraint(
                condition=models.Q(unique_per_author=True),
                fields=("target_type", "target_key", "author"),
                name="rev_one_per_author",
            ),
        ),
    ]
