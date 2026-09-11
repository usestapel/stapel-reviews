# The owner LIST's index (owner_key, -created_at).
#
# Expand-only: one added index and nothing else, so an older release keeps
# running against the migrated schema while the new one rolls out
# (release-management.md §3). The owner aggregate's rev_owner_status index
# (0002) answers a grouped count; listing a seller's reviews newest-first pages
# on created_at, which is the ordering this index carries.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0002_review_owner_key"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="review",
            index=models.Index(
                fields=["owner_key", "-created_at"], name="rev_owner_created"
            ),
        ),
    ]
