"""Django migration 0008 - multi currency migration (no-op placeholder replacement)

This migration was removed because a bad placeholder commit accidentally introduced
an incorrect file. The real migration will be authored and applied separately.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0007_some_previous_migration"),
    ]

    operations = [
        # Intentionally left empty to avoid applying unintended schema changes.
    ]
