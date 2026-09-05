# Denormalised target owner (Review.owner_key) + its aggregate index.
#
# Expand-only: a blank-defaulted column and one index, both additive, so an
# older release keeps running against the migrated schema while the new one
# rolls out (release-management.md §3). Existing rows land with an empty
# owner_key and are filled by `manage.py reviews_backfill_owner_keys`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="owner_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddIndex(
            model_name="review",
            index=models.Index(
                fields=["owner_key", "status"], name="rev_owner_status"
            ),
        ),
    ]
