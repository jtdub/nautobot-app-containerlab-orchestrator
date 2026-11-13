"""Tests for the Node Mappings panels on the Device Type and Platform pages."""

from django.test import override_settings
from django.urls import reverse
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import DeviceType, Platform

from containerlab_orchestrator.models import NodeMapping
from containerlab_orchestrator.tests.fixtures import create_node_mappings

NODE_MAPPING_ADD_URL_NAME = "plugins:containerlab_orchestrator:nodemapping_add"


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class NodeMappingPanelTestCase(TestCase):
    """Test the Node Mappings panel on the Device Type and Platform detail pages."""

    @classmethod
    def setUpTestData(cls):
        create_node_mappings()
        cls.device_type = DeviceType.objects.get(model="DCS-7280")
        cls.platform = Platform.objects.get(name="linux")
        cls.free_platform = Platform.objects.create(name="frr")

    def _add_link(self, field_name, obj):
        return f"{reverse(NODE_MAPPING_ADD_URL_NAME)}?{field_name}={obj.pk}"

    @staticmethod
    def _panel_row_link(mapping):
        return f'<a href="{mapping.get_absolute_url()}">{mapping.kind}</a>'

    def test_device_type_page_shows_mapping_and_add_link(self):
        self.add_permissions("dcim.view_devicetype", "containerlab_orchestrator.view_nodemapping")
        self.add_permissions("containerlab_orchestrator.add_nodemapping")
        mapping = NodeMapping.objects.get(device_type=self.device_type)

        response = self.client.get(self.device_type.get_absolute_url())

        self.assertBodyContains(response, "<strong>Node Mappings</strong>", html=True)
        self.assertBodyContains(response, self._panel_row_link(mapping), html=True)
        self.assertBodyContains(response, self._add_link("device_type", self.device_type))

    def test_platform_page_shows_mapping_and_add_link(self):
        self.add_permissions("dcim.view_platform", "containerlab_orchestrator.view_nodemapping")
        self.add_permissions("containerlab_orchestrator.add_nodemapping")
        mapping = NodeMapping.objects.get(platform=self.platform)

        response = self.client.get(self.platform.get_absolute_url())

        self.assertBodyContains(response, "<strong>Node Mappings</strong>", html=True)
        self.assertBodyContains(response, self._panel_row_link(mapping), html=True)
        self.assertBodyContains(response, self._add_link("platform", self.platform))

    def test_add_link_opens_form_with_platform_selected(self):
        self.add_permissions("dcim.view_platform", "containerlab_orchestrator.add_nodemapping")

        response = self.client.get(self._add_link("platform", self.free_platform))

        self.assertBodyContains(response, f'<option value="{self.free_platform.pk}" selected>frr</option>', html=True)

    def test_add_link_opens_form_with_device_type_selected(self):
        self.add_permissions("dcim.view_devicetype", "containerlab_orchestrator.add_nodemapping")

        response = self.client.get(self._add_link("device_type", self.device_type))

        self.assertBodyContains(response, f'<option value="{self.device_type.pk}" selected>DCS-7280</option>', html=True)

    def test_panel_hides_mapping_without_view_permission(self):
        self.add_permissions("dcim.view_devicetype")
        mapping = NodeMapping.objects.get(device_type=self.device_type)

        response = self.client.get(self.device_type.get_absolute_url())

        self.assertHttpStatus(response, 200)
        self.assertNotContains(response, self._panel_row_link(mapping), html=True)

    def test_panel_hides_add_link_without_add_permission(self):
        self.add_permissions("dcim.view_platform", "containerlab_orchestrator.view_nodemapping")

        response = self.client.get(self.platform.get_absolute_url())

        self.assertBodyContains(response, self._panel_row_link(NodeMapping.objects.get(platform=self.platform)), html=True)
        self.assertNotContains(response, self._add_link("platform", self.platform))
