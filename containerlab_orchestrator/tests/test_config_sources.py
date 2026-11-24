"""Tests for the startup config sources and the startup config keys of the topology builder."""

import ast
from pathlib import Path
from unittest import mock

import yaml
from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from nautobot.apps.testing import TestCase
from nautobot.apps.utils import render_jinja2
from nautobot.dcim.models import Device

from containerlab_orchestrator import config_sources, topology
from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.config_sources import (
    GOLDEN_CONFIG_APP,
    SHORT_FORMS,
    W_EMPTY_CONFIG,
    W_GOLDEN_CONFIG_NOT_INSTALLED,
    W_NO_GOLDEN_CONFIG,
    W_NO_TEMPLATE,
    W_RENAME_FALLBACK,
    W_TEMPLATE_ERROR,
    W_UNKNOWN_SOURCE,
    rename_interfaces,
    source_of,
)
from containerlab_orchestrator.mapper import DefaultMapping
from containerlab_orchestrator.models import NodeMapping
from containerlab_orchestrator.tests.test_topology import TopologyTestCase, load_clab_schema, new_lab
from containerlab_orchestrator.topology import SRL_KINDS, W_OVERRIDE, build_topology, to_yaml

ONE_LAYER = "{{`{{`}}"
"""The escape of {{ for one template pass."""

TWO_LAYERS = "{{`{{`}}`{{`{{`}}`}}"
"""The escape of {{ for two template passes."""

THREE_LAYERS = "{{`{{`}}`{{`{{`}}`}}`{{`{{`}}`{{`{{`}}`}}`}}"
"""The escape of {{ for three template passes."""

PROBE_CASES = {
    "n1": "hostname r1\nenable secret 9 $9$abc$def\n",
    "n2": "banner ${HOME} $HOME $$ $1 ${1} ${HOME:-x}\n",
    "n3": "x {{ .Env }} {{- 1 -}} {{{ }} }}} {{`a`}}\n",
    "n7": "{{",
    "n8": "a $",
}
"""The probe inputs of plan section 11 that have a $ or a {{."""

SRL_JSON_TEXT = ' \n{"system": {"banner": "${HOME} {{ x }}"}}\n'
"""An SR Linux JSON config, which containerlab reads with one more env and template pass."""


containerlab_text = topology._containerlab_text  # pylint: disable=protected-access


def lab_with_source(source):
    """Return an unsaved lab with the default config source."""
    lab = new_lab()
    lab.config_source = source
    return lab


class ContainerlabTextTestCase(TestCase):
    """Test the escape of a startup config for each kind group."""

    def test_containerlab_text_escapes_for_each_kind_group(self):
        groups = (
            ("ceos", "$$$$", TWO_LAYERS),
            ("cisco_iol", "$$", TWO_LAYERS),
            ("cisco_vios", "$$", ONE_LAYER),
            ("arrcus_arcos", "$$", ONE_LAYER),
            ("srl", "$$$$", TWO_LAYERS),
            ("nokia_srlinux", "$$$$", TWO_LAYERS),
            ("nokia_srsim", "$" * 8, THREE_LAYERS),
            ("nokia_sros", "$$$$", TWO_LAYERS),
            ("NOKIA_SRSIM", "$" * 8, THREE_LAYERS),
        )
        for kind, dollar, brace in groups:
            for case, text in PROBE_CASES.items():
                if kind in SRL_KINDS and case == "n7":
                    continue
                with self.subTest(kind=kind, case=case):
                    expected = text.replace("$", dollar).replace("{{", brace)
                    self.assertEqual(containerlab_text(text, kind), expected.rstrip("\n") + "\n")
        for kind in SRL_KINDS:
            with self.subTest(kind=kind, case="json"):
                expected = SRL_JSON_TEXT.replace("$", "$" * 8).replace("{{", THREE_LAYERS)
                self.assertEqual(containerlab_text(SRL_JSON_TEXT, kind), expected)
                self.assertEqual(containerlab_text(PROBE_CASES["n7"], kind), f"{THREE_LAYERS}\n")

    def test_containerlab_text_adds_final_newline(self):
        for text in ("/etc/passwd", "https://example.com/config.txt", "hostname r1"):
            with self.subTest(text=text):
                self.assertEqual(containerlab_text(text, "linux"), f"{text}\n")
        self.assertEqual(containerlab_text("hostname r1\n", "linux"), "hostname r1\n")


APP_DIR = Path(config_sources.__file__).resolve().parent
"""The directory of the app package."""

INTENDED = NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED
"""The Golden Config intended config source."""

BACKUP = NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP
"""The Golden Config backup config source."""

GOLDEN_CONFIG_TEXT = "interface GigabitEthernet0/1\n description to Gi0/2\ninterface Gi0/10\n"
"""A Golden Config text with the names of the Arista interface table."""

RENAMED_TEXT = "interface eth1\n description to eth2\ninterface eth10\n"
"""The Golden Config text after the rename with the Arista interface table."""


class RenameInterfacesTestCase(TestCase):
    """Test the rename of the physical interface names in a Golden Config text."""

    def test_rename_changes_full_and_short_name(self):
        text = "interface GigabitEthernet0/0/1\n description to Gi0/0/1\n"
        renamed = rename_interfaces(text, {"GigabitEthernet0/0/1": "eth1"})
        self.assertEqual(renamed, "interface eth1\n description to eth1\n")

    def test_rename_leaves_longer_number_unchanged(self):
        text = "interface GigabitEthernet0/0/10\ninterface Gi0/0/10\n"
        self.assertEqual(rename_interfaces(text, {"GigabitEthernet0/0/1": "eth1"}), text)

    def test_rename_ignores_names_inside_longer_names(self):
        text = "Ethernet1/1 TenGigabitEthernet0/0/1 xEthernet1 Ethernet1_a Et1/1 Gi0/0/1/2"
        self.assertEqual(rename_interfaces(text, {"Ethernet1": "eth1", "GigabitEthernet0/0/1": "eth2"}), text)
        for text, rename_map in (("2/1/1/1", {"1/1/1": "e1"}), ("xe-0/0/0:1", {"xe-0/0/0": "eth1"})):
            with self.subTest(text=text):
                self.assertEqual(rename_interfaces(text, rename_map), text)

    def test_rename_changes_only_the_first_name_of_a_range(self):
        rename_map = {"GigabitEthernet0/1": "eth1", "GigabitEthernet0/4": "eth4"}
        renamed = rename_interfaces("interface range Gi0/1-Gi0/4\n", rename_map)
        self.assertEqual(renamed, "interface range eth1-Gi0/4\n")

    def test_rename_handles_each_short_form(self):
        for prefix, short in SHORT_FORMS.items():
            with self.subTest(prefix=prefix):
                renamed = rename_interfaces(f"{prefix}1/1 {short}1/1", {f"{prefix}1/1": "eth1"})
                self.assertEqual(renamed, "eth1 eth1")
        self.assertEqual(len(SHORT_FORMS), 8)
        self.assertEqual(set(SHORT_FORMS.values()), {"Gi", "Te", "Fa", "Et", "Hu", "Po"})

    def test_rename_runs_one_pass_longest_first(self):
        swapped = rename_interfaces("Ethernet1 Ethernet2 Et1", {"Ethernet1": "Ethernet2", "Ethernet2": "Ethernet1"})
        self.assertEqual(swapped, "Ethernet2 Ethernet1 Ethernet2")
        rename_map = {"GigabitEthernet0/1": "eth1", "GigabitEthernet0/1.100": "eth5"}
        renamed = rename_interfaces("GigabitEthernet0/1.100 Gi0/1.100 GigabitEthernet0/1.200", rename_map)
        self.assertEqual(renamed, "eth5 eth5 eth1.200")

    def test_rename_full_name_wins_and_shared_short_form_is_dropped(self):
        text = "Te0/1 TenGigE0/1 TenGigabitEthernet0/1 Gi1 GigabitEthernet1"
        expected = "Te0/1 eth2 eth1 eth3 eth4"
        pairs = [
            ("TenGigabitEthernet0/1", "eth1"),
            ("TenGigE0/1", "eth2"),
            ("Gi1", "eth3"),
            ("GigabitEthernet1", "eth4"),
        ]
        for rename_map in (dict(pairs), dict(reversed(pairs))):
            with self.subTest(order=list(rename_map)):
                self.assertEqual(rename_interfaces(text, rename_map), expected)

    def test_rename_changes_subinterface_parent(self):
        renamed = rename_interfaces(
            "interface Gi0/0/1.100\ninterface GigabitEthernet0/0/1.200\n", {"GigabitEthernet0/0/1": "e1"}
        )
        self.assertEqual(renamed, "interface e1.100\ninterface e1.200\n")

    def test_rename_is_case_sensitive(self):
        text = "gigabitethernet0/0/1 gi0/0/1 GI0/0/1"
        self.assertEqual(rename_interfaces(text, {"GigabitEthernet0/0/1": "eth1"}), text)
        self.assertEqual(rename_interfaces(text, {}), text)


class StartupConfigTestCase(TopologyTestCase):
    """Base class with the helper that sets the startup config fields of the Arista mapping."""

    def set_arista_config(self, template="", source=NodeMappingConfigSourceChoices.JINJA2):
        """Set the config template and the config source of the Arista mapping row."""
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(config_source=source, config_template=template)


class StartupConfigSourceTestCase(StartupConfigTestCase):  # pylint: disable=too-many-ancestors
    """Test the source order and the Jinja2 source of the startup config."""

    def nodes_and_warnings(self, devices, source=NodeMappingConfigSourceChoices.NONE, user=None):
        """Return the nodes and the warnings of a topology of the devices with the lab default source."""
        built, warnings, _ = build_topology(lab_with_source(source), devices, user)
        return built["topology"]["nodes"], warnings

    def test_mapping_source_wins_over_lab_source(self):
        self.set_arista_config("hostname {{ device.name }}")
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertEqual(nodes["arista1"]["startup-config"], "hostname arista1\n")
        self.assertEqual(warnings, [])
        self.set_arista_config("hostname {{ device.name }}", NodeMappingConfigSourceChoices.NONE)
        nodes, warnings = self.nodes_and_warnings([self.arista1], NodeMappingConfigSourceChoices.JINJA2)
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(warnings, [])

    def test_empty_mapping_source_uses_lab_source(self):
        self.set_arista_config("hostname {{ device.name }}", "")
        nodes, _ = self.nodes_and_warnings([self.arista1], NodeMappingConfigSourceChoices.JINJA2)
        self.assertEqual(nodes["arista1"]["startup-config"], "hostname arista1\n")
        default_mapping = DefaultMapping(kind="linux", image="alpine:latest")
        for lab_source, expected in (
            (NodeMappingConfigSourceChoices.JINJA2, NodeMappingConfigSourceChoices.JINJA2),
            ("", NodeMappingConfigSourceChoices.NONE),
        ):
            with self.subTest(lab_source=lab_source):
                self.assertEqual(source_of(default_mapping, lab_with_source(lab_source)), expected)
        nodes, warnings = self.nodes_and_warnings([self.arista1, self.leaf1], "")
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertNotIn("startup-config", nodes["leaf1"])
        self.assertEqual(warnings, [])

    def test_none_source_gives_no_startup_config_keys(self):
        self.set_arista_config("hostname {{ device.name }}", NodeMappingConfigSourceChoices.NONE)
        nodes, warnings = self.nodes_and_warnings([self.arista1, self.leaf1, self.nokia1])
        for node in nodes.values():
            self.assertNotIn("startup-config", node)
            self.assertNotIn("enforce-startup-config", node)
        self.assertEqual(warnings, [])

    def test_jinja2_source_renders_device_lab_and_interface_map(self):
        self.set_arista_config(
            "hostname {{ device.name }}\nlab {{ lab.name }}\nserial {{ device.serial }}\n"
            "{% for name, container in interface_map|dictsort %}{{ name }}={{ container }}\n{% endfor %}",
        )
        self.arista1.serial = "a&b<c>"
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertEqual(
            nodes["arista1"]["startup-config"],
            "hostname arista1\nlab lab-a\nserial a&b<c>\n"
            "GigabitEthernet0/1=eth1\nGigabitEthernet0/10=eth10\nGigabitEthernet0/2=eth2\n",
        )
        self.assertEqual(warnings, [])

    def test_jinja2_without_template_warns_and_gives_no_config(self):
        self.set_arista_config()
        nodes, warnings = self.nodes_and_warnings([self.arista1, self.leaf1], NodeMappingConfigSourceChoices.JINJA2)
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertNotIn("startup-config", nodes["leaf1"])
        self.assertEqual(
            warnings,
            [
                W_NO_TEMPLATE.format(device="arista1", mapping=self.arista_mapping),
                W_NO_TEMPLATE.format(device="leaf1", mapping="Default mapping"),
            ],
        )

    def test_template_error_warns_and_build_continues(self):
        NodeMapping.objects.filter(pk=self.nokia_mapping.pk).update(
            config_source=NodeMappingConfigSourceChoices.JINJA2, config_template="hostname {{ device.name }}"
        )
        for template, error in (
            ("{{ missing.name }}", "'missing' is undefined"),
            ("{% if %}", "Expected an expression, got 'end of statement block'"),
            ("{{ device.name }}", "a template error of any type"),
        ):
            with self.subTest(template=template):
                self.set_arista_config(template)
                any_error = ValueError(error) if template == "{{ device.name }}" else None
                with mock.patch.object(config_sources, "render_jinja2", side_effect=self.raise_for_arista(any_error)):
                    nodes, warnings = self.nodes_and_warnings([self.arista1, self.nokia1])
                self.assertNotIn("startup-config", nodes["arista1"])
                self.assertEqual(nodes["nokia1"]["startup-config"], "hostname nokia1\n")
                self.assertEqual(
                    warnings, [W_TEMPLATE_ERROR.format(device="arista1", mapping=self.arista_mapping, error=error)]
                )

    @staticmethod
    def raise_for_arista(error):
        """Return a render_jinja2 stand-in that raises the error for the arista1 template, else renders."""

        def render(template_code, context):
            if error is not None and context["device"].name == "arista1":
                raise error
            return render_jinja2(template_code, context)

        return render

    def test_soft_time_limit_stops_the_build(self):
        self.set_arista_config("hostname {{ device.name }}")
        with mock.patch.object(config_sources, "render_jinja2", side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                build_topology(new_lab(), [self.arista1])

    def test_empty_template_output_warns(self):
        self.set_arista_config("{% if false %}x{% endif %} \n\t")
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(warnings, [W_EMPTY_CONFIG.format(device="arista1", source="Jinja2 template")])

    def test_unknown_source_warns(self):
        self.set_arista_config("hostname {{ device.name }}", "golden")
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(warnings, [W_UNKNOWN_SOURCE.format(device="arista1", source="golden")])

    def test_template_database_error_rolls_back_its_savepoint(self):
        self.set_arista_config("hostname {{ device.name }}")

        def broken_query(*_args):
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM containerlab_orchestrator_no_such_table")

        with mock.patch.object(config_sources, "render_jinja2", side_effect=broken_query):
            nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("Device arista1: the config template of "))
        self.assertTrue(Device.objects.filter(pk=self.arista1.pk).exists())
        with mock.patch.object(config_sources, "render_jinja2", side_effect=DatabaseError("lost")):
            _, warnings = self.nodes_and_warnings([self.arista1])
        self.assertEqual(
            warnings, [W_TEMPLATE_ERROR.format(device="arista1", mapping=self.arista_mapping, error="lost")]
        )


class StartupConfigBuildTestCase(StartupConfigTestCase):  # pylint: disable=too-many-ancestors
    """Test the startup config keys that build_topology writes on the nodes."""

    def test_build_escapes_jinja2_output_and_keeps_it(self):
        self.set_arista_config("enable secret 9 $9$abc\nbanner {{ '{{' }} x")
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        self.assertEqual(
            built["topology"]["nodes"]["arista1"]["startup-config"],
            f"enable secret 9 $$$$9$$$$abc\nbanner {TWO_LAYERS} x\n",
        )
        self.assertEqual(warnings, [])

    def test_startup_config_node_has_enforce_key(self):
        self.set_arista_config("hostname {{ device.name }}")
        built, _, _ = build_topology(new_lab(), [self.arista1, self.leaf1])
        nodes = built["topology"]["nodes"]
        self.assertIs(nodes["arista1"]["enforce-startup-config"], True)
        self.assertNotIn("enforce-startup-config", nodes["leaf1"])

    def test_interface_map_holds_table_rows_and_builder_names(self):
        self.set_arista_config(
            "{% for name, container in interface_map|dictsort %}{{ name }}={{ container }}\n{% endfor %}"
        )
        wan1 = self.add_device("wan1")
        self.connect(
            self.add_interfaces(self.arista1, "Ethernet5")[0], self.add_interfaces(self.arista2, "Ethernet7")[0]
        )
        self.connect(self.add_interfaces(self.arista1, "Ethernet6")[0], self.add_interfaces(wan1, "Ethernet1")[0])
        self.add_permissions("dcim.view_device", constraints={"name__in": ["arista1", "arista2"]})
        with mock.patch.object(topology, "map_interfaces", wraps=topology.map_interfaces) as mapper:
            built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2], self.user)
        self.assertEqual(mapper.call_count, 2)
        nodes = built["topology"]["nodes"]
        table = "GigabitEthernet0/1=eth1\nGigabitEthernet0/10=eth10\nGigabitEthernet0/2=eth2\n"
        self.assertEqual(nodes["arista1"]["startup-config"], f"Ethernet5=eth3\nEthernet6=eth4\n{table}")
        self.assertEqual(nodes["arista2"]["startup-config"], f"Ethernet7=eth3\n{table}")

    def test_overrides_cannot_set_startup_config(self):
        self.set_arista_config("hostname {{ device.name }}")
        self.set_arista_overrides({"startup-config": "/etc/passwd", "enforce-startup-config": False})
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        node = built["topology"]["nodes"]["arista1"]
        self.assertEqual((node["startup-config"], node["enforce-startup-config"]), ("hostname arista1\n", True))
        self.assertEqual(
            warnings,
            [W_OVERRIDE.format(mapping=self.arista_mapping, keys="enforce-startup-config, startup-config")],
        )

    def test_stub_nodes_get_no_startup_config(self):
        self.set_arista_config("hostname {{ device.name }}")
        wan1 = self.add_device("wan1")
        self.connect(self.add_interfaces(self.arista1, "Ethernet5")[0], self.add_interfaces(wan1, "Ethernet1")[0])
        built, _, _ = build_topology(lab_with_source(NodeMappingConfigSourceChoices.JINJA2), [self.arista1])
        nodes = built["topology"]["nodes"]
        self.assertEqual(nodes["arista1"]["startup-config"], "hostname arista1\n")
        self.assertEqual(set(nodes["stub-wan1"]), {"kind", "image", "mgmt-ipv4"})

    def test_topology_with_startup_config_passes_schema(self):
        self.use_full_kind_names()
        self.set_arista_config("hostname {{ device.name }}\nusername admin secret $9$x {{ '{{' }}\n")
        built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2, self.leaf1])
        self.assertIn("startup-config", built["topology"]["nodes"]["arista2"])
        self.assertEqual([error.message for error in load_clab_schema().iter_errors(built)], [])

    def test_to_yaml_writes_block_style_and_round_trips(self):
        self.set_arista_config("hostname {{ device.name }}\nbanner x\n")
        built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2])
        nodes = built["topology"]["nodes"]
        nodes["arista2"]["startup-config"] = "trailing space \ncrlf line\r\nnext\r\n"
        text = to_yaml(built)
        self.assertIn("      startup-config: |\n        hostname arista1\n        banner x\n", text)
        self.assertIn('startup-config: "trailing space', text)
        self.assertIn("name: lab-a\n", text)
        self.assertEqual(yaml.safe_load(text), built)
        self.assertNotIn("|", yaml.safe_dump({"a": "x\ny\n"}))


class GoldenConfigSourceTestCase(StartupConfigTestCase):  # pylint: disable=too-many-ancestors
    """Test the Golden Config intended and backup sources of the startup config."""

    @staticmethod
    def add_golden_config(device, intended="", backup=""):
        """Return a saved Golden Config record of the device with the intended and backup texts."""
        golden_config = apps.get_model(GOLDEN_CONFIG_APP, "GoldenConfig")
        return golden_config.objects.create(device=device, intended_config=intended, backup_config=backup)

    def arista_config(self, source, user=None):
        """Return the arista1 node and the warnings of a build with the Golden Config source on the Arista mapping."""
        self.set_arista_config(source=source)
        built, warnings, _ = build_topology(new_lab(), [self.arista1], user)
        return built["topology"]["nodes"]["arista1"], warnings

    def test_rename_keeps_fallback_names_with_warning(self):
        self.add_golden_config(self.arista1, intended="interface Ethernet5\ninterface GigabitEthernet0/1\n")
        self.connect(self.add_interfaces(self.arista1, "Ethernet5")[0], self.add_interfaces(self.leaf1, "eth9")[0])
        node, warnings = self.arista_config(INTENDED)
        self.assertEqual(node["startup-config"], "interface Ethernet5\ninterface eth1\n")
        self.assertIn(W_RENAME_FALLBACK.format(device="arista1", interface="Ethernet5"), warnings)

    def test_intended_source_reads_and_renames(self):
        self.add_golden_config(self.arista1, intended=GOLDEN_CONFIG_TEXT, backup="hostname backup\n")
        node, warnings = self.arista_config(INTENDED)
        self.assertEqual((node["startup-config"], node["enforce-startup-config"]), (RENAMED_TEXT, True))
        self.assertEqual(warnings, [])

    def test_backup_source_reads_and_renames(self):
        self.add_golden_config(
            self.arista1, intended="hostname intended\n", backup=f"secret $9$x\n{GOLDEN_CONFIG_TEXT}"
        )
        node, warnings = self.arista_config(BACKUP)
        self.assertEqual(node["startup-config"], f"secret $$$$9$$$$x\n{RENAMED_TEXT}")
        self.assertEqual(warnings, [])

    def test_rename_off_keeps_the_physical_names_without_rename_warning(self):
        self.add_golden_config(self.arista1, backup=f"{GOLDEN_CONFIG_TEXT}interface Ethernet5\n")
        self.connect(self.add_interfaces(self.arista1, "Ethernet5")[0], self.add_interfaces(self.leaf1, "eth9")[0])
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(rename_interfaces=False)
        node, warnings = self.arista_config(BACKUP)
        self.assertEqual(node["startup-config"], f"{GOLDEN_CONFIG_TEXT}interface Ethernet5\n")
        self.assertNotIn(W_RENAME_FALLBACK.format(device="arista1", interface="Ethernet5"), warnings)

    def test_no_record_warns_and_gives_no_config(self):
        for source in (INTENDED, BACKUP):
            with self.subTest(source=source):
                node, warnings = self.arista_config(source)
                self.assertNotIn("startup-config", node)
                self.assertEqual(warnings, [W_NO_GOLDEN_CONFIG.format(device="arista1")])

    def test_hidden_record_warns_like_no_record(self):
        self.add_golden_config(self.arista1, intended=GOLDEN_CONFIG_TEXT)
        self.add_permissions("dcim.view_device")
        node, warnings = self.arista_config(INTENDED, self.user)
        self.assertNotIn("startup-config", node)
        self.assertEqual(warnings, [W_NO_GOLDEN_CONFIG.format(device="arista1")])
        self.add_permissions("nautobot_golden_config.view_goldenconfig")
        node, _ = self.arista_config(INTENDED, get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(node["startup-config"], RENAMED_TEXT)

    def test_empty_golden_config_text_warns(self):
        record = self.add_golden_config(self.arista1)
        for text in ("", "  \n"):
            record.intended_config = text
            record.save()
            with self.subTest(text=text):
                node, warnings = self.arista_config(INTENDED)
                self.assertNotIn("startup-config", node)
                self.assertEqual(warnings, [W_EMPTY_CONFIG.format(device="arista1", source="Golden Config intended")])

    def test_golden_config_installed_reads_the_app_registry(self):
        self.assertTrue(config_sources.golden_config_installed())
        with mock.patch.dict(apps.app_configs):
            del apps.app_configs[GOLDEN_CONFIG_APP]
            self.assertFalse(config_sources.golden_config_installed())

    def test_not_installed_build_warns(self):
        self.add_golden_config(self.arista1, intended=GOLDEN_CONFIG_TEXT, backup=GOLDEN_CONFIG_TEXT)
        with mock.patch.object(config_sources, "golden_config_installed", return_value=False):
            for source, label in ((INTENDED, "Golden Config intended"), (BACKUP, "Golden Config backup")):
                with self.subTest(source=source):
                    node, warnings = self.arista_config(source)
                    self.assertNotIn("startup-config", node)
                    expected = W_GOLDEN_CONFIG_NOT_INSTALLED.format(device="arista1", source=label)
                    self.assertEqual(warnings, [expected])

    def test_no_module_imports_golden_config(self):
        modules = [path for path in APP_DIR.rglob("*.py") if "tests" not in path.relative_to(APP_DIR).parts]
        self.assertIn(APP_DIR / "config_sources.py", modules)
        for path in modules:
            imported = set()
            for statement in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(statement, ast.Import):
                    imported.update(alias.name for alias in statement.names)
                elif isinstance(statement, ast.ImportFrom):
                    imported.add(statement.module or "")
            with self.subTest(path=path.name):
                self.assertFalse({name for name in imported if name.split(".")[0] == GOLDEN_CONFIG_APP})
