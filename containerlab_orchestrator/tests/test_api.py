"""Tests for the REST API of the app."""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, override_settings
from nautobot.apps.choices import JobResultStatusChoices
from nautobot.apps.testing import APIViewTestCases, TransactionTestCase, create_job_result_and_run_job
from nautobot.dcim.models import DeviceType, Location, Manufacturer, Platform
from nautobot.extras.models import DynamicGroup, ObjectChange, Status
from rest_framework import status

from containerlab_orchestrator.api.serializers import LabSerializer
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, LabNode, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    SECRET_VALUE,
    create_containerlab_hosts,
    create_http_secrets_group,
    create_interface_mappings,
    create_lab_devices,
    create_lab_nodes,
    create_labs,
    create_node_mappings,
    patch_secret_variables,
)
from containerlab_orchestrator.validators import CIDR_MESSAGE, SIZE_MESSAGE

OWNER_KEYS = {"id", "object_type", "url", "display", "username"}
"""The keys of a lab owner in the REST API at depth 1 or more, and in the change log."""


class ContainerlabHostAPITestCase(APIViewTestCases.APIViewTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot REST API tests on ContainerlabHost."""

    model = ContainerlabHost
    choices_fields = ()

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()
        cls.secrets_group = create_http_secrets_group("API credentials")
        cls.create_data = [
            {"name": "clab-api-01", "remote_url": "https://clab-api-01:8080", "secrets_group": cls.secrets_group.pk},
            {
                "name": "clab-api-02",
                "remote_url": "http://clab-api-02:8080",
                "verify_ssl": False,
                "secrets_group": cls.secrets_group.pk,
                "description": "Second API host",
            },
            {"name": "clab-api-03", "remote_url": "https://clab-api-03:8080", "secrets_group": cls.secrets_group.pk},
        ]
        cls.update_data = {"remote_url": "https://clab-updated:8080", "description": "Updated host"}
        cls.bulk_update_data = {"verify_ssl": False}

    def test_create_with_incomplete_secrets_group_returns_400(self):
        self.add_permissions("containerlab_orchestrator.add_containerlabhost", "extras.view_secretsgroup")
        incomplete_group = create_http_secrets_group("No password", password=False)
        data = {"name": "clab-bad", "remote_url": "https://clab-bad:8080", "secrets_group": incomplete_group.pk}

        response = self.client.post(self._get_list_url(), data, format="json", **self.header)

        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["secrets_group"],
            ["The Secrets Group must have an HTTP(S) password secret."],
        )
        self.assertFalse(ContainerlabHost.objects.filter(name="clab-bad").exists())

    def test_csv_import_with_incomplete_secrets_group_returns_400(self):
        self.add_permissions("containerlab_orchestrator.add_containerlabhost", "extras.view_secretsgroup")
        incomplete_group = create_http_secrets_group("No password CSV", password=False)
        csv_data = f"name,remote_url,secrets_group\nclab-csv,https://clab-csv:8080,{incomplete_group.pk}\n"

        response = self.client.post(self._get_list_url(), csv_data, content_type="text/csv", **self.header)

        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn("The Secrets Group must have an HTTP(S) password secret.", str(response.data))
        self.assertFalse(ContainerlabHost.objects.filter(name="clab-csv").exists())

    def test_response_contains_no_secret_value(self):
        self.add_permissions("containerlab_orchestrator.view_containerlabhost", "extras.view_secretsgroup")
        host = ContainerlabHost.objects.get(name="clab01")
        with patch_secret_variables():
            response = self.client.get(f"{self._get_detail_url(host)}?depth=2", **self.header)

        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data["secrets_group"]["id"], str(host.secrets_group.pk))
        self.assertNotIn(SECRET_VALUE, response.content.decode(response.charset))
        self.assert_no_verboten_content(response)


class NodeMappingAPITestCase(APIViewTestCases.APIViewTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot REST API tests and the validation tests on NodeMapping."""

    model = NodeMapping
    choices_fields = ("config_source",)

    @classmethod
    def setUpTestData(cls):
        create_node_mappings()
        arista = Manufacturer.objects.get(name="Arista")
        juniper = Manufacturer.objects.create(name="Juniper")
        cls.free_device_type = DeviceType.objects.create(manufacturer=arista, model="cEOS-lab")
        vmx = DeviceType.objects.create(manufacturer=juniper, model="vMX")
        cls.free_platform = Platform.objects.create(name="frr")
        cls.create_data = [
            {"device_type": cls.free_device_type.pk, "kind": "ceos", "image": "ceos:4.33.0F"},
            {"platform": cls.free_platform.pk, "kind": "linux", "image": "frrouting/frr:v8.4.1"},
            {
                "device_type": vmx.pk,
                "kind": "juniper_vmx",
                "image": "vrnetlab/vr-vmx:22.1",
                "env": {"CLAB_MGMT_VRF": "MGMT"},
                "overrides": {"startup-delay": 30},
            },
        ]
        cls.update_data = {"image": "ceos:4.33.0F", "memory": "4Gb"}
        cls.bulk_update_data = {"config_source": "none"}

    def _post_invalid(self, **data):
        """POST a mapping and return the errors of the HTTP 400 response."""
        self.add_permissions("containerlab_orchestrator.add_nodemapping", "dcim.view_devicetype", "dcim.view_platform")
        payload = {"device_type": self.free_device_type.pk, "kind": "ceos", "image": "ceos:4.33.0F", **data}
        before = NodeMapping.objects.count()
        response = self.client.post(self._get_list_url(), payload, format="json", **self.header)
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(NodeMapping.objects.count(), before)
        return response.data

    def test_both_targets_returns_400(self):
        errors = self._post_invalid(platform=self.free_platform.pk)
        self.assertEqual(errors["__all__"], ["Set a Device Type or a Platform, but not both."])

    def test_no_target_returns_400(self):
        errors = self._post_invalid(device_type=None)
        self.assertEqual(errors["__all__"], ["Set a Device Type or a Platform."])

    def test_duplicate_device_type_returns_400(self):
        errors = self._post_invalid(device_type=DeviceType.objects.get(model="DCS-7280").pk)
        self.assertIn("device_type", errors)

    def test_env_and_overrides_that_are_not_string_maps_return_400(self):
        for field, value in (("env", []), ("env", {"A": 1}), ("overrides", "text")):
            with self.subTest(field=field, value=value):
                self.assertIn(field, self._post_invalid(**{field: value}))

    def test_null_env_returns_400(self):
        errors = self._post_invalid(env=None)
        self.assertEqual(errors["env"], ["This field may not be null."])

    def test_invalid_kind_image_cpu_and_memory_return_400(self):
        for field, value in (("kind", "Ceos"), ("image", "a b"), ("cpu", "0"), ("memory", "512")):
            with self.subTest(field=field, value=value):
                self.assertIn(field, self._post_invalid(**{field: value}))

    def test_invalid_config_template_returns_400(self):
        for data in ({"config_source": "jinja2"}, {"config_template": "{% if %}"}):
            with self.subTest(data=data):
                self.assertIn("config_template", self._post_invalid(**data))


class InterfaceMappingAPITestCase(APIViewTestCases.APIViewTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot REST API tests and the validation tests on InterfaceMapping."""

    model = InterfaceMapping
    choices_fields = ()

    @classmethod
    def setUpTestData(cls):
        node_mappings = create_node_mappings()
        create_interface_mappings(node_mappings)
        cls.arista, cls.nokia, _ = node_mappings
        cls.create_data = [
            {"node_mapping": cls.nokia.pk, "physical_name": f"ethernet-1/{number}", "container_name": f"e1-{number}"}
            for number in range(1, 4)
        ]
        cls.update_data = {"container_name": "e1-9"}

    def _post_invalid(self, **data):
        """POST a row on the Arista mapping and return the errors of the HTTP 400 response."""
        self.add_permissions(
            "containerlab_orchestrator.add_interfacemapping", "containerlab_orchestrator.view_nodemapping"
        )
        payload = {"node_mapping": self.arista.pk, "physical_name": "GigabitEthernet0/3", "container_name": "eth3"}
        payload.update(data)
        before = InterfaceMapping.objects.count()
        response = self.client.post(self._get_list_url(), payload, format="json", **self.header)
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(InterfaceMapping.objects.count(), before)
        return response.data

    def test_duplicate_physical_name_returns_400(self):
        errors = self._post_invalid(physical_name="GigabitEthernet0/1")
        self.assertEqual(list(errors), ["__all__"])

    def test_duplicate_container_name_returns_400(self):
        errors = self._post_invalid(container_name="eth1")
        self.assertEqual(list(errors), ["__all__"])

    def test_bad_container_name_returns_400(self):
        self.assertIn("container_name", self._post_invalid(container_name="eth 1"))

    def test_physical_name_with_spaces_at_start_and_end_is_stored_trimmed(self):
        self.add_permissions(
            "containerlab_orchestrator.add_interfacemapping", "containerlab_orchestrator.view_nodemapping"
        )
        payload = {"node_mapping": self.arista.pk, "physical_name": "Gi0/1 ", "container_name": "eth3"}
        response = self.client.post(self._get_list_url(), payload, format="json", **self.header)
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertEqual(InterfaceMapping.objects.get(pk=response.data["id"]).physical_name, "Gi0/1")

    def test_get_absolute_url_for_api_gives_row_url(self):
        row = InterfaceMapping.objects.get(physical_name="ens3")
        self.assertEqual(row.get_absolute_url(api=True), self._get_detail_url(row))

    def test_list_hides_ordering_field(self):
        self.add_permissions("containerlab_orchestrator.view_interfacemapping")
        response = self.client.get(self._get_list_url(), **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertNotIn("_physical_name", response.data["results"][0])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_recreate_platform_row_from_csv(self):
        self.add_permissions(
            "containerlab_orchestrator.add_interfacemapping", "containerlab_orchestrator.view_interfacemapping"
        )
        row = InterfaceMapping.objects.get(physical_name="ens3")
        platform_mapping = row.node_mapping
        response = self.client.get(f"{self._get_detail_url(row)}?format=csv", **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        csv_data = response.content.decode(response.charset)
        row.delete()

        response = self.client.post(self._get_list_url(), csv_data, content_type="text/csv", **self.header)

        self.assertHttpStatus(response, status.HTTP_201_CREATED, csv_data)
        new_row = InterfaceMapping.objects.get(pk=response.data[0]["id"])
        self.assertEqual(
            (new_row.node_mapping, new_row.physical_name, new_row.container_name),
            (platform_mapping, "ens3", "eth1"),
        )


class LabAPITestCase(APIViewTestCases.APIViewTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot REST API tests and the owner, status, and validation tests on Lab."""

    model = Lab
    choices_fields = ("config_source",)

    @classmethod
    def setUpTestData(cls):
        cls.hosts = create_containerlab_hosts()
        cls.lab_devices = create_lab_devices()
        cls.labs = create_labs(cls.hosts, cls.lab_devices)
        clab03 = cls.hosts[2].pk
        cls.create_data = [
            {"name": "api-lab-1", "host": clab03, "mgmt_subnet": "192.168.10.0/24"},
            {
                "name": "api-lab-2",
                "host": clab03,
                "mgmt_subnet": "192.168.11.0/24",
                "device_filter": {"role": ["spine"]},
            },
            {"name": "api-lab-3", "host": clab03, "mgmt_subnet": "192.168.12.0/24", "config_source": "jinja2"},
        ]
        cls.update_data = {"mgmt_subnet": "192.168.20.0/24", "config_source": "jinja2"}
        cls.bulk_update_data = {"config_source": "none"}

    def _post_lab(self, expected_status, **data):
        """POST a lab on clab01 with the given fields, check the HTTP status, and return the response data."""
        self.add_permissions(
            "containerlab_orchestrator.add_lab",
            "containerlab_orchestrator.view_containerlabhost",
            "dcim.view_device",
            "extras.view_dynamicgroup",
        )
        payload = {"host": self.hosts[0].pk, "mgmt_subnet": "192.168.30.0/24", **data}
        response = self.client.post(self._get_list_url(), payload, format="json", **self.header)
        self.assertHttpStatus(response, expected_status)
        return response.data

    def test_create_sets_owner_and_planned_status(self):
        bob = get_user_model().objects.get(username="bob")
        data = self._post_lab(
            status.HTTP_201_CREATED, name="api-owner", owner=bob.pk, status=Status.objects.get(name="Built").pk
        )

        lab = Lab.objects.get(pk=data["id"])
        self.assertEqual(lab.owner, self.user)
        self.assertEqual(lab.status.name, "Planned")
        change = ObjectChange.objects.filter(changed_object_id=lab.pk).first()
        self.assertEqual(set(change.object_data_v2["owner"]), OWNER_KEYS)

    def test_update_keeps_owner_and_status(self):
        self.add_permissions("containerlab_orchestrator.change_lab", "containerlab_orchestrator.view_lab")
        lab_b = self.labs[1]
        data = {"name": "lab-b2", "owner": self.user.pk, "status": Status.objects.get(name="Deployed").pk}

        response = self.client.patch(self._get_detail_url(lab_b), data, format="json", **self.header)

        self.assertHttpStatus(response, status.HTTP_200_OK)
        lab_b.refresh_from_db()
        self.assertEqual(lab_b.name, "lab-b2")
        self.assertEqual(lab_b.owner.username, "bob")
        self.assertEqual(lab_b.status.name, "Built")

    def test_create_with_devices_and_dynamic_group(self):
        data = self._post_lab(
            status.HTTP_201_CREATED,
            name="api-selection",
            devices=[self.lab_devices["leaf1"].pk, self.lab_devices["leaf2"].pk],
            dynamic_group=self.lab_devices["leaf_group"].pk,
        )

        lab = Lab.objects.get(pk=data["id"])
        self.assertEqual(set(lab.devices.values_list("name", flat=True)), {"leaf1", "leaf2"})
        self.assertEqual(lab.dynamic_group, self.lab_devices["leaf_group"])

    def test_duplicate_name_returns_400(self):
        errors = self._post_lab(status.HTTP_400_BAD_REQUEST, name="LAB-A")
        self.assertEqual(errors, {"name": ["A lab with this name already exists on this host."]})

        self._post_lab(status.HTTP_201_CREATED, name="lab-b", host=self.hosts[1].pk)

    def test_bad_name_subnet_filter_and_group_return_400(self):
        location_group = DynamicGroup.objects.create(
            name="Lab locations", content_type=ContentType.objects.get_for_model(Location)
        )
        for field, value, message in (
            ("name", "lab a", 'Use 1 to 128 letters, digits, "_", or "-".'),
            ("mgmt_subnet", "172.20.21.1/24", CIDR_MESSAGE),
            ("mgmt_subnet", "10.0.0.0/31", SIZE_MESSAGE),
            ("device_filter", ["leaf"], 'Enter a JSON object, for example {"key": "value"}.'),
            ("dynamic_group", location_group.pk, "Select a Dynamic Group of devices."),
        ):
            with self.subTest(field=field, value=value):
                errors = self._post_lab(status.HTTP_400_BAD_REQUEST, **{"name": "api-bad", field: value})
                self.assertEqual(errors, {field: [message]})

    def test_owner_shows_no_user_data_except_the_username(self):
        """Nautobot 3.2 and later also drop `username` for a user who cannot view users."""
        self.add_permissions("containerlab_orchestrator.view_lab")
        for depth in (0, 1, 2):
            with self.subTest(depth=depth):
                response = self.client.get(f"{self._get_detail_url(self.labs[0])}?depth={depth}", **self.header)
                self.assertHttpStatus(response, status.HTTP_200_OK)
                self.assertLessEqual({"id", "object_type", "url"}, set(response.data["owner"]))
                self.assertLessEqual(set(response.data["owner"]), OWNER_KEYS)

    def test_create_outside_the_rest_api_fails(self):
        """Nautobot 3.2 and later send the job user to the CSV import in a request that is not a REST API request."""
        self.add_permissions("containerlab_orchestrator.view_containerlabhost")
        request = RequestFactory().post("/")
        request.user = self.user
        data = {"name": "import-lab", "host": self.hosts[0].pk, "mgmt_subnet": "192.168.40.0/24"}

        serializer = LabSerializer(data=data, context={"request": request})

        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors, {"non_field_errors": ["Use the add form or the REST API to add a lab."]})


class LabNodeAPITestCase(  # pylint: disable=too-many-ancestors
    APIViewTestCases.GetObjectViewTestCase, APIViewTestCases.ListObjectsViewTestCase
):
    """Run the generic Nautobot read tests and the read-only tests on LabNode."""

    model = LabNode

    @classmethod
    def setUpTestData(cls):
        lab_devices = create_lab_devices()
        cls.labs = create_labs(create_containerlab_hosts(), lab_devices)
        create_lab_nodes(cls.labs, lab_devices)

    def test_write_returns_405(self):
        self.add_permissions(
            "containerlab_orchestrator.add_labnode",
            "containerlab_orchestrator.change_labnode",
            "containerlab_orchestrator.delete_labnode",
        )
        node = LabNode.objects.filter(node_name="leaf1").first()
        payload = {"lab": self.labs[0].pk, "node_name": "new", "kind": "linux", "image": "alpine:latest"}

        responses = (
            self.client.post(self._get_list_url(), payload, format="json", **self.header),
            self.client.patch(self._get_detail_url(node), {"state": "exited"}, format="json", **self.header),
            self.client.delete(self._get_detail_url(node), **self.header),
        )

        for response in responses:
            self.assertHttpStatus(response, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_filter_by_lab_stub_and_device(self):
        self.add_permissions("containerlab_orchestrator.view_labnode")
        for query, expected in (
            (f"lab={self.labs[0].pk}", ["leaf1", "spine1", "stub-wan1"]),
            ("is_stub=true", ["stub-wan1"]),
            ("device=leaf1", ["leaf1", "leaf1"]),
        ):
            with self.subTest(query=query):
                response = self.client.get(f"{self._get_list_url()}?{query}", **self.header)
                self.assertHttpStatus(response, status.HTTP_200_OK)
                self.assertEqual(sorted(row["node_name"] for row in response.data["results"]), expected)

    def test_get_absolute_url_for_api_gives_row_url(self):
        node = LabNode.objects.get(node_name="stub-wan1")
        self.assertTrue(node.get_absolute_url(api=True).endswith(f"/lab-nodes/{node.pk}/"))


class LabCsvImportJobTestCase(TransactionTestCase):
    """Run the CSV import system job on Lab.

    The job writes its log through a second database connection, so this test commits its data.
    """

    def test_csv_import_rejects_each_lab_row(self):
        clab01 = create_containerlab_hosts()[0]
        csv_data = f"name,host,mgmt_subnet\nimport-lab,{clab01.pk},192.168.40.0/24"

        job_result = create_job_result_and_run_job(
            "nautobot.core.jobs",
            "ImportObjects",
            content_type=ContentType.objects.get_for_model(Lab).pk,
            csv_data=csv_data,
        )

        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertIn(
            "Row 1: `non_field_errors`: `Use the add form or the REST API to add a lab.`",
            " ".join(job_result.job_log_entries.values_list("message", flat=True)),
        )
        self.assertFalse(Lab.objects.filter(name="import-lab").exists())
