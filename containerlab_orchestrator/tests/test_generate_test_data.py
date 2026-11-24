"""Tests of the generate_containerlab_orchestrator_test_data management command."""

import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

import yaml
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from nautobot.apps.testing import TestCase
from nautobot.extras.models import DynamicGroup, GitRepository, GraphQLQuery
from nautobot.dcim.models import Cable, Device, DeviceType, Location
from nautobot.ipam.models import VRF, IPAddress, Prefix

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices, NodeMappingConfigSourceChoices
from containerlab_orchestrator.config_sources import GOLDEN_CONFIG_APP
from containerlab_orchestrator.management.commands import generate_containerlab_orchestrator_test_data as demo
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, NodeMapping
from containerlab_orchestrator.selection import select_devices

ALL_NAMES = sorted(demo.SPINE_NAMES + demo.LEAF_NAMES + demo.SERVER_NAMES)
LINK_COUNT = len(demo.SPINE_NAMES) * len(demo.LEAF_NAMES) + len(demo.SERVER_NAMES)
ARISTA_NAMES = sorted(demo.SPINE_NAMES + demo.LEAF_NAMES)


def backup_text(device_name):
    """Return the fake backup config of the device in the demo repository."""
    return f"hostname {device_name}\ninterface Ethernet49\n   no switchport\n"


def write_backups(repository, *args, **kwargs):  # pylint: disable=unused-argument
    """Write a backup file for each Arista device, as a clone of the demo repository gives."""
    configs = Path(repository.filesystem_path) / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    for name in ARISTA_NAMES:
        (configs / f"{name}.cfg").write_text(backup_text(name))


def run_command(*args):
    """Run the command with the arguments, and return its output."""
    output = StringIO()
    call_command("generate_containerlab_orchestrator_test_data", *args, stdout=output)
    return output.getvalue()


def interface_addresses(device_name, interface_name):
    """Return the addresses of the interface as strings."""
    interface = Device.objects.get(name=device_name).interfaces.get(name=interface_name)
    return [str(ip_address.address) for ip_address in interface.ip_addresses.all()]


def demo_prefixes():
    """Return the prefixes of the demo location."""
    return Prefix.objects.filter(locations__name=demo.LOCATION_NAME)


class DemoDataTestCase(TestCase):
    """Base class that replaces the clone of the demo repository and the sync job, so no test uses the network."""

    def setUp(self):
        """Use a temporary Git root, and replace the clone and the sync job of the demo repository."""
        super().setUp()
        git_root = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(git_root.cleanup)
        self.enterContext(override_settings(GIT_ROOT=git_root.name))
        self.clone = self.enterContext(mock.patch.object(demo, "ensure_git_repository", side_effect=write_backups))
        self.sync = self.enterContext(mock.patch.object(GitRepository, "sync"))

    def run_and_commit(self, *args):
        """Run the command, run its on-commit work, and return the output."""
        output = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("generate_containerlab_orchestrator_test_data", *args, stdout=output)
        return output.getvalue()

    def demo_lab(self):
        """Return the demo lab."""
        return Lab.objects.get(name=demo.LAB_NAME, host__name=demo.HOST_NAME)


class GenerateTestDataTestCase(DemoDataTestCase):
    """Test the demo data of one location with an Arista spine and leaf fabric and Linux hosts."""

    def test_command_creates_spines_leafs_and_linux_hosts_at_one_location(self):
        run_command()
        devices = Device.objects.filter(location__name=demo.LOCATION_NAME)
        self.assertEqual(sorted(devices.values_list("name", flat=True)), ALL_NAMES)
        self.assertEqual(
            devices.filter(role__name=demo.SPINE_ROLE, device_type__manufacturer__name="Arista").count(), 2
        )
        self.assertEqual(devices.filter(role__name=demo.LEAF_ROLE, device_type__manufacturer__name="Arista").count(), 4)
        self.assertEqual(devices.filter(role__name=demo.SERVER_ROLE, platform__name="Linux").count(), 4)

    def test_command_cables_each_leaf_to_each_spine_and_to_its_linux_host(self):
        run_command()
        self.assertEqual(Cable.objects.count(), LINK_COUNT)
        leaf = Device.objects.get(name="dc1-leaf3")
        uplink_peers = {
            peer.device.name
            for name in ("Ethernet49", "Ethernet50")
            for peer in leaf.interfaces.get(name=name).get_cable_peers()
        }
        self.assertEqual(uplink_peers, set(demo.SPINE_NAMES))
        server_peers = leaf.interfaces.get(name=demo.LEAF_SERVER_PORT).get_cable_peers()
        self.assertEqual([(peer.device.name, peer.name) for peer in server_peers], [("dc1-host3", "eth1")])

    def test_each_link_gets_a_31_prefix_with_one_address_on_each_end(self):
        run_command()
        self.assertEqual(demo_prefixes().filter(prefix_length=31).count(), LINK_COUNT)
        self.assertEqual(interface_addresses("dc1-spine1", "Ethernet1"), ["10.1.0.0/31"])
        self.assertEqual(interface_addresses("dc1-leaf1", "Ethernet49"), ["10.1.0.1/31"])
        self.assertEqual(interface_addresses("dc1-spine2", "Ethernet4"), ["10.1.0.14/31"])
        self.assertEqual(interface_addresses("dc1-leaf4", "Ethernet50"), ["10.1.0.15/31"])
        self.assertEqual(interface_addresses("dc1-leaf2", "Ethernet1"), ["10.1.1.2/31"])
        self.assertEqual(interface_addresses("dc1-host2", "eth1"), ["10.1.1.3/31"])

    def test_link_prefixes_are_in_container_prefixes_of_the_location(self):
        run_command()
        for container in (demo.FABRIC_PREFIX, demo.SERVER_LINK_PREFIX):
            parent = demo_prefixes().get(prefix=container)
            self.assertEqual(parent.type, "container")
            self.assertTrue(parent.children.filter(prefix_length=31).exists())
        link_prefix = Prefix.objects.get(prefix="10.1.0.2/31")
        self.assertEqual(str(link_prefix.parent.prefix), demo.FABRIC_PREFIX)
        self.assertEqual(IPAddress.objects.get(address="10.1.0.3/31").parent, link_prefix)

    def test_each_management_interface_is_in_the_mgmt_vrf_with_the_primary_ip(self):
        run_command()
        vrf = VRF.objects.get(name=demo.MGMT_VRF_NAME)
        mgmt_prefix = demo_prefixes().get(prefix=demo.MGMT_PREFIX)
        self.assertIn(mgmt_prefix, vrf.prefixes.all())
        for device in Device.objects.filter(location__name=demo.LOCATION_NAME):
            mgmt_interface = device.interfaces.get(mgmt_only=True)
            self.assertEqual(mgmt_interface.vrf, vrf)
            self.assertIn(device, vrf.devices.all())
            self.assertEqual(list(mgmt_interface.ip_addresses.all()), [device.primary_ip4])
            self.assertEqual(device.primary_ip4.parent, mgmt_prefix)
        self.assertEqual(str(Device.objects.get(name="dc1-host1").primary_ip4.address), "10.20.20.2/24")
        self.assertEqual(
            Device.objects.get(name="dc1-spine1").interfaces.get(mgmt_only=True).name, demo.SPINE.mgmt_interface
        )
        self.assertEqual(Device.objects.get(name="dc1-host1").interfaces.get(mgmt_only=True).name, "eth0")

    def test_command_maps_each_model_to_its_containerlab_kind(self):
        run_command()
        for demo_model in demo.DEMO_MODELS:
            mapping = NodeMapping.objects.get(device_type__model=demo_model.model)
            self.assertEqual((mapping.kind, mapping.image), (demo_model.kind, demo_model.image))
        leaf_row = InterfaceMapping.objects.get(
            node_mapping__device_type__model=demo.LEAF.model, physical_name="Ethernet49"
        )
        self.assertEqual(leaf_row.container_name, "eth49")
        server_row = InterfaceMapping.objects.get(node_mapping__device_type__model=demo.SERVER.model)
        self.assertEqual((server_row.physical_name, server_row.container_name), ("eth1", "eth1"))

    def test_command_creates_a_planned_lab_that_selects_the_location_devices(self):
        get_user_model().objects.create_superuser(username="demo-admin")
        run_command()
        lab = self.demo_lab()
        self.assertEqual(lab.status.name, LabStatusChoices.PLANNED)
        self.assertEqual(lab.owner.username, "demo-admin")
        self.assertEqual(lab.host.secrets_group.name, demo.SECRETS_GROUP_NAME)
        self.assertEqual(sorted(device.name for device in select_devices(lab)), ALL_NAMES)

    def test_demo_lab_builds_with_no_warning_and_node_addresses_equal_to_primary_ips(self):
        self.run_and_commit()
        result = build_lab(self.demo_lab())
        self.assertEqual((result.node_count, result.link_count, result.warnings), (len(ALL_NAMES), LINK_COUNT, []))
        nodes = yaml.safe_load(result.lab.topology_yaml)["topology"]["nodes"]
        self.assertEqual(nodes["dc1-host1"]["kind"], "linux")
        self.assertNotIn("startup-config", nodes["dc1-host1"])
        self.assertEqual(nodes["dc1-leaf1"]["startup-config"], backup_text("dc1-leaf1"))
        for device in Device.objects.filter(location__name=demo.LOCATION_NAME):
            self.assertEqual(nodes[device.name]["mgmt-ipv4"], str(device.primary_ip4.host))

    def test_second_run_creates_no_duplicate(self):
        run_command()
        run_command()
        self.assertEqual(Device.objects.filter(location__name=demo.LOCATION_NAME).count(), len(ALL_NAMES))
        self.assertEqual(Cable.objects.count(), LINK_COUNT)
        self.assertEqual(demo_prefixes().count(), LINK_COUNT + 3)
        self.assertEqual(interface_addresses("dc1-leaf1", "Ethernet49"), ["10.1.0.1/31"])
        self.assertEqual(Lab.objects.filter(name=demo.LAB_NAME).count(), 1)

    def test_flush_deletes_the_demo_data_and_loads_it_again(self):
        run_command()
        old_lab_pk = self.demo_lab().pk
        old_vrf_pk = VRF.objects.get(name=demo.MGMT_VRF_NAME).pk
        output = run_command("--flush")
        self.assertIn("Deleting the existing Containerlab Orchestrator demo data", output)
        self.assertNotEqual(self.demo_lab().pk, old_lab_pk)
        self.assertNotEqual(VRF.objects.get(name=demo.MGMT_VRF_NAME).pk, old_vrf_pk)
        self.assertEqual(Device.objects.filter(location__name=demo.LOCATION_NAME).count(), len(ALL_NAMES))
        self.assertEqual(Cable.objects.count(), LINK_COUNT)
        self.assertEqual(demo_prefixes().count(), LINK_COUNT + 3)
        self.assertEqual(IPAddress.objects.count(), 2 * LINK_COUNT + len(ALL_NAMES))
        self.assertEqual(ContainerlabHost.objects.filter(name=demo.HOST_NAME).count(), 1)
        self.assertEqual(DeviceType.objects.filter(model__in=[m.model for m in demo.DEMO_MODELS]).count(), 3)
        self.assertEqual(Location.objects.filter(name=demo.LOCATION_NAME).count(), 1)

    def test_flush_keeps_a_mgmt_vrf_that_other_prefixes_use(self):
        run_command()
        vrf = VRF.objects.get(name=demo.MGMT_VRF_NAME)
        other = Prefix.objects.create(
            prefix="192.0.2.0/24", namespace=vrf.namespace, status=Prefix.objects.first().status
        )
        vrf.add_prefix(other)
        run_command("--flush")
        self.assertEqual(VRF.objects.get(name=demo.MGMT_VRF_NAME).pk, vrf.pk)
        self.assertTrue(Prefix.objects.filter(pk=other.pk).exists())


class GenerateGoldenConfigTestCase(DemoDataTestCase):
    """Test the Golden Config backup repository and the backup records of the demo data."""

    def golden_config_records(self):
        """Return the backup text of each demo Golden Config record, by device name."""
        records = apps.get_model(GOLDEN_CONFIG_APP, "GoldenConfig").objects.filter(
            device__location__name=demo.LOCATION_NAME
        )
        return dict(records.values_list("device__name", "backup_config"))

    def test_command_adds_the_backup_repository_and_the_golden_config_setting(self):
        run_command()
        repository = GitRepository.objects.get(name=demo.REPOSITORY_NAME)
        self.assertEqual((repository.remote_url, repository.branch), (demo.REPOSITORY_URL, "main"))
        self.assertEqual(repository.provided_contents, [demo.BACKUP_CONTENT])
        setting = apps.get_model(GOLDEN_CONFIG_APP, "GoldenConfigSetting").objects.get(name=demo.GOLDEN_CONFIG_NAME)
        self.assertEqual(setting.backup_repository, repository)
        self.assertEqual(setting.backup_path_template, demo.BACKUP_PATH_TEMPLATE)
        self.assertFalse(setting.backup_test_connectivity)
        self.assertEqual(setting.sot_agg_query, GraphQLQuery.objects.get(name=demo.GRAPHQL_QUERY_NAME))
        members = setting.dynamic_group.update_cached_members()
        self.assertEqual(sorted(members.values_list("name", flat=True)), ARISTA_NAMES)

    def test_arista_mappings_use_the_backup_with_the_physical_names(self):
        run_command()
        for demo_model in (demo.SPINE, demo.LEAF):
            mapping = NodeMapping.objects.get(device_type__model=demo_model.model)
            self.assertEqual(mapping.config_source, NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP)
            self.assertFalse(mapping.rename_interfaces)
        server_mapping = NodeMapping.objects.get(device_type__model=demo.SERVER.model)
        self.assertEqual(server_mapping.config_source, "")

    def test_commit_loads_each_backup_and_starts_the_sync_job(self):
        owner = get_user_model().objects.create_superuser(username="demo-admin")
        self.run_and_commit()
        self.clone.assert_called_once_with(GitRepository.objects.get(name=demo.REPOSITORY_NAME))
        self.assertEqual(self.golden_config_records(), {name: backup_text(name) for name in ARISTA_NAMES})
        self.sync.assert_called_once_with(user=owner)

    def test_no_record_or_sync_before_the_commit(self):
        run_command()
        self.clone.assert_not_called()
        self.sync.assert_not_called()
        self.assertEqual(self.golden_config_records(), {})

    def test_failed_clone_warns_and_keeps_the_demo_data(self):
        self.clone.side_effect = RuntimeError("no route to host")
        output = self.run_and_commit()
        self.assertIn(demo.CLONE_FAILED_MESSAGE.format(url=demo.REPOSITORY_URL, error="no route to host"), output)
        self.assertEqual(self.golden_config_records(), {})
        self.assertEqual(Device.objects.filter(location__name=demo.LOCATION_NAME).count(), len(ALL_NAMES))

    def test_missing_backup_file_warns_for_that_device(self):
        def write_without_spine2(repository, *args, **kwargs):
            write_backups(repository)
            (Path(repository.filesystem_path) / "configs" / "dc1-spine2.cfg").unlink()

        self.clone.side_effect = write_without_spine2
        output = self.run_and_commit()
        self.assertIn(demo.MISSING_BACKUP_MESSAGE.format(device="dc1-spine2", path="configs/dc1-spine2.cfg"), output)
        self.assertEqual(sorted(self.golden_config_records()), [name for name in ARISTA_NAMES if name != "dc1-spine2"])

    def test_second_run_updates_the_backups_without_duplicates(self):
        self.run_and_commit()
        self.run_and_commit()
        self.assertEqual(GitRepository.objects.filter(name=demo.REPOSITORY_NAME).count(), 1)
        self.assertEqual(len(self.golden_config_records()), len(ARISTA_NAMES))

    def test_flush_deletes_the_golden_config_data_and_loads_it_again(self):
        self.run_and_commit()
        old_repository_pk = GitRepository.objects.get(name=demo.REPOSITORY_NAME).pk
        self.run_and_commit("--flush")
        self.assertNotEqual(GitRepository.objects.get(name=demo.REPOSITORY_NAME).pk, old_repository_pk)
        self.assertEqual(DynamicGroup.objects.filter(name=demo.DYNAMIC_GROUP_NAME).count(), 1)
        self.assertEqual(GraphQLQuery.objects.filter(name=demo.GRAPHQL_QUERY_NAME).count(), 1)
        self.assertEqual(len(self.golden_config_records()), len(ARISTA_NAMES))

    def test_without_golden_config_the_command_skips_it(self):
        with mock.patch.object(demo, "golden_config_installed", return_value=False):
            output = self.run_and_commit()
        self.assertIn(demo.NO_GOLDEN_CONFIG_MESSAGE, output)
        self.assertFalse(GitRepository.objects.filter(name=demo.REPOSITORY_NAME).exists())
        self.clone.assert_not_called()
        for demo_model in demo.DEMO_MODELS:
            self.assertEqual(NodeMapping.objects.get(device_type__model=demo_model.model).config_source, "")
