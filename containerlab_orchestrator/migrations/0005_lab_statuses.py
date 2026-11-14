"""Link the lab statuses to the Lab model."""

from django.db import migrations

LAB_STATUSES = (
    ("Planned", "00bcd4", "Unit has been planned"),
    ("Built", "2196f3", "The lab topology is built"),
    ("Deployed", "4caf50", "The lab runs on its host"),
    ("Failed", "f44336", "Unit has failed"),
    ("Destroyed", "9e9e9e", "The lab containers are removed"),
)
"""The name, the color, and the description of each lab status."""


def add_lab_statuses(apps, schema_editor):
    """Create each lab status that does not exist, and link each lab status to Lab."""
    Status = apps.get_model("extras", "Status")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_type = ContentType.objects.get_for_model(apps.get_model("containerlab_orchestrator", "Lab"))
    for name, color, description in LAB_STATUSES:
        status, _ = Status.objects.get_or_create(name=name, defaults={"color": color, "description": description})
        status.content_types.add(content_type)


def remove_lab_statuses(apps, schema_editor):
    """Remove the link between Lab and each lab status, and keep the statuses, because other models use them."""
    Status = apps.get_model("extras", "Status")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_type = ContentType.objects.get_for_model(apps.get_model("containerlab_orchestrator", "Lab"))
    for status in Status.objects.filter(name__in=[name for name, _, _ in LAB_STATUSES]):
        status.content_types.remove(content_type)


class Migration(migrations.Migration):
    dependencies = [
        ("containerlab_orchestrator", "0004_lab_labnode"),
    ]

    operations = [
        migrations.RunPython(add_lab_statuses, remove_lab_statuses),
    ]
