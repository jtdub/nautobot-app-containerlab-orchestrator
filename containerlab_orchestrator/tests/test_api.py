"""Tests for the REST API of the app."""

from django.test import override_settings
from nautobot.apps.testing import APIViewTestCases
from nautobot.dcim.models import DeviceType, Manufacturer, Platform
from rest_framework import status

from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    SECRET_VALUE,
    create_containerlab_hosts,
    create_http_secrets_group,
    create_interface_mappings,
    create_node_mappings,
    patch_secret_variables,
)


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
