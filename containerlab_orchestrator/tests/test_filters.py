"""Tests for the ContainerlabHost filter set."""

from nautobot.apps.testing import FilterTestCases

from containerlab_orchestrator.filters import ContainerlabHostFilterSet
from containerlab_orchestrator.models import ContainerlabHost
from containerlab_orchestrator.tests.fixtures import create_containerlab_hosts


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
