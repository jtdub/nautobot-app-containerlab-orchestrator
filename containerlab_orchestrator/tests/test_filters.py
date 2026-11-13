"""Tests for the filter sets of the app."""

from nautobot.apps.testing import FilterTestCases
from nautobot.dcim.models import Platform

from containerlab_orchestrator.filters import ContainerlabHostFilterSet, NodeMappingFilterSet
from containerlab_orchestrator.models import ContainerlabHost, NodeMapping
from containerlab_orchestrator.tests.fixtures import create_containerlab_hosts, create_node_mappings


class ContainerlabHostFilterTestCase(FilterTestCases.FilterTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot filter tests on ContainerlabHostFilterSet."""

    queryset = ContainerlabHost.objects.all()
    filterset = ContainerlabHostFilterSet
    generic_filter_tests = (
        ["name"],
        ["remote_url"],
        ["description"],
        ["secrets_group", "secrets_group__name"],
        ["secrets_group", "secrets_group__id"],
    )

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()

    def test_q_matches_name_url_and_description(self):
        for value in ("CLAB02", "clab02.example.com", "server 2"):
            with self.subTest(value=value):
                result = self.filterset({"q": value}, self.queryset).qs
                self.assertQuerySetEqual(result, self.queryset.filter(name="clab02"))

    def test_verify_ssl(self):
        result = self.filterset({"verify_ssl": False}, self.queryset).qs
        self.assertQuerySetEqual(result, self.queryset.filter(name="clab03"))


class NodeMappingFilterTestCase(FilterTestCases.FilterTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot filter tests on NodeMappingFilterSet."""

    queryset = NodeMapping.objects.all()
    filterset = NodeMappingFilterSet
    generic_filter_tests = (
        ["kind"],
        ["image"],
        ["memory"],
        ["device_type", "device_type__model"],
        ["device_type", "device_type__id"],
        ["platform", "platform__name"],
        ["platform", "platform__id"],
    )

    @classmethod
    def setUpTestData(cls):
        create_node_mappings()
        NodeMapping.objects.create(
            platform=Platform.objects.create(name="edge-router"),
            kind="linux",
            image="frrouting/frr:v8.4.1",
            memory="1Gb",
        )

    def test_q_matches_kind_image_device_type_and_platform(self):
        for value, expected in (
            ("NOKIA_SRL", {"kind": "nokia_srlinux"}),
            ("srlinux:24", {"kind": "nokia_srlinux"}),
            ("IXR-D3", {"kind": "nokia_srlinux"}),
            ("IXRD3", {"kind": "nokia_srlinux"}),
            ("dcs-7280", {"kind": "ceos"}),
            ("EDGE-ROUTER", {"platform__name": "edge-router"}),
        ):
            with self.subTest(value=value):
                result = self.filterset({"q": value}, self.queryset).qs
                self.assertQuerySetEqual(result, self.queryset.filter(**expected))

    def test_config_source(self):
        result = self.filterset({"config_source": ["none"]}, self.queryset).qs
        self.assertQuerySetEqual(result, self.queryset.filter(device_type__model="7220 IXR-D3"))
