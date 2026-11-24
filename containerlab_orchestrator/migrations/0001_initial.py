"""Create the models of the app and link the lab statuses to the Lab model."""

import uuid
from decimal import Decimal

import django.core.serializers.json
import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
import nautobot.core.models.fields
import nautobot.core.models.ordering
import nautobot.core.models.query_functions
import nautobot.extras.models.mixins
import nautobot.extras.models.statuses
from django.conf import settings
from django.db import migrations, models

import containerlab_orchestrator.validators

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
    initial = True

    dependencies = [
        ("dcim", "0084_add_module_type_image_support"),
        ("extras", "0142_remove_scheduledjob_approval_required"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ContainerlabHost",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "_custom_field_data",
                    models.JSONField(blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
                ),
                ("name", models.CharField(max_length=255, unique=True)),
                (
                    "remote_url",
                    nautobot.core.models.fields.LaxURLField(
                        max_length=500, validators=[containerlab_orchestrator.validators.validate_clab_api_url]
                    ),
                ),
                ("verify_ssl", models.BooleanField(default=True)),
                ("description", models.CharField(blank=True, max_length=255)),
                (
                    "secrets_group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="containerlab_hosts",
                        to="extras.secretsgroup",
                    ),
                ),
                ("tags", nautobot.core.models.fields.TagsField(through="extras.TaggedItem", to="extras.Tag")),
            ],
            options={
                "verbose_name": "Containerlab host",
                "ordering": ["name"],
            },
            bases=(
                nautobot.extras.models.mixins.DataComplianceModelMixin,
                nautobot.extras.models.mixins.DynamicGroupMixin,
                nautobot.extras.models.mixins.NotesMixin,
                models.Model,
            ),
        ),
        migrations.CreateModel(
            name="Lab",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "_custom_field_data",
                    models.JSONField(blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=255, validators=[containerlab_orchestrator.validators.validate_clab_lab_name]
                    ),
                ),
                ("device_filter", models.JSONField(blank=True, default=dict)),
                ("config_source", models.CharField(default="none", max_length=50)),
                (
                    "mgmt_subnet",
                    models.CharField(
                        max_length=255, validators=[containerlab_orchestrator.validators.validate_ipv4_network]
                    ),
                ),
                ("topology_yaml", models.TextField(blank=True, editable=False)),
                ("built_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("last_inspect", models.JSONField(blank=True, default=dict, editable=False)),
                ("devices", models.ManyToManyField(blank=True, related_name="labs", to="dcim.device")),
                (
                    "dynamic_group",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="labs",
                        to="extras.dynamicgroup",
                    ),
                ),
                (
                    "host",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="labs",
                        to="containerlab_orchestrator.containerlabhost",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="labs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "status",
                    nautobot.extras.models.statuses.StatusField(
                        editable=False, on_delete=django.db.models.deletion.PROTECT, to="extras.status"
                    ),
                ),
                ("tags", nautobot.core.models.fields.TagsField(through="extras.TaggedItem", to="extras.Tag")),
            ],
            options={
                "verbose_name": "Lab",
                "ordering": ["host", "name"],
            },
            bases=(
                nautobot.extras.models.mixins.DataComplianceModelMixin,
                nautobot.extras.models.mixins.DynamicGroupMixin,
                nautobot.extras.models.mixins.NotesMixin,
                models.Model,
            ),
        ),
        migrations.CreateModel(
            name="LabNode",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("node_name", models.CharField(max_length=255)),
                ("is_stub", models.BooleanField(default=False)),
                (
                    "kind",
                    models.CharField(
                        max_length=255, validators=[containerlab_orchestrator.validators.validate_clab_kind]
                    ),
                ),
                (
                    "image",
                    models.CharField(
                        max_length=255, validators=[containerlab_orchestrator.validators.validate_image_reference]
                    ),
                ),
                ("mgmt_ipv4", models.GenericIPAddressField(blank=True, null=True, protocol="IPv4")),
                ("state", models.CharField(blank=True, max_length=255)),
                ("container_id", models.CharField(blank=True, max_length=255)),
                (
                    "device",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lab_nodes",
                        to="dcim.device",
                    ),
                ),
                (
                    "lab",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lab_nodes",
                        to="containerlab_orchestrator.lab",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lab node",
                "ordering": ["lab", "node_name"],
            },
        ),
        migrations.CreateModel(
            name="NodeMapping",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "_custom_field_data",
                    models.JSONField(blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
                ),
                (
                    "kind",
                    models.CharField(
                        max_length=255, validators=[containerlab_orchestrator.validators.validate_clab_kind]
                    ),
                ),
                (
                    "image",
                    models.CharField(
                        max_length=255, validators=[containerlab_orchestrator.validators.validate_image_reference]
                    ),
                ),
                ("node_type", models.CharField(blank=True, max_length=255)),
                ("env", models.JSONField(blank=True, default=dict)),
                (
                    "cpu",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                    ),
                ),
                (
                    "memory",
                    models.CharField(
                        blank=True,
                        max_length=32,
                        validators=[containerlab_orchestrator.validators.validate_clab_memory],
                    ),
                ),
                ("overrides", models.JSONField(blank=True, default=dict)),
                ("config_source", models.CharField(blank=True, default="", max_length=50)),
                ("config_template", models.TextField(blank=True)),
                ("rename_interfaces", models.BooleanField(default=True)),
                (
                    "device_type",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="node_mapping",
                        to="dcim.devicetype",
                    ),
                ),
                (
                    "platform",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="node_mapping",
                        to="dcim.platform",
                    ),
                ),
                ("tags", nautobot.core.models.fields.TagsField(through="extras.TaggedItem", to="extras.Tag")),
            ],
            options={
                "verbose_name": "Node mapping",
                "ordering": ["device_type", "platform"],
            },
            bases=(
                nautobot.extras.models.mixins.DataComplianceModelMixin,
                nautobot.extras.models.mixins.DynamicGroupMixin,
                nautobot.extras.models.mixins.NotesMixin,
                models.Model,
            ),
        ),
        migrations.CreateModel(
            name="InterfaceMapping",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "physical_name",
                    models.CharField(
                        max_length=255, validators=[containerlab_orchestrator.validators.validate_trimmed_name]
                    ),
                ),
                (
                    "_physical_name",
                    nautobot.core.models.fields.NaturalOrderingField(
                        "physical_name",
                        blank=True,
                        db_index=True,
                        max_length=255,
                        naturalize_function=nautobot.core.models.ordering.naturalize_interface,
                    ),
                ),
                (
                    "container_name",
                    models.CharField(
                        max_length=255,
                        validators=[containerlab_orchestrator.validators.validate_container_interface_name],
                    ),
                ),
                (
                    "node_mapping",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interface_mappings",
                        to="containerlab_orchestrator.nodemapping",
                    ),
                ),
            ],
            options={
                "verbose_name": "Interface mapping",
                "ordering": ("node_mapping", nautobot.core.models.query_functions.CollateAsChar("_physical_name")),
            },
        ),
        migrations.AddConstraint(
            model_name="lab",
            constraint=models.UniqueConstraint(
                fields=("host", "name"), name="containerlab_orchestrator_lab_host_name_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="lab",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("name"),
                models.F("host"),
                name="containerlab_orchestrator_lab_host_name_ci_unique",
                violation_error_message="A lab with this name already exists on this host.",
            ),
        ),
        migrations.AddConstraint(
            model_name="labnode",
            constraint=models.UniqueConstraint(
                fields=("lab", "node_name"), name="containerlab_orchestrator_labnode_name_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="nodemapping",
            constraint=models.CheckConstraint(
                condition=models.Q(("device_type__isnull", True), ("platform__isnull", True), _connector="OR"),
                name="containerlab_orchestrator_nodemapping_not_both",
                violation_error_message="Set a Device Type or a Platform, but not both.",
            ),
        ),
        migrations.AddConstraint(
            model_name="interfacemapping",
            constraint=models.UniqueConstraint(
                fields=("node_mapping", "physical_name"),
                name="containerlab_orchestrator_interfacemapping_physical_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="interfacemapping",
            constraint=models.UniqueConstraint(
                fields=("node_mapping", "container_name"),
                name="containerlab_orchestrator_interfacemapping_container_unique",
            ),
        ),
        migrations.RunPython(add_lab_statuses, remove_lab_statuses),
    ]
