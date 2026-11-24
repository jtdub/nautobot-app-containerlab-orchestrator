"""Add the rename_interfaces flag to NodeMapping."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("containerlab_orchestrator", "0005_lab_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="nodemapping",
            name="rename_interfaces",
            field=models.BooleanField(default=True),
        ),
    ]
