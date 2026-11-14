"""Tests for the filter sets of the app."""

from nautobot.apps.testing import FilterTestCases
from nautobot.dcim.models import Platform

from containerlab_orchestrator.filters import ContainerlabHostFilterSet, InterfaceMappingFilterSet, NodeMappingFilterSet
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_interface_mappings,
    create_node_mappings,
)


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


class InterfaceMappingFilterTestCase(FilterTestCases.FilterTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot filter tests on InterfaceMappingFilterSet."""

    queryset = InterfaceMapping.objects.all()
    filterset = InterfaceMappingFilterSet
    generic_filter_tests = (
        ["physical_name"],
        ["container_name"],
        ["node_mapping", "node_mapping__id"],
    )

    @classmethod
    def setUpTestData(cls):
        node_mappings = create_node_mappings()
        create_interface_mappings(node_mappings)
        InterfaceMapping.objects.create(
            node_mapping=node_mappings[1], physical_name="ethernet-1/1", container_name="e1-1"
        )

    def test_q_matches_physical_and_container_name(self):
        for value, expected in (("gigabitethernet0/10", "GigabitEthernet0/10"), ("ETH10", "GigabitEthernet0/10")):
            with self.subTest(value=value):
                result = self.filterset({"q": value}, self.queryset).qs
                self.assertQuerySetEqual(result, self.queryset.filter(physical_name=expected))

    def test_node_mapping_returns_rows_of_that_mapping_only(self):
        linux = NodeMapping.objects.get(platform__name="linux")
        result = self.filterset({"node_mapping": [linux.pk]}, self.queryset).qs
        self.assertQuerySetEqual(result, self.queryset.filter(physical_name="ens3"))

    def test_created_and_last_updated_are_filters_and_ordering_field_is_not(self):
        filters = self.filterset.get_filters()
        for name in ("created", "created__gte", "last_updated", "last_updated__gte"):
            with self.subTest(name=name):
                self.assertIn(name, filters)
        self.assertNotIn("_physical_name", filters)
